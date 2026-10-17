import uuid
from datetime import UTC, datetime, timedelta

from tests.conftest import csrf_headers
from tests.helpers import API, active_host, admin_client, day_local, live_listing, occupy


async def world(new_client, make_admin, **listing):
    admin = await admin_client(new_client, make_admin)
    host, host_id = await active_host(new_client, admin)
    prop = await live_listing(host, admin, **listing)
    anon = await new_client()
    return admin, host, host_id, prop, anon, prop["units"][0]


def quote_body(days=10, hour=14, hours=24, guests=2):
    return {"checkin_local": day_local(days, hour), "hours": hours, "guests": guests}


async def quote(anon, unit, **kw):
    r = await anon.post(f"{API}/units/{unit['id']}/quote", json=quote_body(**kw))
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_free_period_is_available_with_the_full_price_breakdown(new_client, make_admin):
    _, _, _, _, anon, unit = await world(new_client, make_admin)
    q = await quote(anon, unit)
    assert q["available"] is True and q["rooms_free"] == 1 and q["timezone"] == "Australia/Sydney"
    assert q["checkin_local"] == day_local(10) and q["checkout_local"] == day_local(11)
    assert q["price"]["accommodation"] == 25000 and q["price"]["service_fee"] == 1875
    assert q["price"]["tax"] == 2688 and q["price"]["total"] == 29563 and q["currency"] == "AUD"
    for hidden in ("commission", "host_payable", "commission_percent"):
        assert hidden not in q["price"]
    two = await quote(anon, unit, hours=48)
    assert two["price"]["accommodation"] == 50000 and two["price"]["nights"] == 2 and two["checkout_local"] == day_local(12)


async def test_a_booked_period_says_who_is_staying_and_offers_other_times(new_client, make_admin, db):
    _, _, _, _, anon, unit = await world(new_client, make_admin)
    await occupy(db, unit["rooms"][0]["id"], day_local(10), day_local(11))
    q = await quote(anon, unit)
    assert q["available"] is False and q["code"] == "SLOT_UNAVAILABLE"
    assert q["message"].startswith("Someone is staying from ") and " until " in q["message"]
    assert len(q["alternatives"]) == 3
    assert q["alternatives"][0]["checkin_local"] == day_local(11)  # first moment a full 24h stay fits
    assert q["alternatives"][0]["checkout_local"] == day_local(12)
    days = {a["checkin_local"][:10] for a in q["alternatives"]}
    assert len(days) == 3  # one suggestion per day


async def test_a_host_block_is_reported_without_saying_why(new_client, make_admin, db):
    _, _, _, _, anon, unit = await world(new_client, make_admin)
    await occupy(db, unit["rooms"][0]["id"], day_local(10), day_local(12), kind="host_block")
    q = await quote(anon, unit)
    assert q["available"] is False and "Someone is staying" not in q["message"] and "not available" in q["message"]


async def test_back_to_back_stays_are_allowed(new_client, make_admin, db):
    _, _, _, _, anon, unit = await world(new_client, make_admin)
    await occupy(db, unit["rooms"][0]["id"], day_local(10), day_local(11))
    assert (await quote(anon, unit, days=11))["available"] is True   # starts exactly when the other guest leaves
    assert (await quote(anon, unit, days=9))["available"] is True    # ends exactly when the other guest arrives


async def test_every_rule_has_its_own_clear_message(new_client, make_admin):
    _, _, _, _, anon, unit = await world(new_client, make_admin)
    cases = [
        (dict(hours=36), "INVALID_DURATION"),
        (dict(hours=24 * 8), "STAY_TOO_LONG"),
        (dict(guests=5), "OVER_CAPACITY"),
        (dict(hour=23), "OUTSIDE_CHECKIN_HOURS"),
        (dict(hour=6), "OUTSIDE_CHECKIN_HOURS"),
        (dict(days=-1), "IN_THE_PAST"),
        (dict(days=400), "TOO_FAR_AHEAD"),
    ]
    for kw, code in cases:
        q = await quote(anon, unit, **kw)
        assert q["available"] is False and q["code"] == code and q["message"], (kw, q)
    off_step = await anon.post(f"{API}/units/{unit['id']}/quote", json={"checkin_local": day_local(10)[:-2] + "30", "hours": 24, "guests": 1})
    assert off_step.json()["code"] == "INVALID_TIME_STEP"


async def test_lead_time_is_a_per_host_setting(new_client, make_admin, db):
    admin, _, host_id, _, anon, unit = await world(new_client, make_admin)
    assert (await quote(anon, unit, days=5))["available"] is True
    r = await admin.put(f"{API}/admin/hosts/{host_id}/overrides/booking.min_lead_time_hours", json={"value": 24 * 30}, headers=csrf_headers(admin))
    assert r.status_code == 200
    q = await quote(anon, unit, days=5)
    assert q["code"] == "LEAD_TIME_VIOLATION" and "720 hour" in q["message"]


