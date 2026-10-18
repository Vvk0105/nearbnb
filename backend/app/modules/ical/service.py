"""Calendar sync (iCal-lite): keeping NearBnB and other booking sites from selling the same dates.

IMPORT: every few minutes each source's feed is read and its taken periods become `external_block` rows on the unit's single
room (so guests cannot request them). The blocks are replaced idempotently: only differences are written. If the feed cannot
be read, the blocks already there are KEPT (the safe choice) and the failure is recorded.

CONFLICTS: if an outside period lands on dates that already hold a confirmed booking or a pending request, nothing is
cancelled or overwritten. The part that is free still gets blocked, a conflict is recorded, and the host and the admins are
emailed. A person decides.

EXPORT: each unit has a private link serving our confirmed bookings and the host's own blocks, rounded outward to whole days.
Imported blocks are never exported back (that would make the two sites echo each other's bookings forever)."""
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.locks import lock_unit
from app.modules.audit import service as audit
from app.modules.availability import service as availability
from app.modules.bookings import service as bookings
from app.modules.bookings.models import B_CHECKED_IN, B_CONFIRMED, Booking
from app.modules.hosts.models import HostProfile
from app.modules.ical.adapter import CalendarSyncAdapter, ExternalRange, FeedEntry, IcalAdapter, etag_of
from app.modules.ical.models import CF_DISMISSED, CF_OPEN, CF_RESOLVED, IcalConflict, IcalExport, IcalSource
from app.modules.ical.safe_fetch import FeedError, validate_url
from app.modules.properties.models import Property, Room, RoomOccupancy, Unit
from app.modules.settings import service as settings_service
from app.modules.users.models import User

log = logging.getLogger(__name__)
MAX_SOURCES_PER_UNIT = 3
MANUAL_SYNC_GAP_SECONDS = 60
ALERT_AFTER_FAILURES = 5
OWN_FEED = re.compile(r"^/(api/v\d+/)?ical/[\w-]{16,}\.ics$")


def _now() -> datetime:
    return datetime.now(UTC)


def single_room(unit: Unit) -> Room | None:
    """Calendar sync works for units with exactly one room. With several identical rooms we cannot tell which one an outside
    booking took, so those units are not eligible."""
    rooms = [r for r in unit.rooms if r.active]
    return rooms[0] if len(rooms) == 1 else None


def source_json(s: IcalSource) -> dict:
    return {"id": str(s.id), "unit_id": str(s.unit_id), "name": s.name, "url": s.url, "checkin_time": s.checkin_time.strftime("%H:%M"), "checkout_time": s.checkout_time.strftime("%H:%M"),
            "active": s.active, "last_synced_at": s.last_synced_at.isoformat() if s.last_synced_at else None, "last_attempt_at": s.last_attempt_at.isoformat() if s.last_attempt_at else None,
            "last_error": s.last_error, "events_count": s.events_count, "failing": s.consecutive_failures > 0}


# ---------- sources ----------

def _check_link(raw: str, own_hosts: set[str]) -> str:
    try:
        url = validate_url(raw)
    except FeedError as exc:
        raise AppError(422, "INVALID_CALENDAR_LINK", str(exc)) from None
    parts = urlsplit(url)
    if OWN_FEED.match(parts.path) and (parts.hostname or "").lower() in own_hosts:
        raise AppError(422, "OWN_CALENDAR_LINK", "That is NearBnB's own calendar link. Paste the link from the OTHER site here, and paste ours into the other site.")
    return url


def _check_times(checkin: time, checkout: time) -> None:
    if checkin.minute % 15 or checkout.minute % 15:
        raise AppError(422, "INVALID_TIME", "Use times on the quarter hour.")


