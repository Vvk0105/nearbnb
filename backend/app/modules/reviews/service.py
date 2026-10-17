"""Blind reviews of a stay.

  * Opens when the booking is completed and stays open for the review window (14 days by default).
  * One review per side per booking, rating 1 to 5 plus an optional comment. Reviews cannot be edited or replied to.
  * Neither review is visible until both are in, or the window has closed. Then they publish together.
  * Guest -> host: public on the property page and part of its rating.
    Host -> guest: private. Only hosts who receive a request from that guest, and admins, ever see it. The guest never does.
  * An admin can hide a review (with a reason) and restore it."""
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.audit import service as audit
from app.modules.bookings import service as bookings
from app.modules.bookings.models import B_COMPLETED, Booking
from app.modules.hosts.models import HostProfile
from app.modules.properties.models import Property
from app.modules.reviews.models import DIR_GUEST_TO_HOST, DIR_HOST_TO_GUEST, RV_HIDDEN, RV_PUBLISHED, RV_SUBMITTED, Review
from app.modules.settings import service as settings_service
from app.modules.users.models import User

log = logging.getLogger(__name__)
MAX_COMMENT = 2000


def _now() -> datetime:
    return datetime.now(UTC)


def deadline(b: Booking, window_days: int) -> datetime:
    """When reviewing closes. Stored when the stay completes; bookings completed before that was stored use today's window."""
    return b.review_closes_at or (b.check_out + timedelta(days=window_days))


def review_info(b: Booking, reviews: list[Review], viewer: str, now: datetime, window_days: int) -> dict | None:
    """What one side of a booking sees about reviews. The guest never sees the host's review of them."""
    if b.status != B_COMPLETED or viewer not in ("guest", "host"):
        return None
    mine_dir, theirs_dir = (DIR_GUEST_TO_HOST, DIR_HOST_TO_GUEST) if viewer == "guest" else (DIR_HOST_TO_GUEST, DIR_GUEST_TO_HOST)
    closes = deadline(b, window_days)
    mine = next((r for r in reviews if r.direction == mine_dir), None)
    theirs = next((r for r in reviews if r.direction == theirs_dir), None)
    info = {
        "closes_at": closes.isoformat(), "open": now <= closes, "can_review": mine is None and now <= closes,
        "mine": {"rating": mine.rating, "comment": mine.comment, "status": mine.status, "submitted_at": mine.submitted_at.isoformat()} if mine else None,
        "theirs": None,
    }
    # The host may read the guest's published review of their property (it is public anyway). A guest gets nothing here.
    if viewer == "host" and theirs is not None and theirs.status == RV_PUBLISHED:
        info["theirs"] = {"rating": theirs.rating, "comment": theirs.comment}
    return info


async def reviews_for(session: AsyncSession, booking_ids: set[uuid.UUID]) -> dict[uuid.UUID, list[Review]]:
    out: dict[uuid.UUID, list[Review]] = {}
    if booking_ids:
        for r in (await session.execute(select(Review).where(Review.booking_id.in_(booking_ids)))).scalars():
            out.setdefault(r.booking_id, []).append(r)
    return out


# ---------- writing ----------

async def submit(session: AsyncSession, user: User, booking_id: str, direction: str, rating: int, comment: str) -> Review:
    comment = (comment or "").strip()
    if not 1 <= rating <= 5:
        raise AppError(422, "INVALID_RATING", "Choose a rating from 1 to 5.")
    if len(comment) > MAX_COMMENT:
        raise AppError(422, "COMMENT_TOO_LONG", f"Keep the comment under {MAX_COMMENT} characters.")
    b = await bookings._locked(session, booking_id)
    host = await session.get(HostProfile, b.host_id)
    if direction == DIR_GUEST_TO_HOST:
        allowed, subject = b.guest_id == user.id, host.user_id
    else:
        allowed, subject = host.user_id == user.id, b.guest_id
    if not allowed:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    if b.status != B_COMPLETED:
        raise AppError(409, "NOT_COMPLETED", "You can review a stay once it is over.")
    now = _now()
    window = await settings_service.get_value(session, "review.window_days")
    if now > deadline(b, window):
        raise AppError(409, "WINDOW_CLOSED", "The time to review this stay has passed.")
    existing = (await session.execute(select(Review.id).where(Review.booking_id == b.id, Review.direction == direction))).first()
    if existing:
        raise AppError(409, "ALREADY_REVIEWED", "You have already reviewed this stay. Reviews cannot be changed.")
    review = Review(booking_id=b.id, property_id=b.property_id, direction=direction, author_id=user.id, subject_id=subject, rating=rating, comment=comment)
    session.add(review)
    await session.flush()
    audit.record(session, action="review.submitted", entity_type="booking", entity_id=b.id, actor_id=user.id, after={"direction": direction, "rating": rating})
    other = (await session.execute(select(Review).where(Review.booking_id == b.id, Review.direction != direction))).scalar_one_or_none()
    if other is not None:  # both sides are in: they publish together, right now
        await _publish(session, [review, other], now)
    return review