async def test_check_in_interval_follows_the_admin_setting(new_client, make_admin):
    admin, _, _, _, anon, unit = await world(new_client, make_admin)
    half_hour = {"checkin_local": day_local(10)[:-2] + "30", "hours": 24, "guests": 1}
    assert (await anon.post(f"{API}/units/{unit['id']}/quote", json=half_hour)).json()["code"] == "INVALID_TIME_STEP"
    await admin.put(f"{API}/admin/settings/checkin.interval_minutes", json={"value": 30}, headers=csrf_headers(admin))
    assert (await anon.post(f"{API}/units/{unit['id']}/quote", json=half_hour)).json()["available"] is True


async def test_malformed_input_is_rejected(new_client, make_admin):
    _, _, _, _, anon, unit = await world(new_client, make_admin)
    for bad in ("tomorrow", "2026-13-45T10:00", day_local(10) + "+10:00"):
        r = await anon.post(f"{API}/units/{unit['id']}/quote", json={"checkin_local": bad, "hours": 24, "guests": 1})
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_DATETIME", bad
    assert (await anon.post(f"{API}/units/{unit['id']}/quote", json={"checkin_local": day_local(10), "hours": 0, "guests": 1})).status_code == 422
    assert (await anon.post(f"{API}/units/not-a-uuid/quote", json=quote_body())).status_code == 404
    assert (await anon.post(f"{API}/units/{uuid.uuid4()}/quote", json=quote_body())).status_code == 404


async def test_unpublished_paused_or_suspended_places_cannot_be_quoted(new_client, make_admin):
    admin, host, host_id, prop, anon, unit = await world(new_client, make_admin)
    h = csrf_headers(host)
    await host.post(f"{API}/host/units/{unit['id']}/pause", headers=h)
    assert (await anon.post(f"{API}/units/{unit['id']}/quote", json=quote_body())).status_code == 404
    await host.post(f"{API}/host/units/{unit['id']}/resume", headers=h)
    assert (await anon.post(f"{API}/units/{unit['id']}/quote", json=quote_body())).status_code == 200
    await admin.post(f"{API}/admin/hosts/{host_id}/suspend", json={"reason": "check"}, headers=csrf_headers(admin))
    assert (await anon.post(f"{API}/units/{unit['id']}/quote", json=quote_body())).status_code == 404
    assert (await anon.get(f"{API}/units/{unit['id']}/availability/day", params={"date": day_local(10)[:10]})).status_code == 404


async def test_hotel_rooms_a_stay_needs_one_room_free_the_whole_time(new_client, make_admin, db):
    _, _, _, _, anon, unit = await world(new_client, make_admin, quantity=2)
    room_a, room_b = [r["id"] for r in unit["rooms"]]
    await occupy(db, room_a, day_local(10, 14), day_local(11, 14))
    q = await quote(anon, unit)
    assert q["available"] is True and q["rooms_free"] == 1   # the other room is free

    await occupy(db, room_b, day_local(10, 14), day_local(11, 14))
    q = await quote(anon, unit)
    assert q["available"] is False and q["message"].startswith("Every room is taken from ")


async def test_fragmented_hotel_availability_is_explained(new_client, make_admin, db):
    """Room A is busy early, room B late: the hotel is never sold out, but no single room fits the whole stay."""
    _, _, _, _, anon, unit = await world(new_client, make_admin, quantity=2)
    room_a, room_b = [r["id"] for r in unit["rooms"]]
    await occupy(db, room_a, day_local(10, 0), day_local(10, 18))
    await occupy(db, room_b, day_local(10, 18), day_local(11, 18))
    q = await quote(anon, unit, days=10, hour=12)
    assert q["available"] is False and q["message"] == "No single room is free for the whole period you chose."


async def test_month_calendar_shows_available_partial_and_unavailable_days(new_client, make_admin, db):
    _, _, _, _, anon, unit = await world(new_client, make_admin)
    room = unit["rooms"][0]["id"]
    await occupy(db, room, day_local(10, 14), day_local(11, 14), kind="host_block")      # partly covers two days
    await occupy(db, room, day_local(20, 0), day_local(22, 0), kind="host_block")        # covers two whole days

    async def status_of(days_ahead: int) -> str:
        date = day_local(days_ahead)[:10]
        r = await anon.get(f"{API}/units/{unit['id']}/availability/calendar", params={"month": date[:7]})
        assert r.status_code == 200
        return next(d["status"] for d in r.json()["days"] if d["date"] == date)

    assert await status_of(9) == "available"
    assert await status_of(10) == "partial"
    assert await status_of(11) == "partial"
    assert await status_of(12) == "available"
    assert await status_of(20) == "unavailable"
    assert await status_of(21) == "unavailable"
    assert await status_of(22) == "available"
    assert await status_of(-1) == "past"
    assert await status_of(400) == "closed"


async def test_month_calendar_input_validation(new_client, make_admin):
    _, _, _, _, anon, unit = await world(new_client, make_admin)
    for bad in ("2026", "2026-13", "nope", "1999-01"):
        assert (await anon.get(f"{API}/units/{unit['id']}/availability/calendar", params={"month": bad})).status_code == 422