async def create_source(session: AsyncSession, host: HostProfile, actor: User, unit: Unit, *, name: str, url: str, checkin: time, checkout: time, own_hosts: set[str]) -> IcalSource:
    name = name.strip()
    if not name:
        raise AppError(422, "NAME_REQUIRED", "Give this calendar a name, for example Airbnb.")
    if single_room(unit) is None:
        raise AppError(409, "NOT_ELIGIBLE", "Calendar sync works for units with a single room. This unit has several.")
    _check_times(checkin, checkout)
    count = (await session.execute(select(func.count()).select_from(IcalSource).where(IcalSource.unit_id == unit.id))).scalar_one()
    if count >= MAX_SOURCES_PER_UNIT:
        raise AppError(409, "TOO_MANY_SOURCES", f"You can connect up to {MAX_SOURCES_PER_UNIT} calendars to one unit.")
    source = IcalSource(unit_id=unit.id, name=name, url=_check_link(url, own_hosts), checkin_time=checkin, checkout_time=checkout)
    session.add(source)
    await session.flush()
    audit.record(session, action="ical.source_added", entity_type="unit", entity_id=unit.id, actor_id=actor.id, after={"name": name, "host": urlsplit(source.url).hostname})
    return source


async def load_source(session: AsyncSession, source_id: str, unit_ids: set[uuid.UUID] | None = None) -> IcalSource:
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Calendar not found.") from None
    source = (await session.execute(select(IcalSource).where(IcalSource.id == sid).with_for_update())).scalar_one_or_none()
    if source is None or (unit_ids is not None and source.unit_id not in unit_ids):
        raise AppError(404, "NOT_FOUND", "Calendar not found.")
    return source


async def update_source(session: AsyncSession, actor: User, source: IcalSource, *, name: str | None, url: str | None, checkin: time | None, checkout: time | None,
                        active: bool | None, own_hosts: set[str]) -> IcalSource:
    if name is not None:
        if not name.strip():
            raise AppError(422, "NAME_REQUIRED", "Give this calendar a name.")
        source.name = name.strip()
    if url is not None and url.strip() != source.url:
        source.url = _check_link(url, own_hosts)
        source.etag = source.last_modified = None  # a different feed: forget what we knew about the old one
    if checkin is not None or checkout is not None:
        source.checkin_time, source.checkout_time = checkin or source.checkin_time, checkout or source.checkout_time
        _check_times(source.checkin_time, source.checkout_time)
        source.etag = source.last_modified = None  # whole-day events must be turned into dates again
    if active is not None:
        source.active = active
    audit.record(session, action="ical.source_updated", entity_type="unit", entity_id=source.unit_id, actor_id=actor.id, after={"name": source.name, "active": source.active})
    return source


async def delete_source(session: AsyncSession, actor: User, source: IcalSource) -> None:
    """Removing a calendar removes the blocks it created (and its conflicts) at once."""
    unit = await session.get(Unit, source.unit_id)
    await lock_unit(session, unit.id)
    await session.execute(delete(RoomOccupancy).where(RoomOccupancy.source_id == source.id, RoomOccupancy.kind == "external_block"))
    audit.record(session, action="ical.source_removed", entity_type="unit", entity_id=unit.id, actor_id=actor.id, after={"name": source.name})
    await session.delete(source)


# ---------- importing ----------

def _subtract(segments: list[tuple[datetime, datetime]], lo: datetime, hi: datetime) -> list[tuple[datetime, datetime]]:
    out = []
    for s, e in segments:
        if e <= lo or s >= hi:
            out.append((s, e))
            continue
        if s < lo:
            out.append((s, lo))
        if e > hi:
            out.append((hi, e))
    return out


