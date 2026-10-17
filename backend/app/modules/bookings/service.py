"""Booking requests: create (with a room hold), accept, reject, withdraw, expire, remind.

A request holds ONE room for the exact period (a `hold` row in room_occupancy). The database exclusion
constraint is the final judge of double booking; everything here only decides who gets there first.
Expired holds are deleted by `expire_due`, which runs every minute and also before any new request, because
the constraint cannot tell that a hold has run out."""
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.locks import lock_unit
from app.modules.audit import service as audit
from app.modules.availability import service as availability
from app.modules.bookings.models import (
    B_ACCEPTED, B_CANCELLED, B_CHECKED_IN, B_COMPLETED, B_CONFIRMED, B_EXPIRED, B_PAYMENT_EXPIRED, B_PAYMENT_PENDING,
    B_REJECTED, B_REQUESTED, B_WITHDRAWN, CONFIRMED_STATES, HOLDING, Booking,
)
from app.modules.hosts import service as host_service
from app.modules.hosts.models import HostProfile
from app.modules.notifications.service import enqueue_email
from app.modules.pricing import service as pricing
from app.modules.properties.models import Media, Property, Room, RoomOccupancy, Unit
from app.modules.properties.service import PUBLIC_MEDIA
from app.modules.settings import service as settings_service
from app.modules.users.models import User

log = logging.getLogger(__name__)
REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I so a reference can be read out over the phone

GROUPS: dict[str, tuple[str, ...]] = {
    "pending": (B_REQUESTED,),
    "awaiting_payment": (B_ACCEPTED, B_PAYMENT_PENDING),
    "upcoming": (B_CONFIRMED, B_CHECKED_IN),
    "past": (B_COMPLETED, B_REJECTED, B_EXPIRED, B_WITHDRAWN, B_PAYMENT_EXPIRED, B_CANCELLED),
}
FIELD_LABELS = {"full_name": "your full name", "dob": "your date of birth", "phone": "a phone number", "social_url": "a social media profile link"}


def _now() -> datetime:
    return datetime.now(UTC)


def money(cents: int, currency: str) -> str:
    return f"${cents / 100:,.2f} {currency}" if currency != "AUD" else f"${cents / 100:,.2f}"


# ---------- guest readiness ----------

async def missing_profile_fields(session: AsyncSession, user: User) -> list[dict]:
    """Which of the admin-configured required fields the guest has not filled in yet."""
    required = await settings_service.get_value(session, "guest.required_fields")
    min_social = await settings_service.get_value(session, "guest.min_social_profiles")
    missing: list[str] = []
    if "full_name" in required and not (user.full_name or "").strip():
        missing.append("full_name")
    if "dob" in required and user.dob is None:
        missing.append("dob")
    if "phone" in required and not (user.phone or "").strip():
        missing.append("phone")
    if "social_url" in required and len(user.social_links) < max(1, min_social):
        missing.append("social_url")
    return [{"field": f, "label": FIELD_LABELS[f]} for f in missing]


async def readiness(session: AsyncSession, user: User) -> dict:
    missing = await missing_profile_fields(session, user)
    return {"email_verified": user.email_verified_at is not None, "missing": missing,
            "ready": user.email_verified_at is not None and not missing}


def _age(dob, today) -> int | None:
    if dob is None:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


# ---------- emails ----------

def _email(session: AsyncSession, user: User, template: str, context: dict) -> None:
    enqueue_email(session, to_email=user.email, template=template, user_id=user.id, context={"name": user.full_name, **context})


async def _context(session: AsyncSession, b: Booking) -> dict:
    prop = await session.get(Property, b.property_id)
    unit = await session.get(Unit, b.unit_id)
    tz = ZoneInfo(b.timezone)
    p = b.pricing_snapshot
    return {
        "reference": b.reference, "title": f"{prop.name} ({unit.name})", "guest_name": b.guest_snapshot.get("full_name", "A guest"),
        "checkin": availability.fmt_local(b.check_in, tz), "checkout": availability.fmt_local(b.check_out, tz), "tz": b.timezone,
        "guests": b.guests, "nights": p.get("nights"), "total": money(p["total"], b.currency),
        "host_payable": money(p["host_payable"], b.currency), "note": b.closed_reason or "",
        "response_minutes": b.policy_snapshot.get("response_minutes"), "payment_minutes": b.policy_snapshot.get("payment_minutes"),
        "expires": availability.fmt_local(b.expires_at, tz) if b.expires_at else "",
        "pay_by": availability.fmt_local(b.payment_deadline_at, tz) if b.payment_deadline_at else "",
        "url": get_settings().app_base_url.rstrip("/"),
    }


