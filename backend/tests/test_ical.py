import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from icalendar import Calendar
from sqlalchemy import select, text, update

from app.core.db import SessionLocal
from app.jobs.celery_app import celery_app
from app.modules.bookings.models import Booking
from app.modules.ical import service as ical_service
from app.modules.ical.adapter import FeedEntry, IcalAdapter, build_feed, parse_feed
from app.modules.ical.models import IcalConflict, IcalSource
from app.modules.ical.safe_fetch import FeedError, fetch_feed, is_public, validate_url
from app.modules.properties.models import RoomOccupancy
from tests.conftest import csrf_headers
from tests.helpers import API, active_host, admin_client, day_local, live_listing, occupy
from tests.test_bookings import book, complete_guest, emails
from tests.test_cancellations import get
from tests.test_payments import dev_complete, row

SYD = ZoneInfo("Australia/Sydney")
NOW = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)


def cal(*events: str) -> bytes:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Airbnb Inc//Hosting Calendar 1.0//EN\r\n" + "".join(events) + "END:VCALENDAR\r\n").encode()


def all_day(start: str, end: str, uid: str = "u1", extra: str = "") -> str:
    return f"BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:{start}\r\nDTEND;VALUE=DATE:{end}\r\nUID:{uid}\r\nSUMMARY:Reserved\r\n{extra}END:VEVENT\r\n"


def parse(body: bytes, checkin=time(15, 0), checkout=time(11, 0)):
    return parse_feed(body, checkin, checkout, SYD, NOW)


# ---------- reading a calendar file ----------

