"""Issue reports: a guest or host reports a problem with a booking; an admin works it through to a resolution."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.audit import service as audit
from app.modules.bookings import service as bookings
from app.modules.bookings.models import B_CANCELLED, B_CHECKED_IN, B_COMPLETED, B_CONFIRMED, Booking
from app.modules.hosts.models import HostProfile
from app.modules.issues.models import CATEGORIES, IS_DISMISSED, IS_IN_PROGRESS, IS_OPEN, IS_RESOLVED, OPEN_STATES, Issue
from app.modules.properties.models import Property
from app.modules.users.models import User

MAX_BODY = 3000
# A problem can be reported once there is a paid booking (including after it ended or was cancelled), not on a mere request.
REPORTABLE = (B_CONFIRMED, B_CHECKED_IN, B_COMPLETED, B_CANCELLED)


def _now() -> datetime:
    return datetime.now(UTC)


def reporter_json(i: Issue, b: Booking | None = None, prop: Property | None = None) -> dict:
    """What the person who reported it sees. The admin's internal note is never included."""
    return {"id": str(i.id), "booking_id": str(i.booking_id), "reference": b.reference if b else None, "property": prop.name if prop else None, "role": i.reporter_role,
            "category": i.category, "body": i.body, "status": i.status, "resolution": i.resolution, "created_at": i.created_at.isoformat(),
            "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None}


async def report(session: AsyncSession, user: User, booking_id: str, role: str, category: str, body: str, ip: str | None) -> Issue:
    body = (body or "").strip()
    if category not in CATEGORIES[role]:
        raise AppError(422, "INVALID_CATEGORY", "Choose one of the listed problem types.")
    if len(body) < 10:
        raise AppError(422, "DESCRIPTION_TOO_SHORT", "Please describe the problem in a sentence or two.")
    if len(body) > MAX_BODY:
        raise AppError(422, "DESCRIPTION_TOO_LONG", f"Keep it under {MAX_BODY} characters.")
    try:
        bid = uuid.UUID(booking_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Booking not found.") from None
    b = await session.get(Booking, bid)
    host = await session.get(HostProfile, b.host_id) if b else None
    party = b is not None and ((role == "guest" and b.guest_id == user.id) or (role == "host" and host.user_id == user.id))
    if not party:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    if b.status not in REPORTABLE:
        raise AppError(409, "INVALID_STATE", "You can report a problem once a booking is confirmed.")
    issue = Issue(booking_id=b.id, reporter_id=user.id, reporter_role=role, category=category, body=body)
    session.add(issue)
    await session.flush()
    audit.record(session, action="issue.reported", entity_type="booking", entity_id=b.id, actor_id=user.id, ip=ip, after={"category": category, "role": role})
    prop = await session.get(Property, b.property_id)
    await bookings._notify(session, b, "guest" if role == "guest" else "host", "issue_received_reporter", category=category.replace("_", " "), issue_body=body)
    await bookings.notify_admins(session, "issue_reported_admin", {"reference": b.reference, "property": prop.name, "reporter": user.full_name, "role": role,
                                                                  "category": category.replace("_", " "), "issue_body": body})
    return issue


async def mine(session: AsyncSession, user: User) -> list[dict]:
    rows = (await session.execute(
        select(Issue, Booking, Property).join(Booking, Booking.id == Issue.booking_id).join(Property, Property.id == Booking.property_id)
        .where(Issue.reporter_id == user.id).order_by(Issue.created_at.desc()).limit(100))).all()
    return [reporter_json(i, b, p) for i, b, p in rows]


# ---------- admin ----------

async def admin_json(session: AsyncSession, i: Issue) -> dict:
    b = await session.get(Booking, i.booking_id)
    prop = await session.get(Property, b.property_id)
    reporter = await session.get(User, i.reporter_id)
    host = await session.get(HostProfile, b.host_id)
    host_user = await session.get(User, host.user_id)
    guest = await session.get(User, b.guest_id)
    return {**reporter_json(i, b, prop), "admin_note": i.admin_note, "blocks_payout": i.blocks_payout, "updated_at": i.updated_at.isoformat(),
            "reporter": {"name": reporter.full_name, "email": reporter.email}, "guest": {"name": guest.full_name, "email": guest.email},
            "host": {"name": host_user.full_name, "email": host_user.email}, "booking_status": b.status}


async def admin_list(session: AsyncSession, *, status: str | None, q: str | None, page: int, page_size: int = 25) -> dict:
    query = select(Issue).join(Booking, Booking.id == Issue.booking_id)
    if status == "open_work":
        query = query.where(Issue.status.in_(OPEN_STATES))
    elif status:
        query = query.where(Issue.status == status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        reporters = select(User.id).where(or_(User.email.ilike(like), User.full_name.ilike(like)))
        query = query.where(or_(Booking.reference.ilike(like), Issue.body.ilike(like), Issue.reporter_id.in_(reporters)))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await session.execute(query.order_by(Issue.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"total": total, "page": page, "page_size": page_size, "items": [await admin_json(session, i) for i in rows]}


async def _load(session: AsyncSession, issue_id: str) -> Issue:
    try:
        iid = uuid.UUID(issue_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Issue not found.") from None
    i = (await session.execute(select(Issue).where(Issue.id == iid).with_for_update())).scalar_one_or_none()
    if i is None:
        raise AppError(404, "NOT_FOUND", "Issue not found.")
    return i


async def update(session: AsyncSession, admin: User, issue_id: str, *, status: str | None, admin_note: str | None, resolution: str | None,
                 blocks_payout: bool | None, ip: str | None) -> Issue:
    i = await _load(session, issue_id)
    before = {"status": i.status, "blocks_payout": i.blocks_payout}
    closed = i.status not in OPEN_STATES
    if closed and (status is not None or blocks_payout):
        raise AppError(409, "INVALID_STATE", "This issue is closed.")
    if admin_note is not None:
        i.admin_note = admin_note.strip() or None
    if blocks_payout is not None:
        i.blocks_payout = blocks_payout
    if status is not None and status != i.status:
        if status not in (IS_IN_PROGRESS, IS_RESOLVED, IS_DISMISSED) or (status == IS_IN_PROGRESS and i.status != IS_OPEN):
            raise AppError(422, "INVALID_STATUS", "An issue goes from open to in progress, then to resolved or dismissed.")
        if status in (IS_RESOLVED, IS_DISMISSED):
            if not (resolution or "").strip():
                raise AppError(422, "RESOLUTION_REQUIRED", "Write the message the person who reported it will receive.")
            i.resolution, i.resolved_by, i.resolved_at = resolution.strip(), admin.id, _now()
            i.blocks_payout = False  # a closed issue never holds a payout
        i.status = status
        b = await session.get(Booking, i.booking_id)
        await bookings._notify(session, b, i.reporter_role, "issue_updated_reporter", issue_status=status.replace("_", " "), resolution=i.resolution or "", category=i.category.replace("_", " "))
    audit.record(session, action="issue.updated", entity_type="issue", entity_id=i.id, actor_id=admin.id, ip=ip, before=before,
                 after={"status": i.status, "blocks_payout": i.blocks_payout}, reason=(admin_note or "").strip() or None)
    await session.flush()
    await session.refresh(i)  # updated_at is set by the database; load it before the caller reads it
    return i


async def blocks_payout(session: AsyncSession, booking_id: uuid.UUID) -> bool:
    """True while an admin has flagged an open issue on this booking as holding the host's payout."""
    row = (await session.execute(select(Issue.id).where(Issue.booking_id == booking_id, Issue.blocks_payout.is_(True), Issue.status.in_(OPEN_STATES)).limit(1))).first()
    return row is not None
