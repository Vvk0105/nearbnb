"""Reading an outside calendar and writing ours, behind a small interface so a channel-manager service could replace it later."""
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from icalendar import Calendar

from app.modules.ical.models import IcalSource
from app.modules.ical.safe_fetch import FeedError, fetch_feed

log = logging.getLogger(__name__)
MAX_EVENTS = 3000
HORIZON_DAYS = 800


@dataclass(frozen=True)
class ExternalRange:
    start: datetime  # UTC
    end: datetime    # UTC


@dataclass
class PullResult:
    ranges: list[ExternalRange]
    unchanged: bool = False
    etag: str | None = None
    last_modified: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FeedEntry:
    """One thing we tell the other site is taken."""
    uid: str
    start: datetime  # UTC
    end: datetime    # UTC
    stamp: datetime  # when it was created, so the feed text only changes when something really changed


class CalendarSyncAdapter(Protocol):
    async def pull(self, source: IcalSource, tz: ZoneInfo, now: datetime) -> PullResult: ...
    def push_feed(self, name: str, tz: ZoneInfo, entries: list[FeedEntry]) -> bytes: ...


def _local(day: date, at: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, at, tzinfo=tz).astimezone(UTC)


def parse_feed(body: bytes, checkin: time, checkout: time, tz: ZoneInfo, now: datetime) -> tuple[list[ExternalRange], list[str]]:
    """Turns a calendar file into the periods it says are taken. Whole-day events get our check-in and check-out times."""
    try:
        cal = Calendar.from_ical(body)
    except Exception:  # noqa: BLE001  (a malformed file is the host's problem to fix, not a crash)
        raise FeedError("That file is not a valid calendar.") from None
    warnings: list[str] = []
    found: list[ExternalRange] = []
    horizon = now + timedelta(days=HORIZON_DAYS)
    events = [c for c in cal.walk("VEVENT")][:MAX_EVENTS]
    for event in events:
        if str(event.get("STATUS", "")).upper() == "CANCELLED" or str(event.get("TRANSP", "")).upper() == "TRANSPARENT":
            continue
        start_prop = event.get("DTSTART")
        if start_prop is None:
            continue
        raw_start, end_prop = start_prop.dt, event.get("DTEND")
        if event.get("RRULE"):
            warnings.append("Repeating events are not supported; only their first date is used.")
        if isinstance(raw_start, datetime):
            start = raw_start.astimezone(UTC) if raw_start.tzinfo else raw_start.replace(tzinfo=tz).astimezone(UTC)
            if end_prop is not None and isinstance(end_prop.dt, datetime):
                raw_end = end_prop.dt
                end = raw_end.astimezone(UTC) if raw_end.tzinfo else raw_end.replace(tzinfo=tz).astimezone(UTC)
            elif event.get("DURATION") is not None:
                end = start + event.get("DURATION").dt
            else:
                end = start + timedelta(days=1)
        else:  # a date: the whole day, from check-in time on the first day to check-out time on the (exclusive) end date
            end_date = end_prop.dt if end_prop is not None and not isinstance(end_prop.dt, datetime) else raw_start + timedelta(days=1)
            start, end = _local(raw_start, checkin, tz), _local(end_date, checkout, tz)
        if end <= start or end <= now or start >= horizon:
            continue
        found.append(ExternalRange(start, min(end, horizon)))
    found.sort(key=lambda r: r.start)
    merged: list[ExternalRange] = []
    for r in found:  # two events that overlap would collide in the database, and mean one block anyway
        if merged and r.start < merged[-1].end:
            merged[-1] = ExternalRange(merged[-1].start, max(merged[-1].end, r.end))
        else:
            merged.append(r)
    return merged, sorted(set(warnings))


def build_feed(name: str, tz: ZoneInfo, entries: list[FeedEntry]) -> bytes:
    """Our calendar for another site. Every period is rounded OUTWARD to whole days, so the other site can never
    sell a day we have already sold part of. No guest information is included."""
    from icalendar import Event

    cal = Calendar()
    cal.add("PRODID", "-//NearBnB//Calendar 1.0//EN")
    cal.add("VERSION", "2.0")
    cal.add("CALSCALE", "GREGORIAN")
    cal.add("X-WR-CALNAME", f"NearBnB: {name}")
    for entry in sorted(entries, key=lambda e: (e.start, e.uid)):
        local_start, local_end = entry.start.astimezone(tz), entry.end.astimezone(tz)
        first = local_start.date()
        last_exclusive = local_end.date() + (timedelta(days=1) if local_end.time() != time(0, 0) else timedelta())
        event = Event()
        event.add("UID", entry.uid)
        event.add("DTSTAMP", entry.stamp.astimezone(UTC))
        event.add("DTSTART", first)
        event.add("DTEND", last_exclusive)
        event.add("SUMMARY", "Not available")
        event.add("TRANSP", "OPAQUE")
        cal.add_component(event)
    return cal.to_ical()


def etag_of(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


class IcalAdapter:
    def __init__(self, fetch=fetch_feed):
        self._fetch = fetch

    async def pull(self, source: IcalSource, tz: ZoneInfo, now: datetime) -> PullResult:
        result = await self._fetch(source.url, etag=source.etag, last_modified=source.last_modified)
        if result.status == 304:
            return PullResult([], unchanged=True, etag=source.etag, last_modified=source.last_modified)
        ranges, warnings = parse_feed(result.body, source.checkin_time, source.checkout_time, tz, now)
        return PullResult(ranges, etag=result.etag, last_modified=result.last_modified, warnings=warnings)

    def push_feed(self, name: str, tz: ZoneInfo, entries: list[FeedEntry]) -> bytes:
        return build_feed(name, tz, entries)