async def test_day_timeline_lists_when_the_place_is_taken(new_client, make_admin, db):
    _, _, _, _, anon, unit = await world(new_client, make_admin)
    room = unit["rooms"][0]["id"]
    await occupy(db, room, day_local(15, 14), day_local(16, 14), kind="booking")
    await occupy(db, room, day_local(18, 10), day_local(18, 12), kind="host_block")

    t = (await anon.get(f"{API}/units/{unit['id']}/availability/day", params={"date": day_local(15)[:10]})).json()
    assert len(t["occupied"]) == 1 and t["occupied"][0]["kind"] == "booked"
    assert t["occupied"][0]["start_local"] == day_local(15, 14) and t["occupied"][0]["end_local"] == day_local(16, 14)
    assert t["occupied"][0]["label"].startswith("Someone is staying from ") and "2:00 PM" in t["occupied"][0]["label"]

    # the next day shows the same stay's tail, with its full range for the message
    t = (await anon.get(f"{API}/units/{unit['id']}/availability/day", params={"date": day_local(16)[:10]})).json()
    assert t["occupied"][0]["start_local"] == day_local(15, 14)

    t = (await anon.get(f"{API}/units/{unit['id']}/availability/day", params={"date": day_local(18)[:10]})).json()
    assert t["occupied"][0]["kind"] == "unavailable" and t["occupied"][0]["label"].startswith("Not available from ")
    assert "Someone" not in t["occupied"][0]["label"]  # the host's reason (a private block) is never revealed

    assert (await anon.get(f"{API}/units/{unit['id']}/availability/day", params={"date": day_local(25)[:10]})).json()["occupied"] == []
    assert (await anon.get(f"{API}/units/{unit['id']}/availability/day", params={"date": "31-12-2026"})).status_code == 422


async def test_timeline_for_a_hotel_only_shows_sold_out_periods(new_client, make_admin, db):
    _, _, _, _, anon, unit = await world(new_client, make_admin, quantity=2)
    room_a, room_b = [r["id"] for r in unit["rooms"]]
    await occupy(db, room_a, day_local(10, 10), day_local(10, 20))
    t = (await anon.get(f"{API}/units/{unit['id']}/availability/day", params={"date": day_local(10)[:10]})).json()
    assert t["occupied"] == [] and t["rooms"] == 2            # one room still free all day
    await occupy(db, room_b, day_local(10, 14), day_local(10, 16))
    t = (await anon.get(f"{API}/units/{unit['id']}/availability/day", params={"date": day_local(10)[:10]})).json()
    assert [(o["start_local"], o["end_local"]) for o in t["occupied"]] == [(day_local(10, 14), day_local(10, 16))]


async def test_expired_holds_do_not_block_anyone(new_client, make_admin, db):
    """A pending request's hold frees itself when it expires, even before the cleanup job runs."""
    from sqlalchemy.dialects.postgresql import Range

    from app.modules.properties.models import RoomOccupancy
    from tests.helpers import to_utc_range

    _, _, _, _, anon, unit = await world(new_client, make_admin)
    start, end = to_utc_range(day_local(10), day_local(11))
    db.add(RoomOccupancy(room_id=uuid.UUID(unit["rooms"][0]["id"]), period=Range(start, end, bounds="[)"), kind="hold",
                         expires_at=datetime.now(UTC) + timedelta(minutes=20)))
    await db.commit()
    assert (await quote(anon, unit))["available"] is False   # live hold blocks
    from sqlalchemy import update

    await db.execute(update(RoomOccupancy).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    await db.commit()
    assert (await quote(anon, unit))["available"] is True    # expired hold ignored


async def test_property_page_carries_the_booking_rules_for_each_unit(new_client, make_admin):
    admin, _, host_id, prop, anon, unit = await world(new_client, make_admin)
    page = (await anon.get(f"{API}/properties/{prop['id']}")).json()
    rules = page["units"][0]["rules"]
    assert rules == {"interval_minutes": 60, "step_hours": 24, "max_stay_hours": 168, "min_lead_hours": 2, "max_advance_days": 180}
    await admin.put(f"{API}/admin/hosts/{host_id}/overrides/booking.max_advance_days", json={"value": 30}, headers=csrf_headers(admin))
    assert (await anon.get(f"{API}/properties/{prop['id']}")).json()["units"][0]["rules"]["max_advance_days"] == 30


async def test_booking_meta_reflects_admin_settings(new_client, make_admin):
    admin = await admin_client(new_client, make_admin)
    anon = await new_client()
    assert (await anon.get(f"{API}/meta/booking")).json()["interval_minutes"] == 60
    await admin.put(f"{API}/admin/settings/booking.max_stay_hours", json={"value": 240}, headers=csrf_headers(admin))
    assert (await anon.get(f"{API}/meta/booking")).json()["max_stay_hours"] == 240