async def _notify(session: AsyncSession, b: Booking, who: str, template: str) -> None:
    guest = await session.get(User, b.guest_id)
    host = await session.get(HostProfile, b.host_id)
    host_user = await session.get(User, host.user_id)
    _email(session, guest if who == "guest" else host_user, template, await _context(session, b))


# ---------- expiry and reminders ----------

async def _release_hold(session: AsyncSession, booking_id: uuid.UUID) -> None:
    await session.execute(delete(RoomOccupancy).where(RoomOccupancy.booking_id == booking_id, RoomOccupancy.kind == "hold"))


async def expire_due(session: AsyncSession, now: datetime | None = None, limit: int = 200) -> int:
    """Closes requests the host did not answer and acceptances the guest did not pay, and frees their rooms.
    Safe to call at any time and from several places at once (rows another call is handling are skipped)."""
    now = now or _now()
    rows = (await session.execute(
        select(Booking).where(or_(
            and_(Booking.status == B_REQUESTED, Booking.expires_at <= now),
            and_(Booking.status.in_((B_ACCEPTED, B_PAYMENT_PENDING)), Booking.payment_deadline_at <= now),
        )).order_by(Booking.expires_at).limit(limit).with_for_update(skip_locked=True)
    )).scalars().all()
    for b in rows:
        if b.status == B_REQUESTED:
            b.status, b.closed_reason = B_EXPIRED, "The host did not respond in time."
            template_guest, template_host = "booking_expired_guest", "booking_expired_host"
        else:
            b.status, b.closed_reason = B_PAYMENT_EXPIRED, "Payment was not completed in time."
            template_guest, template_host = "booking_payment_expired_guest", "booking_payment_expired_host"
        b.cancelled_at = now
        await _release_hold(session, b.id)
        await _notify(session, b, "guest", template_guest)
        await _notify(session, b, "host", template_host)
        audit.record(session, action=f"booking.{b.status}", entity_type="booking", entity_id=b.id, after={"status": b.status})
    # Holds with no booking left to process them (should not happen, but a stale row would block the room for good).
    await session.execute(delete(RoomOccupancy).where(RoomOccupancy.kind == "hold", RoomOccupancy.expires_at <= now))
    return len(rows)


async def send_reminders(session: AsyncSession, now: datetime | None = None) -> int:
    """One reminder at the halfway point of the host's response window and of the guest's payment deadline."""
    now = now or _now()
    sent = 0
    for b in (await session.execute(select(Booking).where(Booking.status == B_REQUESTED, Booking.reminder_sent_at.is_(None), Booking.expires_at > now))).scalars():
        if now >= b.requested_at + (b.expires_at - b.requested_at) / 2:
            b.reminder_sent_at = now
            await _notify(session, b, "host", "booking_reminder_host")
            sent += 1
    for b in (await session.execute(select(Booking).where(Booking.status == B_ACCEPTED, Booking.payment_reminder_sent_at.is_(None), Booking.payment_deadline_at > now))).scalars():
        if now >= b.accepted_at + (b.payment_deadline_at - b.accepted_at) / 2:
            b.payment_reminder_sent_at = now
            await _notify(session, b, "guest", "booking_payment_reminder_guest")
            sent += 1
    return sent


# ---------- create ----------

def _is_overlap(exc: IntegrityError) -> bool:
    return "room_occupancy_no_overlap" in str(exc.orig) or getattr(exc.orig, "sqlstate", None) == "23P01"


def _reference() -> str:
    return "NB-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(8))