class TestParsing:
    def test_whole_days_get_our_check_in_and_check_out_times(self):
        ranges, _ = parse(cal(all_day("20260710", "20260712")))
        (r,) = ranges
        assert r.start.astimezone(SYD) == datetime(2026, 7, 10, 15, 0, tzinfo=SYD) and r.end.astimezone(SYD) == datetime(2026, 7, 12, 11, 0, tzinfo=SYD)

    def test_the_times_stay_the_same_on_the_wall_clock_across_daylight_saving(self):
        ranges, _ = parse(cal(all_day("20261002", "20261004"), all_day("20261010", "20261012", "u2")))
        before, after = ranges
        assert before.start.utcoffset() == after.start.utcoffset() == timedelta(0)             # stored in UTC
        assert before.start.astimezone(SYD).hour == after.start.astimezone(SYD).hour == 15
        assert before.start.astimezone(SYD).utcoffset() != after.start.astimezone(SYD).utcoffset()   # Sydney moved its clocks in between

    def test_the_hosts_own_times_are_used(self):
        (r,), _ = parse(cal(all_day("20260710", "20260711")), checkin=time(14, 0), checkout=time(10, 30))
        assert r.start.astimezone(SYD).time() == time(14, 0) and r.end.astimezone(SYD).time() == time(10, 30)

    def test_timed_events_keep_their_exact_times(self):
        body = cal("BEGIN:VEVENT\r\nDTSTART:20260710T040000Z\r\nDTEND:20260712T000000Z\r\nUID:a\r\nEND:VEVENT\r\n"
                   "BEGIN:VEVENT\r\nDTSTART;TZID=Australia/Sydney:20260720T140000\r\nDTEND;TZID=Australia/Sydney:20260721T100000\r\nUID:b\r\nEND:VEVENT\r\n"
                   "BEGIN:VEVENT\r\nDTSTART:20260730T140000\r\nDTEND:20260731T100000\r\nUID:c\r\nEND:VEVENT\r\n")
        a, b, c = parse(body)[0]
        assert a.start == datetime(2026, 7, 10, 4, 0, tzinfo=UTC) and a.end == datetime(2026, 7, 12, 0, 0, tzinfo=UTC)
        assert b.start.astimezone(SYD) == datetime(2026, 7, 20, 14, 0, tzinfo=SYD)
        assert c.start.astimezone(SYD) == datetime(2026, 7, 30, 14, 0, tzinfo=SYD)               # no zone given: the property's own time

    def test_a_single_date_without_an_end_is_one_night(self):
        body = cal("BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20260710\r\nUID:a\r\nEND:VEVENT\r\n")
        (r,), _ = parse(body)
        assert r.end.astimezone(SYD) == datetime(2026, 7, 11, 11, 0, tzinfo=SYD)

    def test_duration_is_understood(self):
        body = cal("BEGIN:VEVENT\r\nDTSTART:20260710T040000Z\r\nDURATION:P2D\r\nUID:a\r\nEND:VEVENT\r\n")
        (r,), _ = parse(body)
        assert r.end - r.start == timedelta(days=2)

    def test_cancelled_free_and_incomplete_events_are_ignored(self):
        body = cal(all_day("20260710", "20260712", "a", "STATUS:CANCELLED\r\n"), all_day("20260714", "20260716", "b", "TRANSP:TRANSPARENT\r\n"),
                   "BEGIN:VEVENT\r\nUID:c\r\nSUMMARY:no dates\r\nEND:VEVENT\r\n", all_day("20260720", "20260721", "d"))
        ranges, _ = parse(body)
        assert [r.start.astimezone(SYD).date() for r in ranges] == [date(2026, 7, 20)]

    def test_past_events_and_far_future_events_are_ignored(self):
        ranges, _ = parse(cal(all_day("20250101", "20250105"), all_day("20260610", "20260612", "x"), all_day("20300101", "20300105", "y")))
        assert [r.start.astimezone(SYD).year for r in ranges] == [2026]

    def test_overlapping_events_become_one_block_and_touching_ones_stay_apart(self):
        ranges, _ = parse(cal(all_day("20260710", "20260714", "a"), all_day("20260712", "20260716", "b"), all_day("20260716", "20260718", "c")), checkin=time(11, 0), checkout=time(11, 0))
        assert [(r.start.astimezone(SYD).day, r.end.astimezone(SYD).day) for r in ranges] == [(10, 16), (16, 18)]

    def test_repeating_events_are_flagged_as_unsupported(self):
        ranges, warnings = parse(cal(all_day("20260710", "20260711", "a", "RRULE:FREQ=WEEKLY\r\n")))
        assert len(ranges) == 1 and "not supported" in warnings[0]

    def test_unfolded_long_lines_and_an_empty_calendar_are_fine(self):
        assert parse(cal())[0] == []
        body = cal("BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20260710\r\nDTEND;VALUE=DATE:20260711\r\nUID:a\r\nSUMMARY:A very long\r\n  folded summary line\r\nEND:VEVENT\r\n")
        assert len(parse(body)[0]) == 1

    def test_garbage_is_reported_not_crashed_on(self):
        with pytest.raises(FeedError):
            parse(b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:not-a-date\r\nEND:VCALENDAR")


class TestWritingOurCalendar:
    def entries(self, *spec):
        return [FeedEntry(uid, s, e, datetime(2026, 1, 1, tzinfo=UTC)) for uid, s, e in spec]

    def read(self, body):
        return [(str(e["UID"]), e["DTSTART"].dt, e["DTEND"].dt) for e in Calendar.from_ical(body).walk("VEVENT")]

    def test_periods_are_rounded_outward_to_whole_days(self):
        body = build_feed("House", SYD, self.entries(("a@x", datetime(2026, 7, 10, 14, 0, tzinfo=SYD), datetime(2026, 7, 12, 10, 0, tzinfo=SYD))))
        assert self.read(body) == [("a@x", date(2026, 7, 10), date(2026, 7, 13))]        # includes the whole of the 12th, the day of check-out

    def test_an_end_at_midnight_does_not_add_a_day(self):
        body = build_feed("House", SYD, self.entries(("a@x", datetime(2026, 7, 10, 0, 0, tzinfo=SYD), datetime(2026, 7, 12, 0, 0, tzinfo=SYD))))
        assert self.read(body) == [("a@x", date(2026, 7, 10), date(2026, 7, 12))]

    def test_it_uses_the_property_time_zone_for_the_date(self):
        body = build_feed("House", SYD, self.entries(("a@x", datetime(2026, 7, 10, 23, 30, tzinfo=SYD).astimezone(UTC), datetime(2026, 7, 11, 9, 0, tzinfo=SYD).astimezone(UTC))))
        assert self.read(body)[0][1] == date(2026, 7, 10)                                   # 13:30 UTC on the 10th is already 23:30 on the 10th in Sydney

    def test_nothing_about_the_guest_is_included_and_the_output_is_stable(self):
        entries = self.entries(("NB-ABC@nearbnb", datetime(2026, 7, 10, 14, 0, tzinfo=SYD), datetime(2026, 7, 12, 10, 0, tzinfo=SYD)))
        body = build_feed("Beach House - Entire house", SYD, entries)
        assert b"Not available" in body and b"Beach House" in body.split(b"X-WR-CALNAME")[1][:60]
        assert build_feed("Beach House - Entire house", SYD, entries) == body
        assert b"@example.com" not in body and b"Guest" not in body


# ---------- fetching a link safely ----------

class TestLinkChecks:
    @pytest.mark.parametrize("url", ["", "ftp://example.com/a.ics", "javascript:alert(1)", "file:///etc/passwd", "https://user:pass@example.com/a.ics", "https://example.com:8080/a.ics",
                                     "https://example.com:22/a.ics", "https:///a.ics", "x" * 2001, "//example.com/a.ics"])
    def test_unacceptable_links_are_refused(self, url):
        with pytest.raises(FeedError):
            validate_url(url)

    def test_acceptable_links_are_cleaned(self):
        assert validate_url("https://www.airbnb.com.au/calendar/ical/123.ics?s=abc") == "https://www.airbnb.com.au/calendar/ical/123.ics?s=abc"
        assert validate_url("webcal://example.com/a.ics") == "https://example.com/a.ics"
        assert validate_url(" http://example.com/a.ics ") == "http://example.com/a.ics"

    @pytest.mark.parametrize("address,public", [("8.8.8.8", True), ("93.184.216.34", True), ("2606:4700:4700::1111", True), ("127.0.0.1", False), ("10.0.0.5", False), ("172.16.0.1", False),
                                                ("192.168.1.1", False), ("169.254.169.254", False), ("100.64.0.1", False), ("0.0.0.0", False), ("224.0.0.1", False),
                                                ("::1", False), ("fc00::1", False), ("fe80::1", False), ("::ffff:10.0.0.1", False), ("::ffff:127.0.0.1", False), ("::ffff:8.8.8.8", True)])
    def test_only_public_addresses_pass(self, address, public):
        assert is_public(address) is public


def resolver_returning(*addresses):
    async def resolve(host, port):
        return list(addresses)
    return resolve


class Recorder:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def transport(*responses):
    handler = Recorder(responses)
    return httpx.MockTransport(handler), handler


FEED = cal(all_day("20260710", "20260712"))


class TestFetching:
    async def test_a_name_that_resolves_to_an_internal_address_is_refused(self):
        for bad in (["127.0.0.1"], ["10.1.2.3"], ["169.254.169.254"], ["::1"], ["::ffff:10.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["192.168.0.10", "8.8.8.8"]):
            t, h = transport(httpx.Response(200, content=FEED))
            with pytest.raises(FeedError, match="not a public website"):
                await fetch_feed("https://calendar.example.com/a.ics", transport=t, resolver=resolver_returning(*bad))
            assert h.requests == []                                                            # no request was ever sent

    async def test_internal_addresses_written_directly_are_refused(self):
        for url in ("http://127.0.0.1/a.ics", "http://169.254.169.254/latest/meta-data/", "http://[::1]/a.ics", "http://10.0.0.1/a.ics", "http://0x7f000001/a.ics", "http://2130706433/a.ics"):
            t, h = transport(httpx.Response(200, content=FEED))
            with pytest.raises(FeedError):
                await fetch_feed(url, transport=t, resolver=resolver_returning("127.0.0.1"))
            assert h.requests == []

    async def test_a_name_that_cannot_be_found_is_reported(self):
        async def nothing(host, port):
            return []
        with pytest.raises(FeedError, match="could not be found"):
            await fetch_feed("https://nowhere.example/a.ics", resolver=nothing)

    async def test_the_connection_goes_to_the_address_that_was_checked_and_keeps_the_real_name(self):
        t, h = transport(httpx.Response(200, content=FEED, headers={"ETag": '"v1"', "Last-Modified": "Mon, 01 Jun 2026 00:00:00 GMT"}))
        result = await fetch_feed("https://calendar.example.com/ical/1.ics?s=abc", transport=t, resolver=resolver_returning("93.184.216.34"))
        (req,) = h.requests
        assert req.url.host == "93.184.216.34" and req.url.path == "/ical/1.ics" and req.url.query == b"s=abc"
        assert req.headers["host"] == "calendar.example.com" and req.extensions["sni_hostname"] == "calendar.example.com"
        assert result.status == 200 and result.body == FEED and result.etag == '"v1"'

    async def test_ipv6_addresses_are_bracketed(self):
        t, h = transport(httpx.Response(200, content=FEED))
        await fetch_feed("https://calendar.example.com/a.ics", transport=t, resolver=resolver_returning("2606:4700:4700::1111"))
        assert h.requests[0].url.host == "2606:4700:4700::1111"

    async def test_unchanged_feeds_are_recognised_with_the_previous_etag(self):
        t, h = transport(httpx.Response(304))
        result = await fetch_feed("https://calendar.example.com/a.ics", etag='"v1"', last_modified="x", transport=t, resolver=resolver_returning("93.184.216.34"))
        assert result.status == 304 and h.requests[0].headers["if-none-match"] == '"v1"' and h.requests[0].headers["if-modified-since"] == "x"

    async def test_a_redirect_to_an_internal_address_is_refused_and_a_normal_one_is_followed(self):
        async def two_answers(host, port):
            return ["10.0.0.9"] if host == "internal.example.com" else ["93.184.216.34"]
        t, h = transport(httpx.Response(302, headers={"location": "https://internal.example.com/secret"}))
        with pytest.raises(FeedError, match="not a public website"):
            await fetch_feed("https://calendar.example.com/a.ics", transport=t, resolver=two_answers)
        t, h = transport(httpx.Response(301, headers={"location": "/moved/b.ics"}), httpx.Response(200, content=FEED))
        assert (await fetch_feed("https://calendar.example.com/a.ics", transport=t, resolver=two_answers)).body == FEED
        assert h.requests[1].url.path == "/moved/b.ics"
        t, h = transport(httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"}))
        with pytest.raises(FeedError):
            await fetch_feed("https://calendar.example.com/a.ics", transport=t, resolver=two_answers)

    async def test_too_many_redirects_and_redirects_to_nowhere_are_refused(self):
        t, _ = transport(*[httpx.Response(302, headers={"location": "/again"}) for _ in range(6)])
        with pytest.raises(FeedError, match="too many times"):
            await fetch_feed("https://calendar.example.com/a.ics", transport=t, resolver=resolver_returning("93.184.216.34"))
        t, _ = transport(httpx.Response(302))
        with pytest.raises(FeedError, match="redirects nowhere"):
            await fetch_feed("https://calendar.example.com/a.ics", transport=t, resolver=resolver_returning("93.184.216.34"))

    async def test_errors_oversize_answers_and_non_calendars_are_refused_with_plain_messages(self):
        resolver = resolver_returning("93.184.216.34")
        t, _ = transport(httpx.Response(404))
        with pytest.raises(FeedError, match="HTTP 404"):
            await fetch_feed("https://calendar.example.com/a.ics", transport=t, resolver=resolver)
        t, _ = transport(httpx.Response(200, content=b"BEGIN:VCALENDAR\r\n" + b"x" * (2 * 1024 * 1024 + 10)))
        with pytest.raises(FeedError, match="too large"):
            await fetch_feed("https://calendar.example.com/a.ics", transport=t, resolver=resolver)
        t, _ = transport(httpx.Response(200, content=b"<html>Please log in</html>"))
        with pytest.raises(FeedError, match="did not return a calendar"):
            await fetch_feed("https://calendar.example.com/a.ics", transport=t, resolver=resolver)

    async def test_slow_and_unreachable_sites_are_reported(self):
        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)
        def down(request):
            raise httpx.ConnectError("down", request=request)
        with pytest.raises(FeedError, match="too long"):
            await fetch_feed("https://calendar.example.com/a.ics", transport=httpx.MockTransport(slow), resolver=resolver_returning("93.184.216.34"))
        with pytest.raises(FeedError, match="Could not connect"):
            await fetch_feed("https://calendar.example.com/a.ics", transport=httpx.MockTransport(down), resolver=resolver_returning("93.184.216.34"))


# ---------- the database side ----------

class Feed:
    """A stand-in for the other site: whatever `body` is, or the error in `fail`."""

    def __init__(self):
        self.body, self.fail, self.unchanged, self.calls = cal(), None, False, 0

    async def fetch(self, url, etag=None, last_modified=None):
        self.calls += 1
        if self.fail:
            raise FeedError(self.fail)
        from app.modules.ical.safe_fetch import FetchResult

        return FetchResult(304, etag=etag) if self.unchanged else FetchResult(200, self.body, '"e%d"' % self.calls)


@pytest.fixture
def feed(monkeypatch):
    f = Feed()
    monkeypatch.setattr(ical_service, "IcalAdapter", lambda: IcalAdapter(fetch=f.fetch))
    return f


async def ical_world(new_client, make_admin, *, quantity=1):
    admin = await admin_client(new_client, make_admin)
    host, host_id = await active_host(new_client, admin)
    prop = await live_listing(host, admin, quantity=quantity)
    guest = await complete_guest(new_client)
    anon = await new_client()                                                                       # a visitor, who needs no CSRF token to ask for a quote
    return type("W", (), dict(admin=admin, host=host, host_id=host_id, prop=prop, unit=prop["units"][0], guest=guest, anon=anon))


def sydney_day(days: int) -> date:
    return (datetime.now(SYD) + timedelta(days=days)).date()


def block_dates(start_days: int, end_days: int, uid="u1") -> str:
    return all_day(sydney_day(start_days).strftime("%Y%m%d"), sydney_day(end_days).strftime("%Y%m%d"), uid)


async def add_source(w, url="https://www.airbnb.com.au/calendar/ical/123.ics?s=secret", name="Airbnb", **extra):
    return await w.host.post(f"{API}/host/units/{w.unit['id']}/ical/sources", json={"name": name, "url": url, **extra}, headers=csrf_headers(w.host))


async def sync(source_id, *, now=None, manual=False):
    async with SessionLocal() as s:
        source = await ical_service.load_source(s, source_id)
        result = await ical_service.sync_source(s, source, now=now, manual=manual)
        await s.commit()
    return result


async def blocks(db, unit_id: str):
    db.expire_all()
    rows = await db.execute(text("select o.period, o.kind, o.note from room_occupancy o join rooms r on r.id = o.room_id where r.unit_id = :u and o.kind = 'external_block' order by lower(o.period)"), {"u": unit_id})
    return [(r[0].lower, r[0].upper, r[2]) for r in rows]


async def test_adding_a_calendar_blocks_its_dates_so_they_cannot_be_booked(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    feed.body = cal(block_dates(20, 22))
    r = await add_source(w)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sync"]["ok"] is True and body["sync"]["added"] == 1 and body["source"]["events_count"] == 1 and body["source"]["last_error"] is None
    (start, end, note), = await blocks(db, w.unit["id"])
    assert start.astimezone(SYD).hour == 15 and end.astimezone(SYD).hour == 11 and note == "Airbnb"
    assert (start.astimezone(SYD).date(), end.astimezone(SYD).date()) == (sydney_day(20), sydney_day(22))      # 20th 15:00 to the 22nd 11:00

    q = (await w.anon.post(f"{API}/units/{w.unit['id']}/quote", json={"checkin_local": day_local(20, 16), "hours": 24, "guests": 2})).json()
    assert q["available"] is False and "not available" in q["message"].lower()
    refused = await book(w.guest, w.unit, days=20, hour=16)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "SLOT_UNAVAILABLE"
    assert (await book(w.guest, w.unit, days=30)).status_code == 201                                 # other dates are unaffected
    days = (await w.guest.get(f"{API}/units/{w.unit['id']}/availability/calendar?month={sydney_day(21).strftime('%Y-%m')}")).json()["days"]
    assert {d["date"]: d["status"] for d in days}[sydney_day(21).isoformat()] in ("partial", "unavailable")


async def test_the_unit_page_shows_the_sources_the_link_to_share_and_the_blocks_on_the_calendar(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    feed.body = cal(block_dates(20, 22))
    await add_source(w)
    page = await get(w.host, f"/host/units/{w.unit['id']}/ical")
    assert page["eligible"] is True and page["max_sources"] == 3 and page["conflicts"] == []
    assert page["sources"][0]["name"] == "Airbnb" and page["sources"][0]["events_count"] == 1 and page["sources"][0]["checkin_time"] == "15:00"
    assert page["export_path"].startswith("/ical/") and page["export_path"].endswith(".ics") and len(page["export_path"]) > 30
    cal_entries = (await get(w.host, f"/host/units/{w.unit['id']}/calendar?from={sydney_day(15)}&to={sydney_day(30)}"))["entries"]
    assert [e["kind"] for e in cal_entries] == ["external_block"]
    removal = await w.host.delete(f"{API}/host/units/{w.unit['id']}/blocks/{cal_entries[0]['id']}", headers=csrf_headers(w.host))
    assert removal.status_code == 409 and removal.json()["error"]["code"] == "NOT_A_BLOCK"       # imported blocks are removed by removing the calendar


async def test_a_second_sync_changes_only_what_changed(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    feed.body = cal(block_dates(20, 22, "a"), block_dates(30, 32, "b"))
    sid = (await add_source(w)).json()["source"]["id"]
    again = await sync(sid)
    assert again["ok"] and again["added"] == 0 and again["removed"] == 0 and again["blocks"] == 2
    feed.body = cal(block_dates(30, 32, "b"), block_dates(40, 41, "c"))                              # one cancelled, one new
    third = await sync(sid)
    assert third["added"] == 1 and third["removed"] == 1 and third["blocks"] == 2
    assert [b[0].astimezone(SYD).date() for b in await blocks(db, w.unit["id"])] == [sydney_day(30), sydney_day(40)]
    assert (await book(w.guest, w.unit, days=20)).status_code == 201                                  # the cancelled dates are bookable again


async def test_a_calendar_that_cannot_be_read_keeps_the_blocks_it_made_and_reports_the_problem(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    feed.body = cal(block_dates(20, 22))
    sid = (await add_source(w)).json()["source"]["id"]
    feed.fail = "The other site took too long to answer."
    result = await sync(sid)
    assert result["ok"] is False and "took too long" in result["error"]
    assert len(await blocks(db, w.unit["id"])) == 1                                                  # never silently unblock dates
    page = await get(w.host, f"/host/units/{w.unit['id']}/ical")
    assert page["sources"][0]["failing"] is True and "took too long" in page["sources"][0]["last_error"] and page["sources"][0]["events_count"] == 1
    feed.fail = None
    assert (await sync(sid))["ok"] is True
    assert (await get(w.host, f"/host/units/{w.unit['id']}/ical"))["sources"][0]["failing"] is False


async def test_the_host_is_emailed_once_when_a_calendar_keeps_failing(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    feed.body = cal()
    sid = (await add_source(w)).json()["source"]["id"]
    feed.fail = "Could not connect to the other site."
    for _ in range(4):
        await sync(sid)
    assert await emails(db, "ical_sync_failing") == []
    await sync(sid)
    await sync(sid)
    mails = await emails(db, "ical_sync_failing", "host@example.com")
    assert len(mails) == 1 and "5 times" in mails[0].body_text and "Airbnb" in mails[0].body_text and "Could not connect" in mails[0].body_text
    failing = await get(w.admin, "/admin/ical")
    assert failing["failing_sources"][0]["name"] == "Airbnb" and failing["failing_sources"][0]["failures"] == 6 and failing["failing_sources"][0]["property"] == "Beach House"


# ---------- clashes with our own bookings ----------

async def confirmed_booking(w, days):
    b = (await book(w.guest, w.unit, days=days)).json()
    await w.host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(w.host))
    await dev_complete(w.guest, b["id"])
    return b


async def test_an_outside_booking_on_dates_we_already_sold_is_a_conflict_nobody_is_cancelled(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    mine = await confirmed_booking(w, 21)                                                            # 21st 14:00 to the 22nd 14:00
    feed.body = cal(block_dates(20, 24))                                                             # the other site took the 20th to the 24th
    r = await add_source(w)
    assert r.json()["sync"]["conflicts_opened"] == 1
    spans = [(b[0].astimezone(SYD), b[1].astimezone(SYD)) for b in await blocks(db, w.unit["id"])]
    assert len(spans) == 2 and spans[0][1] <= spans[1][0] and spans[0][0].date() == sydney_day(20) and spans[1][1].date() == sydney_day(24)   # only the free parts are blocked
    assert (await get(w.guest, f"/me/bookings/{mine['id']}"))["status"] == "confirmed"

    conflicts = (await get(w.host, "/host/ical/conflicts"))["items"]
    assert len(conflicts) == 1 and conflicts[0]["reference"] == mine["reference"] and conflicts[0]["source"] == "Airbnb" and conflicts[0]["status"] == "open"
    host_mail = (await emails(db, "ical_conflict_host", "host@example.com"))[0].body_text
    admin_mail = (await emails(db, "ical_conflict_admin", "admin@example.com"))[0].body_text
    assert "Nothing was cancelled" in host_mail and mine["reference"] in host_mail and mine["reference"] in admin_mail
    sid = (await get(w.host, f"/host/units/{w.unit['id']}/ical"))["sources"][0]["id"]
    await sync(sid)                                                                                  # syncing again must not email again
    assert len(await emails(db, "ical_conflict_host")) == 1 and len(await emails(db, "ical_conflict_admin")) == 1
    dash = {a["key"]: a for a in (await get(w.admin, "/admin/dashboard"))["alerts"]}["ical_conflicts"]
    assert dash["count"] == 1 and dash["href"] == "/admin/calendar" and dash["severity"] == "problem"
    admin_view = await get(w.admin, "/admin/ical")
    assert len(admin_view["conflicts"]["items"]) == 1


async def test_a_conflict_clears_itself_when_the_booking_is_cancelled_and_the_full_block_goes_in(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    mine = await confirmed_booking(w, 21)
    feed.body = cal(block_dates(20, 24))
    sid = (await add_source(w)).json()["source"]["id"]
    await w.host.post(f"{API}/host/bookings/{mine['id']}/cancel", json={"reason": "Double booked, sorry"}, headers=csrf_headers(w.host))
    result = await sync(sid)
    assert result["conflicts_opened"] == 0 and result["blocks"] == 1
    assert (await get(w.host, "/host/ical/conflicts"))["items"] == []
    resolved = (await get(w.host, "/host/ical/conflicts?status=resolved"))["items"]
    assert len(resolved) == 1 and "no longer clash" in resolved[0]["note"]


async def test_a_conflict_can_be_dismissed_by_the_host_or_an_admin_and_stays_dismissed(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin, quantity=1)
    await confirmed_booking(w, 21)
    feed.body = cal(block_dates(20, 24))
    sid = (await add_source(w)).json()["source"]["id"]
    cid = (await get(w.host, "/host/ical/conflicts"))["items"][0]["id"]
    other, _ = await active_host(new_client, w.admin, "other@example.com")
    assert (await other.post(f"{API}/host/ical/conflicts/{cid}/dismiss", json={}, headers=csrf_headers(other))).status_code == 404
    assert (await w.guest.post(f"{API}/host/ical/conflicts/{cid}/dismiss", json={}, headers=csrf_headers(w.guest))).status_code == 404
    ok = await w.host.post(f"{API}/host/ical/conflicts/{cid}/dismiss", json={"note": "Sorted it out with the guest"}, headers=csrf_headers(w.host))
    assert ok.status_code == 200
    assert (await get(w.host, "/host/ical/conflicts"))["items"] == []
    assert (await w.host.post(f"{API}/host/ical/conflicts/{cid}/dismiss", json={}, headers=csrf_headers(w.host))).status_code == 409
    await sync(sid)
    assert (await get(w.host, "/host/ical/conflicts"))["items"] == [] and len(await emails(db, "ical_conflict_host")) == 1
    assert (await get(w.admin, "/admin/ical?status=dismissed"))["conflicts"]["items"][0]["note"] == "Sorted it out with the guest"


async def test_a_pending_request_on_those_dates_is_a_conflict_too_and_clears_when_it_is_withdrawn(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    request = (await book(w.guest, w.unit, days=21)).json()                                          # only a request: a hold
    feed.body = cal(block_dates(20, 24))
    sid = (await add_source(w)).json()["source"]["id"]
    assert len((await get(w.host, "/host/ical/conflicts"))["items"]) == 1
    await w.guest.post(f"{API}/me/bookings/{request['id']}/withdraw", headers=csrf_headers(w.guest))
    feed.unchanged = True                                                                            # the other site's file has not changed at all...
    result = await sync(sid)
    assert result["unchanged"] is True and result["blocks"] == 1                                     # ...but the dates are fully blocked now
    assert (await get(w.host, "/host/ical/conflicts"))["items"] == []


async def test_dates_the_host_blocked_or_another_calendar_blocked_are_not_clashes(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    room = w.unit["rooms"][0]["id"]
    await occupy(db, room, day_local(21, 12), day_local(22, 12), kind="host_block")
    feed.body = cal(block_dates(20, 24))
    r = await add_source(w)
    assert r.json()["sync"]["conflicts_opened"] == 0
    assert len(await blocks(db, w.unit["id"])) == 2                                                  # the host's own block is left alone
    second = await add_source(w, url="https://www.booking.com/ical/456.ics", name="Booking.com")
    assert second.json()["sync"]["conflicts_opened"] == 0 and second.json()["sync"]["blocks"] == 0   # everything is already blocked
    assert (await get(w.host, "/host/ical/conflicts"))["items"] == []


async def test_hotel_units_with_several_rooms_cannot_use_calendar_sync(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin, quantity=2)
    page = await get(w.host, f"/host/units/{w.unit['id']}/ical")
    assert page["eligible"] is False and page["export_path"] is None and "single room" in page["reason"]
    r = await add_source(w)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ELIGIBLE"
    rotate = await w.host.post(f"{API}/host/units/{w.unit['id']}/ical/export/rotate", headers=csrf_headers(w.host))
    assert rotate.status_code == 409


# ---------- adding and editing a link ----------

async def test_links_that_are_unsafe_or_ours_are_refused(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    for url in ("ftp://example.com/a.ics", "https://user:pw@example.com/a.ics", "https://example.com:8080/a.ics", "not a link"):
        r = await add_source(w, url=url)
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CALENDAR_LINK", url
    page = await get(w.host, f"/host/units/{w.unit['id']}/ical")
    own = await add_source(w, url=f"http://test/api/v1{page['export_path']}", name="Echo")
    assert own.status_code == 422 and own.json()["error"]["code"] == "OWN_CALENDAR_LINK"
    assert (await add_source(w, name="  ")).status_code == 422
    assert (await add_source(w, checkin_time="15:07")).status_code == 422


async def test_a_unit_can_have_a_few_calendars_but_not_more(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    for i in range(3):
        assert (await add_source(w, url=f"https://example.com/{i}.ics", name=f"Site {i}")).status_code == 201
    r = await add_source(w, url="https://example.com/4.ics", name="One too many")
    assert r.status_code == 409 and r.json()["error"]["code"] == "TOO_MANY_SOURCES"


async def test_only_the_owner_can_see_or_change_a_units_calendar(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    sid = (await add_source(w)).json()["source"]["id"]
    other, _ = await active_host(new_client, w.admin, "other@example.com")
    h = csrf_headers(other)
    assert (await other.get(f"{API}/host/units/{w.unit['id']}/ical")).status_code == 404
    assert (await other.post(f"{API}/host/units/{w.unit['id']}/ical/sources", json={"name": "x", "url": "https://example.com/a.ics"}, headers=h)).status_code == 404
    assert (await other.patch(f"{API}/host/ical/sources/{sid}", json={"name": "mine now"}, headers=h)).status_code == 404
    assert (await other.delete(f"{API}/host/ical/sources/{sid}", headers=h)).status_code == 404
    assert (await other.post(f"{API}/host/ical/sources/{sid}/sync", headers=h)).status_code == 404
    assert (await other.post(f"{API}/host/units/{w.unit['id']}/ical/export/rotate", headers=h)).status_code == 404
    assert (await w.guest.get(f"{API}/host/units/{w.unit['id']}/ical")).status_code == 404
    assert (await (await new_client()).get(f"{API}/host/units/{w.unit['id']}/ical")).status_code in (401, 403)
    assert (await w.host.get(f"{API}/admin/ical")).status_code == 403


async def test_editing_a_calendar_applies_new_times_and_a_new_link_and_can_pause_it(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    feed.body = cal(block_dates(20, 22))
    sid = (await add_source(w)).json()["source"]["id"]
    h = csrf_headers(w.host)
    r = await w.host.patch(f"{API}/host/ical/sources/{sid}", json={"checkin_time": "14:00", "checkout_time": "10:00", "name": "Airbnb (house)"}, headers=h)
    assert r.status_code == 200 and r.json()["checkin_time"] == "14:00" and r.json()["name"] == "Airbnb (house)"
    later = await sync(sid, now=datetime.now(UTC) + timedelta(minutes=2), manual=True)
    assert later["ok"] and later["added"] == 1 and later["removed"] == 1
    (start, end, _), = await blocks(db, w.unit["id"])
    assert start.astimezone(SYD).time() == time(14, 0) and end.astimezone(SYD).time() == time(10, 0)
    assert (await w.host.patch(f"{API}/host/ical/sources/{sid}", json={"url": "ftp://x"}, headers=h)).status_code == 422
    assert (await w.host.patch(f"{API}/host/ical/sources/{sid}", json={"name": " "}, headers=h)).status_code == 422
    paused = await w.host.patch(f"{API}/host/ical/sources/{sid}", json={"active": False}, headers=h)
    assert paused.json()["active"] is False


async def test_removing_a_calendar_removes_its_blocks_and_conflicts_at_once(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    await confirmed_booking(w, 21)
    feed.body = cal(block_dates(20, 24))
    sid = (await add_source(w)).json()["source"]["id"]
    assert len(await blocks(db, w.unit["id"])) == 2
    r = await w.host.delete(f"{API}/host/ical/sources/{sid}", headers=csrf_headers(w.host))
    assert r.status_code == 200
    assert await blocks(db, w.unit["id"]) == []
    assert (await get(w.host, "/host/ical/conflicts?status=open"))["items"] == []
    assert (await db.execute(select(IcalConflict))).scalars().all() == []
    assert (await book(w.guest, w.unit, days=30)).status_code == 201


# ---------- keeping sources up to date ----------

async def test_the_scheduled_job_syncs_each_source_on_the_configured_interval(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    feed.body = cal(block_dates(20, 22))
    sid = (await add_source(w, sync_now=False)).json()["source"]["id"]
    t0 = datetime.now(UTC)
    async def run(now):
        async with SessionLocal() as s:
            counts = await ical_service.dispatch_due(s, now=now)
            await s.commit()
        return counts
    assert (await run(t0)) == {"synced": 1, "failed": 0}                                             # never synced yet: picked up straight away
    assert (await run(t0 + timedelta(minutes=10))) == {"synced": 0, "failed": 0}                     # 15 minute default not yet over
    feed.body = cal(block_dates(20, 22, "a"), block_dates(40, 42, "b"))
    assert (await run(t0 + timedelta(minutes=16))) == {"synced": 1, "failed": 0}
    assert len(await blocks(db, w.unit["id"])) == 2
    await w.admin.put(f"{API}/admin/settings/ical.poll_minutes", json={"value": 5, "reason": "test"}, headers=csrf_headers(w.admin))
    assert (await run(t0 + timedelta(minutes=22))) == {"synced": 1, "failed": 0}
    await w.host.patch(f"{API}/host/ical/sources/{sid}", json={"active": False}, headers=csrf_headers(w.host))
    assert (await run(t0 + timedelta(minutes=60))) == {"synced": 0, "failed": 0}                     # paused calendars are left alone


async def test_a_booking_made_on_the_other_site_appears_within_15_minutes_and_cannot_be_booked_here(new_client, make_admin, db, feed):
    """The phase's exit check."""
    w = await ical_world(new_client, make_admin)
    feed.body = cal()
    await add_source(w)
    assert (await w.anon.post(f"{API}/units/{w.unit['id']}/quote", json={"checkin_local": day_local(25, 16), "hours": 24, "guests": 2})).json()["available"] is True
    feed.body = cal(block_dates(25, 27))                                                             # a guest books on the other site
    async with SessionLocal() as s:
        await ical_service.dispatch_due(s, now=datetime.now(UTC) + timedelta(minutes=16))
        await s.commit()
    quote = (await w.anon.post(f"{API}/units/{w.unit['id']}/quote", json={"checkin_local": day_local(25, 16), "hours": 24, "guests": 2})).json()
    assert quote["available"] is False
    refused = await book(w.guest, w.unit, days=25, hour=16)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "SLOT_UNAVAILABLE"


async def test_sync_now_is_limited_to_once_a_minute_and_admins_can_trigger_it_too(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    feed.body = cal(block_dates(20, 22))
    sid = (await add_source(w)).json()["source"]["id"]
    first = await w.host.post(f"{API}/host/ical/sources/{sid}/sync", headers=csrf_headers(w.host))
    assert first.status_code == 429 and first.json()["error"]["code"] == "SYNC_TOO_SOON"
    async with SessionLocal() as s:
        await s.execute(update(IcalSource).values(last_attempt_at=datetime.now(UTC) - timedelta(minutes=2)))
        await s.commit()
    ok = await w.host.post(f"{API}/host/ical/sources/{sid}/sync", headers=csrf_headers(w.host))
    assert ok.status_code == 200 and ok.json()["sync"]["ok"] is True
    async with SessionLocal() as s:
        await s.execute(update(IcalSource).values(last_attempt_at=datetime.now(UTC) - timedelta(minutes=2)))
        await s.commit()
    assert (await w.admin.post(f"{API}/admin/ical/sources/{sid}/sync", headers=csrf_headers(w.admin))).status_code == 200
    assert (await w.host.post(f"{API}/admin/ical/sources/{sid}/sync", headers=csrf_headers(w.host))).status_code == 403


def test_the_sync_job_is_scheduled():
    entry = celery_app.conf.beat_schedule["sync-calendars"]
    assert entry["task"] == "ical.dispatch" and entry["schedule"] == 300.0
    import app.jobs.tasks  # noqa: F401  (the worker loads this module at start; importing it registers the tasks)

    assert celery_app.tasks["ical.dispatch"]


# ---------- our calendar for the other site ----------

async def test_the_export_feed_lists_confirmed_bookings_and_host_blocks_rounded_outward_and_nothing_else(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    mine = await confirmed_booking(w, 21)                                                            # 21st 14:00 to the 22nd 14:00
    pending = (await book(w.guest, w.unit, days=30)).json()                                           # only a request: not exported
    await occupy(db, w.unit["rooms"][0]["id"], day_local(40, 12), day_local(42, 12), kind="host_block")
    feed.body = cal(block_dates(50, 52))
    await add_source(w)                                                                              # imported blocks must NOT be exported back
    page = await get(w.host, f"/host/units/{w.unit['id']}/ical")
    anon = await new_client()
    r = await anon.get(f"{API}{page['export_path']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar") and r.headers["etag"] and "private" in r.headers["cache-control"]
    events = [(str(e["UID"]), e["DTSTART"].dt, e["DTEND"].dt, str(e["SUMMARY"])) for e in Calendar.from_ical(r.content).walk("VEVENT")]
    assert len(events) == 2
    booking = next(e for e in events if e[0] == f"{mine['reference']}@nearbnb")
    assert booking[1] == sydney_day(21) and booking[2] == sydney_day(23) and booking[3] == "Not available"      # 21st and the whole of the 22nd
    block = next(e for e in events if e[0].startswith("block-"))
    assert block[1] == sydney_day(40) and block[2] == sydney_day(43)
    assert pending["reference"].encode() not in r.content and b"Test Guest" not in r.content and b"guest@example.com" not in r.content and b"Airbnb" not in r.content


async def test_cancelled_bookings_and_other_units_do_not_appear_in_the_feed(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    mine = await confirmed_booking(w, 21)
    other_prop = await live_listing(w.host, w.admin, name="Second House")
    other_unit = other_prop["units"][0]
    guest2 = await complete_guest(new_client, "g2@example.com")
    b2 = (await book(guest2, other_unit, days=21)).json()
    await w.host.post(f"{API}/host/bookings/{b2['id']}/accept", headers=csrf_headers(w.host))
    await dev_complete(guest2, b2["id"])
    path = (await get(w.host, f"/host/units/{w.unit['id']}/ical"))["export_path"]
    anon = await new_client()
    assert b2["reference"].encode() not in (await anon.get(f"{API}{path}")).content
    await w.host.post(f"{API}/host/bookings/{mine['id']}/cancel", json={"reason": "x"}, headers=csrf_headers(w.host))
    assert mine["reference"].encode() not in (await anon.get(f"{API}{path}")).content


async def test_the_feed_needs_the_exact_secret_link_and_supports_conditional_requests(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    path = (await get(w.host, f"/host/units/{w.unit['id']}/ical"))["export_path"]
    anon = await new_client()
    ok = await anon.get(f"{API}{path}")
    assert ok.status_code == 200
    wrong_last = "Y" if path[-5] == "X" else "X"                 # a random secret can already end in X, so pick a character that differs
    for bad in (path[:-5] + wrong_last + ".ics", path.replace(".ics", ""), "/ical/short.ics", "/ical/" + "a" * 24 + ".ics", path + "x"):
        assert (await anon.get(f"{API}{bad}")).status_code == 404, bad
    cached = await anon.get(f"{API}{path}", headers={"If-None-Match": ok.headers["etag"]})
    assert cached.status_code == 304 and not cached.content
    assert (await anon.get(f"{API}{path}", headers={"If-None-Match": '"different"'})).status_code == 200


async def test_the_link_can_be_replaced_and_the_old_one_stops_working(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    old = (await get(w.host, f"/host/units/{w.unit['id']}/ical"))["export_path"]
    anon = await new_client()
    assert (await anon.get(f"{API}{old}")).status_code == 200
    new = (await w.host.post(f"{API}/host/units/{w.unit['id']}/ical/export/rotate", headers=csrf_headers(w.host))).json()["export_path"]
    assert new != old
    assert (await anon.get(f"{API}{old}")).status_code == 404 and (await anon.get(f"{API}{new}")).status_code == 200
    assert (await get(w.host, f"/host/units/{w.unit['id']}/ical"))["export_path"] == new


async def test_the_feed_is_stable_when_nothing_changed_and_changes_when_a_booking_does(new_client, make_admin, db, feed):
    w = await ical_world(new_client, make_admin)
    path = (await get(w.host, f"/host/units/{w.unit['id']}/ical"))["export_path"]
    anon = await new_client()
    first = await anon.get(f"{API}{path}")
    assert (await anon.get(f"{API}{path}")).headers["etag"] == first.headers["etag"]
    await confirmed_booking(w, 21)
    assert (await anon.get(f"{API}{path}")).headers["etag"] != first.headers["etag"]