def _ranges_from_json(raw: list) -> list[ExternalRange]:
    return [ExternalRange(datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in raw]


async def apply_ranges(session: AsyncSession, source: IcalSource, unit: Unit, prop: Property, room: Room, ranges: list[ExternalRange], now: datetime) -> dict:
    """Makes the room's external blocks for this source match the feed. Returns counts."""
    # A hold that has run out still sits in the table until the expiry job removes it, and the overlap rule cannot tell.
    await session.execute(delete(RoomOccupancy).where(RoomOccupancy.room_id == room.id, RoomOccupancy.kind == "hold", RoomOccupancy.expires_at <= now))
    others: list[RoomOccupancy] = []
    if ranges:
        span = Range(min(r.start for r in ranges), max(r.end for r in ranges), bounds="[)")
        others = list((await session.execute(
            select(RoomOccupancy).where(RoomOccupancy.room_id == room.id, RoomOccupancy.period.overlaps(span),
                                        or_(RoomOccupancy.kind != "external_block", RoomOccupancy.source_id != source.id))
        )).scalars())
    desired: set[tuple[datetime, datetime]] = set()
    clashes: dict[tuple[uuid.UUID, datetime, datetime], None] = {}
    for r in ranges:
        free = [(r.start, r.end)]
        for o in others:
            lo, hi = o.period.lower, o.period.upper
            if lo < r.end and hi > r.start:
                free = _subtract(free, lo, hi)
                if o.kind in ("booking", "hold") and o.booking_id is not None:
                    clashes[(o.booking_id, max(lo, r.start), min(hi, r.end))] = None
        desired.update(free)

    mine = list((await session.execute(select(RoomOccupancy).where(RoomOccupancy.source_id == source.id, RoomOccupancy.kind == "external_block"))).scalars())
    existing = {(m.period.lower, m.period.upper): m for m in mine}
    removed = 0
    for key, row in existing.items():
        if key not in desired:
            await session.delete(row)
            removed += 1
    await session.flush()
    added = 0
    for s, e in sorted(desired - set(existing)):
        session.add(RoomOccupancy(room_id=room.id, period=Range(s, e, bounds="[)"), kind="external_block", source_id=source.id, note=source.name))
        added += 1
    await session.flush()

    opened = await _record_conflicts(session, source, unit, prop, list(clashes), now)
    return {"blocks": len(desired), "added": added, "removed": removed, "conflicts_opened": opened}


async def _record_conflicts(session: AsyncSession, source: IcalSource, unit: Unit, prop: Property, clashes: list[tuple[uuid.UUID, datetime, datetime]], now: datetime) -> int:
    opened = 0
    seen = set(clashes)
    for booking_id, s, e in clashes:
        c = (await session.execute(select(IcalConflict).where(IcalConflict.source_id == source.id, IcalConflict.booking_id == booking_id,
                                                              IcalConflict.external_start == s, IcalConflict.external_end == e))).scalar_one_or_none()
        if c is None:
            c = IcalConflict(unit_id=unit.id, source_id=source.id, booking_id=booking_id, external_start=s, external_end=e)
            session.add(c)
            await session.flush()
        elif c.status == CF_RESOLVED:
            c.status, c.resolved_at, c.note = CF_OPEN, None, None
        else:
            continue  # already open (alerted before) or dismissed by a person
        opened += 1
        await _alert_conflict(session, c, source, prop)
    for c in (await session.execute(select(IcalConflict).where(IcalConflict.source_id == source.id, IcalConflict.status == CF_OPEN))).scalars():
        if (c.booking_id, c.external_start, c.external_end) not in seen:
            c.status, c.resolved_at, c.note = CF_RESOLVED, now, "The dates no longer clash."
    return opened


async def _alert_conflict(session: AsyncSession, c: IcalConflict, source: IcalSource, prop: Property) -> None:
    b = await session.get(Booking, c.booking_id)
    tz = ZoneInfo(prop.timezone)
    extra = {"source_name": source.name, "clash_from": availability.fmt_local(c.external_start, tz), "clash_to": availability.fmt_local(c.external_end, tz), "booking_status": b.status.replace("_", " ")}
    await bookings._notify(session, b, "host", "ical_conflict_host", **extra)
    await bookings.notify_admins(session, "ical_conflict_admin", {"reference": b.reference, "property": prop.name, "unit_id": str(c.unit_id), **extra})
    audit.record(session, action="ical.conflict_detected", entity_type="booking", entity_id=b.id, after={"source": source.name})


async def _record_failure(session: AsyncSession, source: IcalSource, prop: Property, message: str) -> dict:
    source.last_error = message
    source.consecutive_failures += 1
    if source.consecutive_failures >= ALERT_AFTER_FAILURES and not source.failure_notified:
        source.failure_notified = True
        from app.modules.notifications.service import enqueue_email

        unit = await session.get(Unit, source.unit_id)
        host = await session.get(HostProfile, prop.host_id)
        owner = await session.get(User, host.user_id)
        enqueue_email(session, to_email=owner.email, template="ical_sync_failing", user_id=owner.id,
                      context={"name": owner.full_name, "source_name": source.name, "unit": f"{prop.name} ({unit.name})", "error": message, "failures": source.consecutive_failures,
                               "url": get_settings().app_base_url.rstrip("/"), "property_id": str(prop.id), "unit_id": str(unit.id)})
    return {"ok": False, "error": message, "blocks": source.events_count}


async def sync_source(session: AsyncSession, source: IcalSource, *, adapter: CalendarSyncAdapter | None = None, now: datetime | None = None, manual: bool = False) -> dict:
    adapter = adapter or IcalAdapter()
    now = now or _now()
    if manual and source.last_attempt_at and (now - source.last_attempt_at).total_seconds() < MANUAL_SYNC_GAP_SECONDS:
        raise AppError(429, "SYNC_TOO_SOON", "Please wait a minute before syncing again.")
    unit = await session.get(Unit, source.unit_id)
    prop = await session.get(Property, unit.property_id)
    room = single_room(unit)
    source.last_attempt_at = now
    if room is None:
        return await _record_failure(session, source, prop, "This unit has more than one room, so calendar sync cannot be used.")
    await lock_unit(session, unit.id)
    try:
        pulled = await adapter.pull(source, ZoneInfo(prop.timezone), now)
    except FeedError as exc:
        return await _record_failure(session, source, prop, str(exc))
    except Exception:  # noqa: BLE001
        log.exception("calendar sync crashed for source %s", source.id)
        return await _record_failure(session, source, prop, "Something went wrong reading that calendar.")
    ranges = _ranges_from_json(source.ranges or []) if pulled.unchanged else pulled.ranges
    try:
        async with session.begin_nested():
            stats = await apply_ranges(session, source, unit, prop, room, ranges, now)
    except Exception:  # noqa: BLE001  (the savepoint is rolled back, so the blocks that were there are still there)
        log.exception("applying calendar %s failed", source.id)
        return await _record_failure(session, source, prop, "Could not update the calendar blocks. We will try again.")
    if not pulled.unchanged:
        source.ranges = [[r.start.isoformat(), r.end.isoformat()] for r in pulled.ranges]
        source.etag, source.last_modified = pulled.etag, pulled.last_modified
    source.last_error, source.consecutive_failures, source.failure_notified = None, 0, False
    source.last_synced_at, source.events_count = now, stats["blocks"]
    return {"ok": True, "unchanged": pulled.unchanged, "warnings": pulled.warnings, **stats}


async def dispatch_due(session: AsyncSession, *, adapter: CalendarSyncAdapter | None = None, now: datetime | None = None, limit: int = 50) -> dict:
    """The scheduled job: sync every active source that has not been tried for the configured interval."""
    now = now or _now()
    poll = await settings_service.get_value(session, "ical.poll_minutes")
    due = (await session.execute(
        select(IcalSource).where(IcalSource.active.is_(True), or_(IcalSource.last_attempt_at.is_(None), IcalSource.last_attempt_at <= now - timedelta(minutes=poll)))
        .order_by(IcalSource.last_attempt_at.asc().nullsfirst()).limit(limit).with_for_update(skip_locked=True)
    )).scalars().all()
    counts = {"synced": 0, "failed": 0}
    for source in due:
        try:
            async with session.begin_nested():
                result = await sync_source(session, source, adapter=adapter, now=now)
            counts["synced" if result["ok"] else "failed"] += 1
        except Exception:  # noqa: BLE001
            log.exception("calendar dispatch failed for source %s", source.id)
            counts["failed"] += 1
    return counts


# ---------- exporting ----------

async def export_for_unit(session: AsyncSession, unit: Unit) -> IcalExport:
    export = (await session.execute(select(IcalExport).where(IcalExport.unit_id == unit.id))).scalar_one_or_none()
    if export is None:
        export = IcalExport(unit_id=unit.id, token=secrets.token_urlsafe(24))
        session.add(export)
        await session.flush()
    return export


async def rotate_export(session: AsyncSession, actor: User, unit: Unit) -> IcalExport:
    """A new private link; the old one stops working at once (for example if it was shared by mistake)."""
    export = await export_for_unit(session, unit)
    export.token = secrets.token_urlsafe(24)
    audit.record(session, action="ical.export_rotated", entity_type="unit", entity_id=unit.id, actor_id=actor.id)
    return export


async def build_export(session: AsyncSession, token: str, adapter: CalendarSyncAdapter | None = None) -> tuple[bytes, str] | None:
    adapter = adapter or IcalAdapter()
    export = (await session.execute(select(IcalExport).where(IcalExport.token == token))).scalar_one_or_none()
    if export is None:
        return None
    unit = await session.get(Unit, export.unit_id)
    prop = await session.get(Property, unit.property_id)
    room_ids = [r.id for r in unit.rooms]
    since = _now() - timedelta(days=2)
    entries: list[FeedEntry] = []
    for b in (await session.execute(select(Booking).where(Booking.unit_id == unit.id, Booking.status.in_((B_CONFIRMED, B_CHECKED_IN)), Booking.check_out >= since))).scalars():
        entries.append(FeedEntry(f"{b.reference}@nearbnb", b.check_in, b.check_out, b.confirmed_at or b.requested_at))
    for o in (await session.execute(select(RoomOccupancy).where(RoomOccupancy.room_id.in_(room_ids), RoomOccupancy.kind == "host_block", func.upper(RoomOccupancy.period) >= since))).scalars():
        entries.append(FeedEntry(f"block-{o.id}@nearbnb", o.period.lower, o.period.upper, o.created_at))
    body = adapter.push_feed(f"{prop.name} - {unit.name}", ZoneInfo(prop.timezone), entries)
    return body, etag_of(body)


# ---------- conflicts ----------

def conflict_json(c: IcalConflict, b: Booking, prop: Property, source: IcalSource) -> dict:
    tz = ZoneInfo(prop.timezone)
    return {"id": str(c.id), "unit_id": str(c.unit_id), "source": source.name, "status": c.status, "reference": b.reference, "booking_id": str(b.id), "booking_status": b.status,
            "property": prop.name, "booking_from": availability.fmt_local(b.check_in, tz), "booking_to": availability.fmt_local(b.check_out, tz),
            "clash_from": availability.fmt_local(c.external_start, tz), "clash_to": availability.fmt_local(c.external_end, tz),
            "detected_at": c.detected_at.isoformat(), "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None, "note": c.note}


async def list_conflicts(session: AsyncSession, *, unit_ids: set[uuid.UUID] | None, status: str | None, page: int = 1, page_size: int = 25) -> dict:
    q = select(IcalConflict)
    if unit_ids is not None:
        q = q.where(IcalConflict.unit_id.in_(unit_ids))
    if status:
        q = q.where(IcalConflict.status == status)
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await session.execute(q.order_by(IcalConflict.detected_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    items = []
    for c in rows:
        b = await session.get(Booking, c.booking_id)
        items.append(conflict_json(c, b, await session.get(Property, b.property_id), await session.get(IcalSource, c.source_id)))
    return {"total": total, "page": page, "page_size": page_size, "items": items}


async def dismiss_conflict(session: AsyncSession, actor: User, conflict_id: str, unit_ids: set[uuid.UUID] | None, note: str | None) -> IcalConflict:
    try:
        cid = uuid.UUID(conflict_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Conflict not found.") from None
    c = (await session.execute(select(IcalConflict).where(IcalConflict.id == cid).with_for_update())).scalar_one_or_none()
    if c is None or (unit_ids is not None and c.unit_id not in unit_ids):
        raise AppError(404, "NOT_FOUND", "Conflict not found.")
    if c.status != CF_OPEN:
        raise AppError(409, "INVALID_STATE", "This conflict is already closed.")
    c.status, c.resolved_at, c.note = CF_DISMISSED, _now(), (note or "").strip() or "Acknowledged."
    audit.record(session, action="ical.conflict_dismissed", entity_type="booking", entity_id=c.booking_id, actor_id=actor.id, reason=c.note)
    return c


async def failing_sources(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(
        select(IcalSource, Unit, Property).join(Unit, Unit.id == IcalSource.unit_id).join(Property, Property.id == Unit.property_id)
        .where(IcalSource.consecutive_failures > 0).order_by(IcalSource.consecutive_failures.desc()).limit(100))).all()
    return [{**source_json(s), "property": p.name, "unit": u.name, "failures": s.consecutive_failures} for s, u, p in rows]