async def create_request(session: AsyncSession, guest: User, unit_id: uuid.UUID, checkin_local_str: str, hours: int, guests: int, ip: str | None) -> Booking:
    unit, prop, host = await availability.public_unit(session, unit_id)
    if host.user_id == guest.id:
        raise AppError(403, "OWN_LISTING", "You cannot book your own listing.")
    if guest.email_verified_at is None:
        raise AppError(403, "EMAIL_NOT_VERIFIED", "Verify your email address before booking.")
    missing = await missing_profile_fields(session, guest)
    if missing:
        raise AppError(422, "PROFILE_INCOMPLETE", "Complete your profile before booking. Still needed: " + ", ".join(m["label"] for m in missing) + ".")

    now = _now()
    await lock_unit(session, unit.id)
    await expire_due(session, now)
    limit = await settings_service.get_value(session, "booking.max_pending_requests")
    holding = (await session.execute(select(func.count()).select_from(Booking).where(Booking.guest_id == guest.id, Booking.status.in_(HOLDING)))).scalar_one()
    if holding >= limit:
        raise AppError(409, "TOO_MANY_PENDING", f"You can have {limit} open requests at a time. Wait for a reply or withdraw one first.")

    tz = ZoneInfo(prop.timezone)
    checkin_local = availability.parse_local(checkin_local_str)
    globals_ = await availability.load_global_rules(session)
    overrides = (await availability.overrides_for_hosts(session, {host.id})).get(host.id, {})
    rules = availability.rules_for(unit, globals_, overrides)
    violation = availability.check_stay(rules, unit.capacity, tz, checkin_local, hours, guests, now)
    if violation:
        raise AppError(422, violation.code, violation.message)

    start = availability.to_utc(checkin_local, tz)
    end = start + timedelta(hours=hours)
    response_minutes = overrides.get("booking.host_response_minutes") or await settings_service.get_value(session, "booking.host_response_minutes")
    payment_minutes = overrides.get("booking.payment_deadline_minutes") or await settings_service.get_value(session, "booking.payment_deadline_minutes")
    expires_at = min(now + timedelta(minutes=response_minutes), start)

    rooms = availability.active_rooms(unit)
    busy = await availability.busy_by_room(session, [r.id for r in rooms], start, end)
    booking_id = uuid.uuid4()
    chosen: Room | None = None
    for room in (r for r in rooms if not availability._overlaps(busy[r.id], start, end)):
        try:
            async with session.begin_nested():
                session.add(RoomOccupancy(room_id=room.id, period=Range(start, end, bounds="[)"), kind="hold", booking_id=booking_id,
                                          expires_at=expires_at, created_by=guest.id))
                await session.flush()
            chosen = room
            break
        except IntegrityError as exc:
            if not _is_overlap(exc):
                raise
    if chosen is None:
        quote = await availability.build_quote(session, unit, prop, host, checkin_local_str, hours, guests)
        raise AppError(409, "SLOT_UNAVAILABLE", quote.get("message") or "Those dates were just taken. Please choose another time.")

    breakdown = await availability.unit_price(session, unit, prop, overrides, hours)
    today = now.date()
    guest_snapshot = {
        "full_name": guest.full_name, "dob": guest.dob.isoformat() if guest.dob else None, "age": _age(guest.dob, today),
        "phone": guest.phone, "email": guest.email, "member_since": guest.created_at.isoformat() if guest.created_at else None,
        "social_links": [{"platform": s.platform, "url": s.url} for s in guest.social_links],
    }
    policy_snapshot = {
        "response_minutes": response_minutes, "payment_minutes": payment_minutes,
        "cancellation_template": prop.cancellation_template_key or await settings_service.get_value(session, "cancellation.default_template"),
        "payout_anchor": overrides.get("payout.anchor") or await settings_service.get_value(session, "payout.anchor"),
        "payout_delay_hours": overrides["payout.delay_hours"] if "payout.delay_hours" in overrides else await settings_service.get_value(session, "payout.delay_hours"),
        "service_fee_refundable": await settings_service.get_value(session, "finance.service_fee_refundable"),
        "overrides": sorted(overrides),
    }
    booking = Booking(
        id=booking_id, reference=_reference(), guest_id=guest.id, host_id=host.id, property_id=prop.id, unit_id=unit.id, room_id=chosen.id,
        check_in=start, check_out=end, hours=hours, guests=guests, timezone=prop.timezone, currency=unit.currency, status=B_REQUESTED,
        requested_at=now, expires_at=expires_at, guest_snapshot=guest_snapshot, pricing_snapshot=breakdown, policy_snapshot=policy_snapshot,
    )
    session.add(booking)
    await session.flush()
    audit.record(session, action="booking.requested", entity_type="booking", entity_id=booking.id, actor_id=guest.id, ip=ip,
                 after={"unit_id": str(unit.id), "check_in": start.isoformat(), "hours": hours, "guests": guests})
    await _notify(session, booking, "host", "booking_request_host")
    await _notify(session, booking, "guest", "booking_request_guest")
    return booking


# ---------- host and guest actions ----------

