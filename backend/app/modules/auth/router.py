from datetime import UTC, datetime, timedelta

import pyotp
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.cookies import clear_session_cookies, deliver_session, is_mobile, refresh_token_from
from app.core.db import get_session
from app.core.deps import get_current_user, require_admin
from app.core.errors import AppError
from app.core.ratelimit import client_ip, cooldown, rate_limit
from app.core.security import hash_password, hash_token, password_problem, verify_password
from app.core.turnstile import verify_turnstile
from app.modules.audit import service as audit
from app.modules.auth import service
from app.modules.auth.models import RefreshToken
from app.modules.legal import service as legal
from app.modules.notifications.service import enqueue_email, kick_outbox
from app.modules.users.models import USER_ACTIVE, USER_DEACTIVATED, USER_SUSPENDED, USER_UNVERIFIED, User
from app.modules.users.service import serialize_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _check_password_strength(password: str, email: str) -> None:
    problem = password_problem(password, email, get_settings().password_min_length)
    if problem:
        raise AppError(422, "WEAK_PASSWORD", problem)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    full_name: str = Field(min_length=1, max_length=200)
    accept_terms: bool
    turnstile_token: str | None = None


class VerifyIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class EmailIn(BaseModel):
    email: EmailStr
    turnstile_token: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None
    turnstile_token: str | None = None


class ResetIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    new_password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class TotpCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class TotpDisableIn(BaseModel):
    password: str
    code: str = Field(min_length=6, max_length=6)


GENERIC_SENT = {"message": "If the details are valid, a code has been sent to your email."}


@router.post("/register", status_code=202, dependencies=[Depends(rate_limit("register", 5, 3600))])
async def register(body: RegisterIn, request: Request, session: AsyncSession = Depends(get_session)):
    ip = client_ip(request)
    await verify_turnstile(body.turnstile_token, ip, mobile=is_mobile(request))
    if not body.accept_terms:
        raise AppError(422, "TERMS_REQUIRED", "You must accept the Terms and Privacy Policy")
    email = service.normalize_email(body.email)
    _check_password_strength(body.password, email)

    user = await service.get_user_by_email(session, email)
    if user is not None and user.status != USER_UNVERIFIED:
        return GENERIC_SENT  # do not reveal that the email is already registered
    if user is None:
        user = User(email=email, password_hash=hash_password(body.password), full_name=body.full_name.strip())
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            return GENERIC_SENT
    else:  # unverified: latest registrant who can read the mailbox wins
        user.password_hash = hash_password(body.password)
        user.full_name = body.full_name.strip()
    for doc_type in ("terms", "privacy"):
        await legal.record_consent(session, user.id, doc_type, ip)
    code = await service.create_otp(session, user, "verify_email")
    service.send_otp_email(session, user, "verify_email", code)
    await session.commit()
    kick_outbox()
    return GENERIC_SENT


