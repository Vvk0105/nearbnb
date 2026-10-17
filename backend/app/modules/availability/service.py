"""Availability for guests: stay rules, free-room lookup, month calendar, day timeline, quotes, alternatives.

All stored times are UTC. A guest picks times in the PROPERTY's timezone ("2:00 PM at the property"), so every
public function converts local <-> UTC with the property's zone. A stay needs ONE room that is free for the whole
period, because a booking is assigned to a single room."""
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.hosts import service as host_service
from app.modules.hosts.models import HostProfile, HostSettingOverride
from app.modules.pricing import service as pricing
from app.modules.properties.models import ST_APPROVED, Property, Room, RoomOccupancy, Unit
from app.modules.settings import service as settings_service

BOOKED_KINDS = ("booking", "hold")
Busy = tuple[datetime, datetime, str]  # start (UTC), end (UTC), kind


@dataclass(frozen=True)
class StayRules:
    interval_minutes: int
    step_hours: int
    max_stay_hours: int
    min_lead_hours: int
    max_advance_days: int
    checkin_from: time
    checkin_to: time


@dataclass(frozen=True)
class Violation:
    code: str
    message: str


# ---------- formatting ----------

def fmt_local(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return f"{local.day} {local:%b} {local.hour % 12 or 12}:{local:%M} {local:%p}"


def iso_local(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%dT%H:%M")


def parse_local(value: str) -> datetime:
    """'2026-11-03T14:00' -> naive datetime. Rejects values that carry a timezone offset."""
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise AppError(422, "INVALID_DATETIME", "Use the format YYYY-MM-DDTHH:MM.") from None
    if parsed.tzinfo is not None:
        raise AppError(422, "INVALID_DATETIME", "Send the local time at the property, without a timezone offset.")
    return parsed


def to_utc(local_naive: datetime, tz: ZoneInfo) -> datetime:
    return local_naive.replace(tzinfo=tz).astimezone(UTC)


# ---------- rules ----------

async def load_global_rules(session: AsyncSession) -> dict[str, Any]:
    keys = ("checkin.interval_minutes", "booking.duration_step_hours", "booking.max_stay_hours",
            "booking.min_lead_time_hours", "booking.max_advance_days")
    return {k: await settings_service.get_value(session, k) for k in keys}


async def overrides_for_hosts(session: AsyncSession, host_ids: set[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    if not host_ids:
        return {}
    rows = (await session.execute(select(HostSettingOverride).where(HostSettingOverride.host_id.in_(host_ids)))).scalars().all()
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for row in rows:
        out.setdefault(row.host_id, {})[row.key] = row.value["v"]
    return out


def rules_for(unit: Unit, globals_: dict[str, Any], overrides: dict[str, Any] | None) -> StayRules:
    o = overrides or {}
    return StayRules(
        interval_minutes=globals_["checkin.interval_minutes"],
        step_hours=globals_["booking.duration_step_hours"],
        max_stay_hours=unit.max_stay_hours or globals_["booking.max_stay_hours"],
        min_lead_hours=o.get("booking.min_lead_time_hours", globals_["booking.min_lead_time_hours"]),
        max_advance_days=o.get("booking.max_advance_days", globals_["booking.max_advance_days"]),
        checkin_from=unit.checkin_from,
        checkin_to=unit.checkin_to,
    )


def _t(value: time) -> str:
    return f"{value.hour % 12 or 12}:{value:%M} {value:%p}"


def check_stay(rules: StayRules, capacity: int, tz: ZoneInfo, checkin_local: datetime, hours: int, guests: int, now: datetime) -> Violation | None:
    """The rules a stay must satisfy before availability is even considered. Same checks for search, quote and booking."""
    if hours < rules.step_hours or hours % rules.step_hours:
        return Violation("INVALID_DURATION", f"Choose a stay in multiples of {rules.step_hours} hours (24, 48, 72 ...).")
    if hours > rules.max_stay_hours:
        return Violation("STAY_TOO_LONG", f"The longest stay here is {rules.max_stay_hours // 24} days.")
    if guests > capacity:
        return Violation("OVER_CAPACITY", f"This place sleeps up to {capacity} guests.")
    if checkin_local.second or checkin_local.minute % rules.interval_minutes:
        step = "on the hour (for example 2:00 PM)" if rules.interval_minutes == 60 else f"every {rules.interval_minutes} minutes"
        return Violation("INVALID_TIME_STEP", f"Check-in is available {step}.")
    if not (rules.checkin_from <= checkin_local.time() <= rules.checkin_to):
        return Violation("OUTSIDE_CHECKIN_HOURS", f"Check-in is only possible between {_t(rules.checkin_from)} and {_t(rules.checkin_to)}.")
    checkin_utc = to_utc(checkin_local, tz)
    if checkin_utc <= now:
        return Violation("IN_THE_PAST", "Check-in must be in the future.")
    if checkin_utc < now + timedelta(hours=rules.min_lead_hours):
        return Violation("LEAD_TIME_VIOLATION", f"Check-in must be at least {rules.min_lead_hours} hour(s) from now so the host has time to respond.")
    if checkin_utc > now + timedelta(days=rules.max_advance_days):
        return Violation("TOO_FAR_AHEAD", f"Bookings open up to {rules.max_advance_days} days ahead.")
    return None


# ---------- occupancy ----------

async def busy_by_room(session: AsyncSession, room_ids: list[uuid.UUID], start: datetime, end: datetime) -> dict[uuid.UUID, list[Busy]]:
    """Occupancy that overlaps [start, end) per room, whole intervals (not clipped). Expired holds are ignored."""
    if not room_ids:
        return {}
    now = datetime.now(UTC)
    rows = (await session.execute(
        select(RoomOccupancy.room_id, func.lower(RoomOccupancy.period), func.upper(RoomOccupancy.period), RoomOccupancy.kind)
        .where(RoomOccupancy.room_id.in_(room_ids), RoomOccupancy.period.overlaps(Range(start, end, bounds="[)")),
               or_(RoomOccupancy.expires_at.is_(None), RoomOccupancy.expires_at > now))
        .order_by(func.lower(RoomOccupancy.period))
    )).all()
    out: dict[uuid.UUID, list[Busy]] = {rid: [] for rid in room_ids}
    for room_id, lo, hi, kind in rows:
        out[room_id].append((lo, hi, kind))
    return out


def _overlaps(busy: list[Busy], start: datetime, end: datetime) -> list[Busy]:
    return [b for b in busy if b[0] < end and b[1] > start]


def active_rooms(unit: Unit) -> list[Room]:
    return [r for r in unit.rooms if r.active]


async def free_rooms(session: AsyncSession, unit: Unit, start: datetime, end: datetime) -> list[Room]:
    rooms = active_rooms(unit)
    busy = await busy_by_room(session, [r.id for r in rooms], start, end)
    return [r for r in rooms if not _overlaps(busy[r.id], start, end)]


async def units_with_free_room(session: AsyncSession, unit_ids: list[uuid.UUID], start: datetime, end: datetime) -> set[uuid.UUID]:
    """One query for many units: which have at least one active room free for the whole period."""
    if not unit_ids:
        return set()
    now = datetime.now(UTC)
    taken = select(RoomOccupancy.id).where(
        RoomOccupancy.room_id == Room.id, RoomOccupancy.period.overlaps(Range(start, end, bounds="[)")),
        or_(RoomOccupancy.expires_at.is_(None), RoomOccupancy.expires_at > now),
    ).exists()
    rows = await session.execute(select(Room.unit_id).where(Room.unit_id.in_(unit_ids), Room.active.is_(True), ~taken).group_by(Room.unit_id))
    return {row[0] for row in rows}


def _intersect(a: list[Busy], b: list[Busy]) -> list[Busy]:
    """Times covered by both lists. Each list is sorted and non-overlapping (the database guarantees it per room)."""
    out: list[Busy] = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if lo < hi:
            kind = "booking" if "booking" in (a[i][2], b[j][2]) or "hold" in (a[i][2], b[j][2]) else a[i][2]
            out.append((lo, hi, kind))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def all_rooms_busy(busy: dict[uuid.UUID, list[Busy]]) -> list[Busy]:
    """Periods when NO room of the unit is free ("sold out")."""
    lists = list(busy.values())
    if not lists:
        return []
    result = lists[0]
    for other in lists[1:]:
        result = _intersect(result, other)
    return result


# ---------- visibility ----------

async def public_unit(session: AsyncSession, unit_id: uuid.UUID) -> tuple[Unit, Property, HostProfile]:
    unit = (await session.execute(select(Unit).where(Unit.id == unit_id).execution_options(populate_existing=True))).scalar_one_or_none()
    if unit is None or unit.status != ST_APPROVED:
        raise AppError(404, "NOT_FOUND", "This place is not available.")
    prop = (await session.execute(select(Property).where(Property.id == unit.property_id).execution_options(populate_existing=True))).scalar_one()
    host = await session.get(HostProfile, prop.host_id)
    if prop.status != ST_APPROVED or not host_service.is_active(host):
        raise AppError(404, "NOT_FOUND", "This place is not available.")
    return unit, prop, host


# ---------- month calendar and day timeline ----------

def _local_midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time.min, tzinfo=tz).astimezone(UTC)


async def month_calendar(session: AsyncSession, unit: Unit, prop: Property, host: HostProfile, year: int, month: int) -> dict:
    tz = ZoneInfo(prop.timezone)
    first = date(year, month, 1)
    next_first = date(year + (month == 12), month % 12 + 1, 1)
    start, end = _local_midnight(first, tz), _local_midnight(next_first, tz)
    rooms = active_rooms(unit)
    busy = await busy_by_room(session, [r.id for r in rooms], start, end)
    globals_ = await load_global_rules(session)
    rules = rules_for(unit, globals_, (await overrides_for_hosts(session, {host.id})).get(host.id))
    now = datetime.now(UTC)

    days = []
    d = first
    while d < next_first:
        ds, de = _local_midnight(d, tz), _local_midnight(d + timedelta(days=1), tz)
        free_room = False
        some_free_time = False
        for room in rooms:
            overlapping = _overlaps(busy[room.id], ds, de)
            if not overlapping:
                free_room = True
                break
            covered = sum((min(b[1], de) - max(b[0], ds)).total_seconds() for b in overlapping)
            if covered < (de - ds).total_seconds():
                some_free_time = True
        if de <= now:
            status = "past"
        elif ds > now + timedelta(days=rules.max_advance_days):
            status = "closed"
        elif free_room:
            status = "available"
        elif some_free_time:
            status = "partial"
        else:
            status = "unavailable"
        days.append({"date": d.isoformat(), "status": status})
        d += timedelta(days=1)
    return {"timezone": prop.timezone, "year": year, "month": month, "days": days}


async def day_timeline(session: AsyncSession, unit: Unit, prop: Property, d: date) -> dict:
    tz = ZoneInfo(prop.timezone)
    ds, de = _local_midnight(d, tz), _local_midnight(d + timedelta(days=1), tz)
    rooms = active_rooms(unit)
    busy = await busy_by_room(session, [r.id for r in rooms], ds, de)
    sold_out = [b for b in all_rooms_busy(busy) if b[0] < de and b[1] > ds]
    return {
        "timezone": prop.timezone, "date": d.isoformat(), "rooms": len(rooms),
        # Guests see when the place is taken, not why: a host block and a booking look alike except for the wording.
        "occupied": [
            {"start_local": iso_local(lo, tz), "end_local": iso_local(hi, tz), "kind": "booked" if kind in BOOKED_KINDS else "unavailable",
             "label": (f"Someone is staying from {fmt_local(lo, tz)} until {fmt_local(hi, tz)}." if kind in BOOKED_KINDS
                       else f"Not available from {fmt_local(lo, tz)} until {fmt_local(hi, tz)}.")}
            for lo, hi, kind in sold_out
        ],
    }


# ---------- alternatives ----------

async def alternatives(session: AsyncSession, unit: Unit, prop: Property, rules: StayRules, requested_utc: datetime, hours: int,
                       now: datetime, limit: int = 3, scan_days: int = 30) -> list[dict]:
    """The next few check-in times (one per day) when a room is free for the whole stay."""
    tz = ZoneInfo(prop.timezone)
    rooms = active_rooms(unit)
    window_start = max(requested_utc, now)
    window_end = window_start + timedelta(days=scan_days, hours=hours + 24)
    busy = await busy_by_room(session, [r.id for r in rooms], window_start, window_end)
    first_day = window_start.astimezone(tz).date()
    found: list[dict] = []
    for offset in range(scan_days + 1):
        day = first_day + timedelta(days=offset)
        minute = rules.checkin_from.hour * 60 + rules.checkin_from.minute
        minute -= minute % rules.interval_minutes
        last = rules.checkin_to.hour * 60 + rules.checkin_to.minute
        while minute <= last:
            local = datetime.combine(day, time(minute // 60, minute % 60))
            minute += rules.interval_minutes
            start = to_utc(local, tz)
            if start < requested_utc or check_stay(rules, 999, tz, local, hours, 0, now):
                continue
            end = start + timedelta(hours=hours)
            if any(not _overlaps(busy[r.id], start, end) for r in rooms):
                found.append({"checkin_local": local.strftime("%Y-%m-%dT%H:%M"), "checkout_local": iso_local(end, tz),
                              "label": f"{fmt_local(start, tz)} to {fmt_local(end, tz)}"})
                break
        if len(found) >= limit:
            break
    return found


# ---------- quote ----------

async def unit_price(session: AsyncSession, unit: Unit, prop: Property, overrides: dict[str, Any], hours: int) -> dict:
    """The full price breakdown (including commission and host payable) for a stay of `hours` at today's settings."""
    service_fee = await settings_service.get_value(session, "finance.service_fee_percent")
    commission = (overrides["finance.commission_percent"] if "finance.commission_percent" in overrides
                  else await settings_service.get_value(session, "finance.commission_percent"))
    return pricing.compute_price(
        price_per_24h=unit.price_per_24h, hours=hours, cleaning_fee=unit.cleaning_fee, service_fee_percent=service_fee,
        tax=await pricing.tax_for(session, prop.country, prop.state), commission_percent=commission,
        commission_base=await settings_service.get_value(session, "finance.commission_base"),
        rounding=await settings_service.get_value(session, "finance.rounding"),
    )


async def build_quote(session: AsyncSession, unit: Unit, prop: Property, host: HostProfile, checkin_local_str: str, hours: int, guests: int) -> dict:
    tz = ZoneInfo(prop.timezone)
    checkin_local = parse_local(checkin_local_str)
    globals_ = await load_global_rules(session)
    overrides = (await overrides_for_hosts(session, {host.id})).get(host.id, {})
    rules = rules_for(unit, globals_, overrides)
    now = datetime.now(UTC)
    base = {"unit_id": str(unit.id), "timezone": prop.timezone, "hours": hours, "guests": guests}

    violation = check_stay(rules, unit.capacity, tz, checkin_local, hours, guests, now)
    if violation:
        return {**base, "available": False, "code": violation.code, "message": violation.message, "alternatives": []}

    start = to_utc(checkin_local, tz)
    end = start + timedelta(hours=hours)
    rooms = active_rooms(unit)
    busy = await busy_by_room(session, [r.id for r in rooms], start, end)
    free = [r for r in rooms if not _overlaps(busy[r.id], start, end)]

    if not free:
        if len(rooms) == 1:
            lo, hi, kind = _overlaps(busy[rooms[0].id], start, end)[0]
            message = (f"Someone is staying from {fmt_local(lo, tz)} until {fmt_local(hi, tz)}." if kind in BOOKED_KINDS
                       else f"This place is not available from {fmt_local(lo, tz)} until {fmt_local(hi, tz)}.")
        else:
            sold = [b for b in all_rooms_busy(busy) if b[0] < end and b[1] > start]
            message = (f"Every room is taken from {fmt_local(sold[0][0], tz)} until {fmt_local(sold[0][1], tz)}." if sold
                       else "No single room is free for the whole period you chose.")
        alts = await alternatives(session, unit, prop, rules, start, hours, now)
        return {**base, "available": False, "code": "SLOT_UNAVAILABLE", "message": message, "alternatives": alts}

    breakdown = await unit_price(session, unit, prop, overrides, hours)
    return {**base, "available": True, "checkin_local": checkin_local.strftime("%Y-%m-%dT%H:%M"), "checkout_local": iso_local(end, tz),
            "checkin_label": fmt_local(start, tz), "checkout_label": fmt_local(end, tz), "currency": unit.currency,
            "rooms_free": len(free), "price": pricing.guest_view(breakdown),
            "response_minutes": overrides.get("booking.host_response_minutes") or await settings_service.get_value(session, "booking.host_response_minutes")}