async def _locked(session: AsyncSession, booking_id: str) -> Booking:
    try:
        bid = uuid.UUID(booking_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Booking not found.") from None
    b = (await session.execute(select(Booking).where(Booking.id == bid).with_for_update())).scalar_one_or_none()
    if b is None:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    return b


def _not_open(b: Booking) -> AppError:
    said = {B_EXPIRED: "This request expired.", B_REJECTED: "This request was already declined.", B_WITHDRAWN: "The guest withdrew this request.",
            B_ACCEPTED: "This request was already accepted.", B_PAYMENT_EXPIRED: "This request expired before payment."}
    return AppError(409, "INVALID_STATE", said.get(b.status, f"This booking is {b.status.replace('_', ' ')}."))


async def accept(session: AsyncSession, host: HostProfile, actor: User, booking_id: str, ip: str | None) -> Booking:
    await expire_due(session)
    b = await _locked(session, booking_id)
    if b.host_id != host.id:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    if b.status != B_REQUESTED:
        raise _not_open(b)
    if not host_service.is_active(host):
        raise AppError(403, "HOST_NOT_ACTIVE", "Finish payout setup before accepting bookings.")
    now = _now()
    hold = (await session.execute(select(RoomOccupancy).where(RoomOccupancy.booking_id == b.id, RoomOccupancy.kind == "hold").with_for_update())).scalar_one_or_none()
    if hold is None or b.check_in <= now:  # expire_due above normally catches this; this is the last safety net
        raise AppError(409, "REQUEST_EXPIRED", "This request expired.")
    deadline = min(now + timedelta(minutes=b.policy_snapshot["payment_minutes"]), b.check_in)
    b.status, b.accepted_at, b.responded_at, b.payment_deadline_at = B_ACCEPTED, now, now, deadline
    hold.expires_at = deadline
    audit.record(session, action="booking.accepted", entity_type="booking", entity_id=b.id, actor_id=actor.id, ip=ip, after={"payment_deadline_at": deadline.isoformat()})
    await _notify(session, b, "guest", "booking_accepted_guest")
    return b


async def reject(session: AsyncSession, host: HostProfile, actor: User, booking_id: str, note: str | None, ip: str | None) -> Booking:
    await expire_due(session)
    b = await _locked(session, booking_id)
    if b.host_id != host.id:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    if b.status != B_REQUESTED:
        raise _not_open(b)
    now = _now()
    b.status, b.responded_at, b.closed_reason = B_REJECTED, now, (note or "").strip() or None
    await _release_hold(session, b.id)
    audit.record(session, action="booking.rejected", entity_type="booking", entity_id=b.id, actor_id=actor.id, ip=ip, reason=b.closed_reason)
    await _notify(session, b, "guest", "booking_rejected_guest")
    return b


async def withdraw(session: AsyncSession, guest: User, booking_id: str, ip: str | None) -> Booking:
    await expire_due(session)
    b = await _locked(session, booking_id)
    if b.guest_id != guest.id:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    if b.status not in (B_REQUESTED, B_ACCEPTED):
        raise _not_open(b)
    b.status, b.cancelled_at, b.cancelled_by = B_WITHDRAWN, _now(), guest.id
    await _release_hold(session, b.id)
    audit.record(session, action="booking.withdrawn", entity_type="booking", entity_id=b.id, actor_id=guest.id, ip=ip)
    await _notify(session, b, "host", "booking_withdrawn_host")
    return b


# ---------- reading ----------

async def _covers(session: AsyncSession, property_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not property_ids:
        return {}
    out: dict[uuid.UUID, str] = {}
    rows = (await session.execute(select(Media).where(Media.owner_type == "property", Media.owner_id.in_(property_ids), Media.status.in_(PUBLIC_MEDIA))
                                  .order_by(Media.position, Media.created_at))).scalars()
    for m in rows:
        out.setdefault(m.owner_id, f"/api/v1/media/{m.thumb_path}")
    return out


def _seconds_left(target: datetime | None, now: datetime) -> int | None:
    return max(0, int((target - now).total_seconds())) if target else None


async def serialize(session: AsyncSession, bookings: list[Booking], viewer: str) -> list[dict]:
    """viewer: 'guest' | 'host' | 'admin'. Each side sees only what it is meant to (the guest never sees commission,
    the host never sees the guest's contact details before payment)."""
    if not bookings:
        return []
    now = _now()
    props = {p.id: p for p in (await session.execute(select(Property).where(Property.id.in_({b.property_id for b in bookings})))).scalars()}
    units = {u.id: u for u in (await session.execute(select(Unit).where(Unit.id.in_({b.unit_id for b in bookings})))).scalars()}
    rooms = {r.id: r for r in (await session.execute(select(Room).where(Room.id.in_({b.room_id for b in bookings})))).scalars()}
    covers = await _covers(session, set(props))
    host_users: dict[uuid.UUID, tuple[HostProfile, User]] = {}
    for h in (await session.execute(select(HostProfile).where(HostProfile.id.in_({b.host_id for b in bookings})))).scalars():
        host_users[h.id] = (h, await session.get(User, h.user_id))
    out = []
    for b in bookings:
        prop, unit, tz = props[b.property_id], units[b.unit_id], ZoneInfo(b.timezone)
        p, shared = b.pricing_snapshot, b.status in CONFIRMED_STATES
        host, host_user = host_users[b.host_id]
        item = {
            "id": str(b.id), "reference": b.reference, "status": b.status, "hours": b.hours, "guests": b.guests, "currency": b.currency,
            "timezone": b.timezone, "check_in_local": availability.iso_local(b.check_in, tz), "check_out_local": availability.iso_local(b.check_out, tz),
            "check_in_label": availability.fmt_local(b.check_in, tz), "check_out_label": availability.fmt_local(b.check_out, tz),
            "requested_at": b.requested_at.isoformat() if b.requested_at else None,
            "expires_at": b.expires_at.isoformat() if b.expires_at else None,
            "payment_deadline_at": b.payment_deadline_at.isoformat() if b.payment_deadline_at else None,
            "seconds_to_respond": _seconds_left(b.expires_at, now) if b.status == B_REQUESTED else None,
            "seconds_to_pay": _seconds_left(b.payment_deadline_at, now) if b.status == B_ACCEPTED else None,
            "closed_reason": b.closed_reason if b.status in (B_REJECTED, B_EXPIRED, B_PAYMENT_EXPIRED) else None,
            "property": {"id": str(prop.id), "name": prop.name, "suburb": prop.suburb, "state": prop.state, "cover_url": covers.get(prop.id)},
            "unit": {"id": str(unit.id), "name": unit.name},
            "room": rooms[b.room_id].label if viewer != "guest" else None,
            "can_withdraw": viewer == "guest" and b.status in (B_REQUESTED, B_ACCEPTED),
        }
        if viewer == "guest":
            item["price"] = pricing.guest_view(p)
            item["host"] = {"first_name": host_user.full_name.split()[0] if host_user.full_name.strip() else "Your host"}
            if shared:
                item["host"].update({"name": host_user.full_name, "email": host_user.email, "phone": (host.application_data or {}).get("phone")})
                item["checkin_instructions"] = prop.checkin_instructions
        else:
            g = b.guest_snapshot
            guest = {"full_name": g.get("full_name"), "age": g.get("age"), "member_since": g.get("member_since"), "email_verified": True,
                     "social_links": g.get("social_links", [])}
            if shared or viewer == "admin":
                guest.update({"email": g.get("email"), "phone": g.get("phone"), "dob": g.get("dob")})
            item["guest"] = guest
            item["price"] = {k: p[k] for k in ("nights", "hours", "accommodation", "cleaning_fee", "commission", "commission_percent", "host_payable")}
            if viewer == "admin":
                item["price"] = p
                item["policy"] = b.policy_snapshot
                item["host"] = {"id": str(host.id), "name": host_user.full_name, "email": host_user.email}
        out.append(item)
    return out


async def list_for(session: AsyncSession, *, guest_id: uuid.UUID | None = None, host_id: uuid.UUID | None = None, group: str | None = None,
                   status: str | None = None, viewer: str, page: int = 1, page_size: int = 25) -> dict:
    if group is not None and group not in GROUPS:
        raise AppError(422, "INVALID_GROUP", "Unknown filter.")
    scope = []
    if guest_id:
        scope.append(Booking.guest_id == guest_id)
    if host_id:
        scope.append(Booking.host_id == host_id)
    query = select(Booking).where(*scope)
    if group:
        query = query.where(Booking.status.in_(GROUPS[group]))
    if status:
        query = query.where(Booking.status == status)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    # soonest deadline first for open requests, newest first otherwise
    order = (Booking.expires_at.asc(),) if group == "pending" else (Booking.requested_at.desc(),)
    rows = (await session.execute(query.order_by(*order).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    counts = {k: 0 for k in GROUPS}
    for st, n in (await session.execute(select(Booking.status, func.count()).where(*scope).group_by(Booking.status))).all():
        for k, statuses in GROUPS.items():
            if st in statuses:
                counts[k] += n
    return {"items": await serialize(session, list(rows), viewer), "total": total, "page": page, "page_size": page_size, "counts": counts}


async def get_for(session: AsyncSession, booking_id: str, *, guest_id: uuid.UUID | None = None, host_id: uuid.UUID | None = None, viewer: str) -> dict:
    try:
        bid = uuid.UUID(booking_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Booking not found.") from None
    b = await session.get(Booking, bid)
    if b is None or (guest_id and b.guest_id != guest_id) or (host_id and b.host_id != host_id):
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    return (await serialize(session, [b], viewer))[0]
