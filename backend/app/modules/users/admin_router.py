"""Admin: find a user, see their history, suspend or reactivate them."""
import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_admin
from app.core.errors import AppError
from app.core.ratelimit import client_ip
from app.modules.audit import service as audit
from app.modules.audit.models import AuditLog
from app.modules.auth import service as auth_service
from app.modules.bookings import service as bookings
from app.modules.bookings.models import Booking
from app.modules.hosts import service as host_service
from app.modules.hosts.models import HostProfile
from app.modules.notifications.service import enqueue_email, kick_outbox
from app.modules.users.models import USER_ACTIVE, USER_SUSPENDED, User

router = APIRouter(prefix="/admin/users", tags=["admin users"])


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=500)


def _row(u: User, host: HostProfile | None, guest_bookings: int) -> dict:
    return {"id": str(u.id), "email": u.email, "full_name": u.full_name, "status": u.status, "is_admin": u.is_admin, "is_host": host is not None,
            "host_state": host_service.host_state(host) if host else None, "bookings": guest_bookings, "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None}


@router.get("")
async def list_users(q: str | None = Query(None, max_length=100), status: str | None = Query(None, pattern="^(unverified|active|suspended|deactivated)$"),
                     role: str | None = Query(None, pattern="^(admin|host|guest)$"), page: int = Query(1, ge=1, le=1000),
                     _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    query = select(User, HostProfile).outerjoin(HostProfile, HostProfile.user_id == User.id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(User.email.ilike(like), User.full_name.ilike(like), User.phone.ilike(like)))
    if status:
        query = query.where(User.status == status)
    if role == "admin":
        query = query.where(User.is_admin.is_(True))
    elif role == "host":
        query = query.where(HostProfile.id.is_not(None))
    elif role == "guest":
        query = query.where(User.is_admin.is_(False), HostProfile.id.is_(None))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await session.execute(query.order_by(User.created_at.desc()).offset((page - 1) * 25).limit(25))).all()
    counts = dict((await session.execute(select(Booking.guest_id, func.count()).where(Booking.guest_id.in_([u.id for u, _ in rows])).group_by(Booking.guest_id))).all()) if rows else {}
    return {"total": total, "page": page, "page_size": 25, "items": [_row(u, h, counts.get(u.id, 0)) for u, h in rows]}


async def _load(session: AsyncSession, user_id: str) -> User:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "User not found.") from None
    user = await session.get(User, uid)
    if user is None:
        raise AppError(404, "NOT_FOUND", "User not found.")
    return user


@router.get("/{user_id}")
async def user_detail(user_id: str, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    user = await _load(session, user_id)
    host = await host_service.get_host(session, user)
    recent = (await session.execute(select(Booking).where(Booking.guest_id == user.id).order_by(Booking.requested_at.desc()).limit(10))).scalars().all()
    history = (await session.execute(
        select(AuditLog, User.email).join(User, User.id == AuditLog.actor_id, isouter=True)
        .where(or_((AuditLog.entity_type == "user") & (AuditLog.entity_id == str(user.id)), AuditLog.actor_id == user.id)).order_by(AuditLog.id.desc()).limit(25))).all()
    total_bookings = (await session.execute(select(func.count()).select_from(Booking).where(Booking.guest_id == user.id))).scalar_one()
    return {
        **_row(user, host, total_bookings), "phone": user.phone, "dob": user.dob.isoformat() if user.dob else None, "email_verified": user.email_verified_at is not None,
        "social_links": [{"platform": s.platform, "url": s.url} for s in user.social_links], "host_id": str(host.id) if host else None,
        "recent_bookings": await bookings.serialize(session, list(recent), "admin"),
        "history": [{"at": a.created_at.isoformat(), "action": a.action, "entity_type": a.entity_type, "actor": email, "reason": a.reason} for a, email in history],
    }


@router.post("/{user_id}/suspend")
async def suspend(user_id: str, body: ReasonIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    reason = body.reason.strip()
    if not reason:
        raise AppError(422, "REASON_REQUIRED", "A reason is required. It is recorded and emailed to the user.")
    user = await _load(session, user_id)
    if user.id == admin.id:
        raise AppError(409, "CANNOT_SUSPEND_SELF", "You cannot suspend your own account.")
    if user.is_admin:
        raise AppError(409, "CANNOT_SUSPEND_ADMIN", "Administrators cannot be suspended here.")
    if user.status != USER_ACTIVE:
        raise AppError(409, "INVALID_STATE", "Only an active user can be suspended.")
    user.status = USER_SUSPENDED
    await auth_service.revoke_all_sessions(session, user.id)  # signed out everywhere, immediately
    audit.record(session, action="user.suspended", entity_type="user", entity_id=user.id, actor_id=admin.id, reason=reason, ip=client_ip(request))
    enqueue_email(session, to_email=user.email, template="account_suspended", context={"name": user.full_name, "note": reason}, user_id=user.id)
    await session.commit()
    kick_outbox()
    return await user_detail(user_id, admin, session)


@router.post("/{user_id}/reactivate")
async def reactivate(user_id: str, body: ReasonIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    user = await _load(session, user_id)
    if user.status != USER_SUSPENDED:
        raise AppError(409, "INVALID_STATE", "This user is not suspended.")
    user.status = USER_ACTIVE
    audit.record(session, action="user.reactivated", entity_type="user", entity_id=user.id, actor_id=admin.id, reason=body.reason.strip() or None, ip=client_ip(request))
    enqueue_email(session, to_email=user.email, template="account_reactivated", context={"name": user.full_name}, user_id=user.id)
    await session.commit()
    kick_outbox()
    return await user_detail(user_id, admin, session)
