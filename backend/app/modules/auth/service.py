import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.ratelimit import client_ip
from app.core.security import (
    codes_match, create_access_token, generate_otp, generate_token, hash_code, hash_token,
)
from app.modules.auth.models import OtpCode, RefreshToken
from app.modules.notifications.service import enqueue_email
from app.modules.users.models import USER_ACTIVE, USER_DEACTIVATED, USER_SUSPENDED, User

OTP_TEMPLATES = {"verify_email": "verify_email", "reset_password": "reset_password", "change_email": "change_email"}


@dataclass
class SessionTokens:
    access: str
    refresh: str
    csrf: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return (await session.execute(select(User).where(User.email == normalize_email(email)))).scalar_one_or_none()


# ---------- one-time codes ----------

async def create_otp(session: AsyncSession, user: User, purpose: str) -> str:
    now = datetime.now(UTC)
    await session.execute(
        update(OtpCode)
        .where(OtpCode.user_id == user.id, OtpCode.purpose == purpose, OtpCode.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    code = generate_otp()
    session.add(
        OtpCode(
            user_id=user.id, purpose=purpose, code_hash=hash_code(code),
            expires_at=now + timedelta(minutes=get_settings().otp_ttl_minutes),
        )
    )
    return code


def send_otp_email(session: AsyncSession, user: User, purpose: str, code: str, *, to_email: str | None = None) -> None:
    enqueue_email(
        session,
        to_email=to_email or user.email,
        template=OTP_TEMPLATES[purpose],
        context={"name": user.full_name, "code": code, "minutes": get_settings().otp_ttl_minutes},
        user_id=user.id,
    )


async def verify_otp(session: AsyncSession, user: User, purpose: str, code: str) -> None:
    """Consumes the code on success (caller commits). Wrong guesses are committed here so they count."""
    settings = get_settings()
    now = datetime.now(UTC)
    otp = (
        await session.execute(
            select(OtpCode)
            .where(
                OtpCode.user_id == user.id, OtpCode.purpose == purpose,
                OtpCode.consumed_at.is_(None), OtpCode.expires_at > now,
            )
            .order_by(OtpCode.created_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if otp is None:
        raise AppError(400, "INVALID_CODE", "This code is invalid or has expired. Request a new one.")
    otp.attempts += 1
    if otp.attempts > settings.otp_max_attempts:
        otp.consumed_at = now
        await session.commit()
        raise AppError(400, "CODE_LOCKED", "Too many wrong attempts. Request a new code.")
    if not codes_match(code.strip(), otp.code_hash):
        await session.commit()
        raise AppError(400, "INVALID_CODE", "Incorrect code.")
    otp.consumed_at = now


# ---------- sessions ----------

async def start_session(session: AsyncSession, user: User, request: Request) -> SessionTokens:
    settings = get_settings()
    now = datetime.now(UTC)
    refresh = generate_token()
    session.add(
        RefreshToken(
            user_id=user.id, token_hash=hash_token(refresh), family_id=uuid.uuid4(),
            user_agent=(request.headers.get("user-agent") or "")[:300], ip=client_ip(request),
            expires_at=now + timedelta(days=settings.refresh_token_days),
        )
    )
    user.last_login_at = now
    user.failed_login_count = 0
    user.locked_until = None
    return SessionTokens(access=create_access_token(str(user.id)), refresh=refresh, csrf=generate_token()[:32])


async def rotate_refresh(session: AsyncSession, refresh_token: str, request: Request) -> tuple[User, SessionTokens]:
    settings = get_settings()
    now = datetime.now(UTC)
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token)).with_for_update())
    ).scalar_one_or_none()
    if row is None:
        raise AppError(401, "NOT_AUTHENTICATED", "Session expired")
    if row.revoked_at is not None:
        # A rotated token was presented again: assume theft and kill the whole family.
        await session.execute(
            update(RefreshToken).where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now)
        )
        await session.commit()
        raise AppError(401, "NOT_AUTHENTICATED", "Session expired")
    if row.expires_at <= now:
        raise AppError(401, "NOT_AUTHENTICATED", "Session expired")
    user = await session.get(User, row.user_id)
    if user is None or user.status in (USER_SUSPENDED, USER_DEACTIVATED) or user.status != USER_ACTIVE:
        raise AppError(401, "NOT_AUTHENTICATED", "Session expired")
    row.revoked_at = now
    new_refresh = generate_token()
    session.add(
        RefreshToken(
            user_id=user.id, token_hash=hash_token(new_refresh), family_id=row.family_id,
            user_agent=(request.headers.get("user-agent") or "")[:300], ip=client_ip(request),
            expires_at=now + timedelta(days=settings.refresh_token_days),
        )
    )
    return user, SessionTokens(access=create_access_token(str(user.id)), refresh=new_refresh, csrf=generate_token()[:32])


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID, *, except_hash: str | None = None) -> None:
    stmt = update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    if except_hash:
        stmt = stmt.where(RefreshToken.token_hash != except_hash)
    await session.execute(stmt.values(revoked_at=datetime.now(UTC)))