@router.post("/verify-email", dependencies=[Depends(rate_limit("verify", 20, 600))])
async def verify_email(body: VerifyIn, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    user = await service.get_user_by_email(session, body.email)
    if user is None or user.status != USER_UNVERIFIED:
        raise AppError(400, "INVALID_CODE", "This code is invalid or has expired. Request a new one.")
    await service.verify_otp(session, user, "verify_email", body.code)
    user.email_verified_at = datetime.now(UTC)
    user.status = USER_ACTIVE
    tokens = await service.start_session(session, user, request)
    await session.commit()
    issued = deliver_session(request, response, tokens.access, tokens.refresh, tokens.csrf)
    user_out = await serialize_user(session, user)
    return {**user_out, "tokens": issued} if issued else user_out


@router.post("/resend-otp", status_code=202, dependencies=[Depends(rate_limit("resend", 10, 3600))])
async def resend_otp(body: EmailIn, request: Request, session: AsyncSession = Depends(get_session)):
    await verify_turnstile(body.turnstile_token, client_ip(request), mobile=is_mobile(request))
    email = service.normalize_email(body.email)
    await cooldown(f"otp:{email}", get_settings().otp_resend_cooldown_seconds)
    user = await service.get_user_by_email(session, email)
    if user is not None and user.status == USER_UNVERIFIED:
        code = await service.create_otp(session, user, "verify_email")
        service.send_otp_email(session, user, "verify_email", code)
        await session.commit()
        kick_outbox()
    return GENERIC_SENT


@router.post("/login", dependencies=[Depends(rate_limit("login", 20, 60))])
async def login(body: LoginIn, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    settings = get_settings()
    ip = client_ip(request)
    await verify_turnstile(body.turnstile_token, ip, mobile=is_mobile(request))
    now = datetime.now(UTC)
    user = await service.get_user_by_email(session, body.email)

    if user and user.locked_until and user.locked_until > now:
        raise AppError(429, "ACCOUNT_LOCKED", "Too many failed attempts. Try again in a few minutes.")

    password_ok = verify_password(user.password_hash if user else None, body.password)
    if not password_ok:
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.login_max_failures:
                user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
                user.failed_login_count = 0
            await session.commit()
        raise AppError(401, "INVALID_CREDENTIALS", "Incorrect email or password")
    assert user is not None

    if user.status == USER_DEACTIVATED:
        raise AppError(401, "INVALID_CREDENTIALS", "Incorrect email or password")
    if user.status == USER_UNVERIFIED:
        raise AppError(403, "EMAIL_NOT_VERIFIED", "Please verify your email first")
    if user.status == USER_SUSPENDED:
        raise AppError(403, "ACCOUNT_SUSPENDED", "Your account has been suspended")

    if user.totp_enabled:
        if not body.totp_code:
            raise AppError(401, "TOTP_REQUIRED", "Enter your authentication code")
        if not pyotp.TOTP(user.totp_secret).verify(body.totp_code, valid_window=1):
            user.failed_login_count += 1
            if user.failed_login_count >= settings.login_max_failures:
                user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
                user.failed_login_count = 0
            await session.commit()
            raise AppError(401, "INVALID_CODE", "Incorrect authentication code")

    tokens = await service.start_session(session, user, request)
    if user.is_admin:
        audit.record(session, action="admin.login", entity_type="user", entity_id=user.id, actor_id=user.id, ip=ip)
    await session.commit()
    issued = deliver_session(request, response, tokens.access, tokens.refresh, tokens.csrf)
    user_out = await serialize_user(session, user)
    return {**user_out, "tokens": issued} if issued else user_out


@router.post("/refresh")
async def refresh(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    token = refresh_token_from(request)
    if not token:
        raise AppError(401, "NOT_AUTHENTICATED", "Session expired")
    user, tokens = await service.rotate_refresh(session, token, request)
    await session.commit()
    issued = deliver_session(request, response, tokens.access, tokens.refresh, tokens.csrf)
    return {"ok": True, "tokens": issued} if issued else {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    token = refresh_token_from(request)
    if token:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == hash_token(token), RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()
    clear_session_cookies(response)
    return {"ok": True}


@router.post("/forgot-password", status_code=202, dependencies=[Depends(rate_limit("forgot", 5, 3600))])
async def forgot_password(body: EmailIn, request: Request, session: AsyncSession = Depends(get_session)):
    await verify_turnstile(body.turnstile_token, client_ip(request), mobile=is_mobile(request))
    email = service.normalize_email(body.email)
    await cooldown(f"reset:{email}", get_settings().otp_resend_cooldown_seconds)
    user = await service.get_user_by_email(session, email)
    if user is not None and user.status == USER_ACTIVE:
        code = await service.create_otp(session, user, "reset_password")
        service.send_otp_email(session, user, "reset_password", code)
        await session.commit()
        kick_outbox()
    return GENERIC_SENT


@router.post("/reset-password", dependencies=[Depends(rate_limit("reset", 20, 600))])
async def reset_password(body: ResetIn, session: AsyncSession = Depends(get_session)):
    user = await service.get_user_by_email(session, body.email)
    if user is None or user.status != USER_ACTIVE:
        raise AppError(400, "INVALID_CODE", "This code is invalid or has expired. Request a new one.")
    _check_password_strength(body.new_password, user.email)
    await service.verify_otp(session, user, "reset_password", body.code)
    user.password_hash = hash_password(body.new_password)
    user.failed_login_count = 0
    user.locked_until = None
    await service.revoke_all_sessions(session, user.id)
    audit.record(session, action="user.password_reset", entity_type="user", entity_id=user.id, actor_id=user.id)
    enqueue_email(session, to_email=user.email, template="password_changed", context={"name": user.full_name}, user_id=user.id)
    await session.commit()
    kick_outbox()
    return {"ok": True}


@router.post("/change-password", dependencies=[Depends(rate_limit("chpw", 10, 600))])
async def change_password(
    body: ChangePasswordIn, request: Request, user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not verify_password(user.password_hash, body.current_password):
        raise AppError(400, "INVALID_CREDENTIALS", "Current password is incorrect")
    _check_password_strength(body.new_password, user.email)
    user.password_hash = hash_password(body.new_password)
    current = refresh_token_from(request)
    await service.revoke_all_sessions(session, user.id, except_hash=hash_token(current) if current else None)
    audit.record(session, action="user.password_changed", entity_type="user", entity_id=user.id, actor_id=user.id, ip=client_ip(request))
    enqueue_email(session, to_email=user.email, template="password_changed", context={"name": user.full_name}, user_id=user.id)
    await session.commit()
    kick_outbox()
    return {"ok": True}


@router.get("/sessions")
async def list_sessions(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    current = refresh_token_from(request)
    current_hash = hash_token(current) if current else None
    rows = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > datetime.now(UTC))
            .order_by(RefreshToken.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": str(r.id), "user_agent": r.user_agent, "ip": r.ip, "created_at": r.created_at,
            "current": r.token_hash == current_hash,
        }
        for r in rows
    ]


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    import uuid

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND") from None
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.id == sid, RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
    if result.rowcount == 0:
        raise AppError(404, "NOT_FOUND")
    return {"ok": True}


# ---------- admin two-factor (TOTP) ----------

@router.post("/2fa/setup")
async def totp_setup(user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if user.totp_enabled:
        raise AppError(409, "TOTP_ALREADY_ENABLED", "Two-factor authentication is already enabled")
    user.totp_secret = pyotp.random_base32()
    await session.commit()
    uri = pyotp.TOTP(user.totp_secret).provisioning_uri(name=user.email, issuer_name="NearBNB")
    return {"secret": user.totp_secret, "otpauth_uri": uri}


@router.post("/2fa/enable")
async def totp_enable(body: TotpCodeIn, request: Request, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if not user.totp_secret:
        raise AppError(400, "TOTP_NOT_SET_UP", "Start setup first")
    if not pyotp.TOTP(user.totp_secret).verify(body.code, valid_window=1):
        raise AppError(400, "INVALID_CODE", "Incorrect authentication code")
    user.totp_enabled = True
    audit.record(session, action="admin.2fa_enabled", entity_type="user", entity_id=user.id, actor_id=user.id, ip=client_ip(request))
    await session.commit()
    return {"ok": True}


@router.post("/2fa/disable")
async def totp_disable(body: TotpDisableIn, request: Request, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if not user.totp_enabled or not user.totp_secret:
        raise AppError(400, "TOTP_NOT_ENABLED", "Two-factor authentication is not enabled")
    if not verify_password(user.password_hash, body.password):
        raise AppError(400, "INVALID_CREDENTIALS", "Password is incorrect")
    if not pyotp.TOTP(user.totp_secret).verify(body.code, valid_window=1):
        raise AppError(400, "INVALID_CODE", "Incorrect authentication code")
    user.totp_enabled = False
    user.totp_secret = None
    audit.record(session, action="admin.2fa_disabled", entity_type="user", entity_id=user.id, actor_id=user.id, ip=client_ip(request))
    await session.commit()
    return {"ok": True}