async def _publish(session: AsyncSession, reviews: list[Review], now: datetime) -> None:
    for r in reviews:
        if r.status == RV_SUBMITTED:
            r.status, r.published_at = RV_PUBLISHED, now
            audit.record(session, action="review.published", entity_type="review", entity_id=r.id)


# ---------- reading ----------

async def property_ratings(session: AsyncSession, property_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Average and count of published guest reviews per property (hidden reviews do not count)."""
    if not property_ids:
        return {}
    rows = (await session.execute(
        select(Review.property_id, func.avg(Review.rating), func.count()).where(
            Review.property_id.in_(property_ids), Review.direction == DIR_GUEST_TO_HOST, Review.status == RV_PUBLISHED).group_by(Review.property_id)
    )).all()
    return {pid: {"average": round(float(avg), 1), "count": n} for pid, avg, n in rows}


def _month(dt: datetime | None) -> str | None:
    return dt.strftime("%B %Y") if dt else None


async def public_reviews(session: AsyncSession, property_id: uuid.UUID, page: int, page_size: int = 10) -> dict:
    q = select(Review, User).join(User, User.id == Review.author_id).where(
        Review.property_id == property_id, Review.direction == DIR_GUEST_TO_HOST, Review.status == RV_PUBLISHED)
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await session.execute(q.order_by(Review.published_at.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    summary = (await property_ratings(session, [property_id])).get(property_id, {"average": None, "count": 0})
    return {"total": total, "page": page, "page_size": page_size, "average": summary["average"], "count": summary["count"], "items": [
        {"id": str(r.id), "rating": r.rating, "comment": r.comment, "author": (u.full_name.split() or ["Guest"])[0], "month": _month(r.published_at)} for r, u in rows]}


async def guest_summary(session: AsyncSession, guest_id: uuid.UUID) -> dict:
    """What a host sees about a guest who has asked to book: how other hosts rated them, and how many stays they have completed."""
    base = (Review.subject_id == guest_id, Review.direction == DIR_HOST_TO_GUEST, Review.status == RV_PUBLISHED)
    avg, count = (await session.execute(select(func.avg(Review.rating), func.count()).where(*base))).one()
    recent = (await session.execute(select(Review).where(*base).order_by(Review.published_at.desc()).limit(5))).scalars().all()
    stays = (await session.execute(select(func.count()).select_from(Booking).where(Booking.guest_id == guest_id, Booking.status == B_COMPLETED))).scalar_one()
    return {"count": count, "average": round(float(avg), 1) if avg is not None else None, "completed_stays": stays,
            "items": [{"rating": r.rating, "comment": r.comment, "month": _month(r.published_at)} for r in recent]}


# ---------- the job: invitations, reminders, publishing ----------

async def process(session: AsyncSession, now: datetime | None = None) -> dict:
    """Run every 15 minutes: invite both sides once a stay is over, remind whoever has not reviewed shortly before the
    window closes, and publish reviews whose window has closed."""
    now = now or _now()
    window = await settings_service.get_value(session, "review.window_days")
    remind_days = await settings_service.get_value(session, "review.reminder_days_before_close")
    counts = {"invited": 0, "reminded": 0, "published": 0}

    for b in (await session.execute(select(Booking).where(Booking.status == B_COMPLETED, Booking.review_invite_sent_at.is_(None), Booking.check_out <= now)
                                    .order_by(Booking.check_out).limit(200).with_for_update(skip_locked=True))).scalars():
        b.review_closes_at = b.review_closes_at or (b.check_out + timedelta(days=window))
        b.review_invite_sent_at = now
        extra = {"closes": b.review_closes_at.astimezone(bookings.ZoneInfo(b.timezone)).strftime("%d %b %Y")}
        await bookings._notify(session, b, "guest", "review_invite_guest", **extra)
        await bookings._notify(session, b, "host", "review_invite_host", **extra)
        counts["invited"] += 1

    for b in (await session.execute(select(Booking).where(Booking.status == B_COMPLETED, Booking.review_invite_sent_at.is_not(None), Booking.review_reminder_sent_at.is_(None))
                                    .limit(200).with_for_update(skip_locked=True))).scalars():
        closes = deadline(b, window)
        if closes <= now or now < closes - timedelta(days=remind_days):
            continue
        done = {r.direction for r in (await session.execute(select(Review).where(Review.booking_id == b.id))).scalars()}
        b.review_reminder_sent_at = now
        extra = {"closes": closes.astimezone(bookings.ZoneInfo(b.timezone)).strftime("%d %b %Y")}
        if DIR_GUEST_TO_HOST not in done:
            await bookings._notify(session, b, "guest", "review_reminder_guest", **extra)
        if DIR_HOST_TO_GUEST not in done:
            await bookings._notify(session, b, "host", "review_reminder_host", **extra)
        counts["reminded"] += 1

    waiting = (await session.execute(
        select(Review, Booking).join(Booking, Booking.id == Review.booking_id).where(Review.status == RV_SUBMITTED).limit(500))).all()
    by_booking: dict[uuid.UUID, list[Review]] = {}
    for r, b in waiting:
        if deadline(b, window) <= now:
            by_booking.setdefault(b.id, []).append(r)
    for rs in by_booking.values():
        await _publish(session, rs, now)
        counts["published"] += len(rs)
    return counts


# ---------- moderation ----------

def admin_json(r: Review, author: User, subject: User, prop: Property, reference: str) -> dict:
    return {"id": str(r.id), "reference": reference, "booking_id": str(r.booking_id), "property": prop.name, "direction": r.direction, "rating": r.rating, "comment": r.comment,
            "status": r.status, "author": author.full_name, "author_email": author.email, "subject": subject.full_name, "submitted_at": r.submitted_at.isoformat(),
            "published_at": r.published_at.isoformat() if r.published_at else None, "hidden_reason": r.hidden_reason}


async def admin_list(session: AsyncSession, *, status: str | None, direction: str | None, q: str | None, page: int, page_size: int = 25) -> dict:
    query = (select(Review, Booking).join(Booking, Booking.id == Review.booking_id))
    if status:
        query = query.where(Review.status == status)
    if direction:
        query = query.where(Review.direction == direction)
    if q and q.strip():
        like = f"%{q.strip()}%"
        authors = select(User.id).where(or_(User.email.ilike(like), User.full_name.ilike(like)))
        query = query.where(or_(Booking.reference.ilike(like), Review.comment.ilike(like), Review.author_id.in_(authors)))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await session.execute(query.order_by(Review.submitted_at.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    items = []
    for r, b in rows:
        items.append(admin_json(r, await session.get(User, r.author_id), await session.get(User, r.subject_id), await session.get(Property, r.property_id), b.reference))
    return {"total": total, "page": page, "page_size": page_size, "items": items}


async def _load(session: AsyncSession, review_id: str) -> Review:
    try:
        rid = uuid.UUID(review_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Review not found.") from None
    r = (await session.execute(select(Review).where(Review.id == rid).with_for_update())).scalar_one_or_none()
    if r is None:
        raise AppError(404, "NOT_FOUND", "Review not found.")
    return r


async def hide(session: AsyncSession, admin: User, review_id: str, reason: str, ip: str | None) -> Review:
    reason = (reason or "").strip()
    if not reason:
        raise AppError(422, "REASON_REQUIRED", "A reason is required. It is recorded in the audit log.")
    r = await _load(session, review_id)
    if r.status == RV_HIDDEN:
        raise AppError(409, "INVALID_STATE", "This review is already hidden.")
    before = r.status
    r.status, r.hidden_reason, r.hidden_by, r.hidden_at = RV_HIDDEN, reason, admin.id, _now()
    r.published_at = r.published_at  # kept: restoring puts it back as it was
    audit.record(session, action="review.hidden", entity_type="review", entity_id=r.id, actor_id=admin.id, reason=reason, ip=ip, before={"status": before})
    return r


async def restore(session: AsyncSession, admin: User, review_id: str, reason: str | None, ip: str | None) -> Review:
    r = await _load(session, review_id)
    if r.status != RV_HIDDEN:
        raise AppError(409, "INVALID_STATE", "This review is not hidden.")
    r.status = RV_PUBLISHED if r.published_at else RV_SUBMITTED  # a review hidden before it published goes back to waiting
    r.hidden_reason = r.hidden_by = r.hidden_at = None
    audit.record(session, action="review.restored", entity_type="review", entity_id=r.id, actor_id=admin.id, reason=(reason or "").strip() or None, ip=ip)
    return r
