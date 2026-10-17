import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cookies import clear_session_cookies
from app.core.db import get_session
from app.core.deps import get_current_user
from app.core.errors import AppError
from app.core.ratelimit import client_ip, rate_limit
from app.core.security import verify_password
from app.modules.audit import service as audit
from app.modules.auth import service as auth_service
from app.modules.legal import service as legal
from app.modules.legal.models import DOC_TYPES
from app.modules.notifications.service import enqueue_email, kick_outbox
from app.modules.users import service
from app.modules.users.models import USER_DEACTIVATED, User, UserSocialLink

router = APIRouter(prefix="/me", tags=["me"])


class ProfileIn(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = None
    dob: date | None = None


class SocialIn(BaseModel):
    url: str = Field(min_length=3, max_length=500)


class ConsentIn(BaseModel):
    doc_type: str


class EmailChangeIn(BaseModel):
    new_email: EmailStr
    password: str


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class PasswordIn(BaseModel):
    password: str


@router.get("")
async def get_me(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await service.serialize_user(session, user)


@router.patch("")
async def update_me(body: ProfileIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    if body.full_name is not None:
        user.full_name = body.full_name.strip()
    if body.phone is not None:
        user.phone = service.validate_phone(body.phone) if body.phone.strip() else None
    if body.dob is not None:
        user.dob = await service.validate_dob(session, body.dob)
    await session.commit()
    return await service.serialize_user(session, user)


@router.post("/social-links", status_code=201)
async def add_social_link(body: SocialIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    platform, url = await service.validate_social_url(session, body.url)
    if len(user.social_links) >= service.MAX_SOCIAL_LINKS:
        raise AppError(422, "TOO_MANY_LINKS", f"You can add up to {service.MAX_SOCIAL_LINKS} profile links")
    if any(link.url == url for link in user.social_links):
        raise AppError(409, "DUPLICATE_LINK", "That link is already on your profile")
    user.social_links.append(UserSocialLink(platform=platform, url=url))
    await session.commit()
    return await service.serialize_user(session, user)


@router.delete("/social-links/{link_id}")
async def delete_social_link(link_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    try:
        lid = uuid.UUID(link_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND") from None
    link = next((l for l in user.social_links if l.id == lid), None)
    if link is None:
        raise AppError(404, "NOT_FOUND")
    user.social_links.remove(link)
    await session.commit()
    return await service.serialize_user(session, user)


@router.post("/photo", dependencies=[Depends(rate_limit("photo", 20, 3600))])
async def upload_photo(file: UploadFile = File(...), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    data = await file.read(service.MAX_AVATAR_BYTES + 1)
    service.save_avatar(user, data)
    await session.commit()
    return await service.serialize_user(session, user)


@router.post("/consents")
async def accept_consent(body: ConsentIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    if body.doc_type not in DOC_TYPES:
        raise AppError(422, "UNKNOWN_DOCUMENT")
    await legal.record_consent(session, user.id, body.doc_type, client_ip(request))
    await session.commit()
    return await service.serialize_user(session, user)


@router.post("/email/change", status_code=202, dependencies=[Depends(rate_limit("email_change", 5, 3600))])
async def request_email_change(body: EmailChangeIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    if not verify_password(user.password_hash, body.password):
        raise AppError(400, "INVALID_CREDENTIALS", "Password is incorrect")
    new_email = auth_service.normalize_email(body.new_email)
    if new_email == user.email:
        raise AppError(422, "SAME_EMAIL", "That is already your email")
    if await auth_service.get_user_by_email(session, new_email):
        raise AppError(409, "EMAIL_IN_USE", "That email cannot be used")
    user.pending_email = new_email
    code = await auth_service.create_otp(session, user, "change_email")
    auth_service.send_otp_email(session, user, "change_email", code, to_email=new_email)
    await session.commit()
    kick_outbox()
    return {"message": "A confirmation code was sent to the new email address."}


@router.post("/email/confirm", dependencies=[Depends(rate_limit("email_confirm", 20, 600))])
async def confirm_email_change(body: CodeIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    if not user.pending_email:
        raise AppError(400, "NO_PENDING_CHANGE", "There is no email change in progress")
    await auth_service.verify_otp(session, user, "change_email", body.code)
    if await auth_service.get_user_by_email(session, user.pending_email):
        raise AppError(409, "EMAIL_IN_USE", "That email cannot be used")
    old = user.email
    user.email = user.pending_email
    user.pending_email = None
    audit.record(session, action="user.email_changed", entity_type="user", entity_id=user.id, actor_id=user.id,
                 before={"email": old}, after={"email": user.email}, ip=client_ip(request))
    await session.commit()
    return await service.serialize_user(session, user)


@router.post("/deactivate")
async def deactivate(body: PasswordIn, request: Request, response: Response, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    if user.is_admin:
        raise AppError(403, "FORBIDDEN", "Administrator accounts cannot be self-deactivated")
    if not verify_password(user.password_hash, body.password):
        raise AppError(400, "INVALID_CREDENTIALS", "Password is incorrect")
    # Phase 4+: block when the user has upcoming bookings or open money movements.
    user.status = USER_DEACTIVATED
    await auth_service.revoke_all_sessions(session, user.id)
    audit.record(session, action="user.deactivated", entity_type="user", entity_id=user.id, actor_id=user.id, ip=client_ip(request))
    enqueue_email(session, to_email=user.email, template="account_deactivated", context={"name": user.full_name}, user_id=user.id)
    await session.commit()
    kick_outbox()
    clear_session_cookies(response)
    return {"ok": True}
