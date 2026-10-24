"""Units can run on flexible timing (any hour in a window, whole 24-hour blocks, as before) or fixed timing
(the host sets one check-in time and one check-out time; guests pick only the date and the nights)."""
from tests.conftest import csrf_headers
from tests.helpers import API, day_local, occupy
from tests.test_availability import quote, world
from tests.test_bookings import book, complete_guest


async def set_timing(host, unit, **fields):
    return await host.patch(f"{API}/host/units/{unit['id']}", json=fields, headers=csrf_headers(host))


FIXED = {"stay_mode": "fixed", "fixed_checkin_time": "12:00", "fixed_checkout_time": "11:30"}


# ---------- nothing changes for flexible units ----------

async def test_every_unit_starts_flexible_and_shows_no_fixed_times(new_client, make_admin):
    _, _, _, prop, anon, unit = await world(new_client, make_admin)
    assert unit["stay_mode"] == "flexible" and unit["fixed_checkin_time"] is None and unit["fixed_checkout_time"] is None
    public = (await anon.get(f"{API}/properties/{prop['id']}")).json()["units"][0]
    assert public["stay_mode"] == "flexible" and (public["checkin_from"], public["checkin_to"]) == ("08:00", "22:00")


async def test_a_flexible_unit_still_needs_an_hour_inside_its_window_and_stays_in_whole_days(new_client, make_admin):
    _, _, _, _, anon, unit = await world(new_client, make_admin)
    late = await anon.post(f"{API}/units/{unit['id']}/quote", json={"checkin_local": day_local(10, 3), "hours": 24, "guests": 2})
    assert late.json()["available"] is False and late.json()["code"] == "OUTSIDE_CHECKIN_HOURS"
    ok = await quote(anon, unit, days=10, hour=14, hours=48)
    assert ok["checkin_local"] == day_local(10, 14) and ok["checkout_local"] == day_local(12, 14)      # same time of day, 48 hours later


async def test_storing_fixed_times_on_a_flexible_unit_changes_nothing_for_guests(new_client, make_admin):
    _, host, _, _, anon, unit = await world(new_client, make_admin)
    assert (await set_timing(host, unit, fixed_checkin_time="12:00", fixed_checkout_time="11:30")).status_code == 200
    q = await quote(anon, unit, days=10, hour=14)
    assert q["checkin_local"] == day_local(10, 14) and q["checkout_local"] == day_local(11, 14)


# ---------- fixed timing ----------

async def test_a_fixed_unit_uses_its_own_check_in_and_check_out_times_and_prices_per_night(new_client, make_admin):
    _, host, _, _, anon, unit = await world(new_client, make_admin)
    r = await set_timing(host, unit, **FIXED)
    assert r.status_code == 200, r.text
    one = await quote(anon, unit, days=10, hour=14, hours=24)                      # the hour the guest sent is ignored
    assert one["available"] is True and one["stay_mode"] == "fixed" and one["nights"] == 1
    assert one["checkin_local"] == day_local(10, 12) and one["checkout_local"] == day_local(11, 11).replace("T11:00", "T11:30")
    assert one["price"]["accommodation"] == 25000 and one["price"]["nights"] == 1
    two = await quote(anon, unit, days=10, hour=14, hours=48)
    assert two["checkout_local"] == day_local(12, 11).replace("T11:00", "T11:30") and two["price"]["accommodation"] == 50000 and two["nights"] == 2
    assert two["checkin_label"].endswith("12:00 PM") and two["checkout_label"].endswith("11:30 AM")


async def test_a_fixed_unit_does_not_care_what_hour_the_guest_picked_even_outside_the_old_window(new_client, make_admin):
    _, host, _, _, anon, unit = await world(new_client, make_admin)
    await set_timing(host, unit, **FIXED)
    q = await anon.post(f"{API}/units/{unit['id']}/quote", json={"checkin_local": day_local(10, 3), "hours": 24, "guests": 2})
    assert q.json()["available"] is True and q.json()["checkin_local"] == day_local(10, 12)


async def test_a_fixed_stay_is_still_counted_in_whole_nights(new_client, make_admin):
    _, host, _, _, anon, unit = await world(new_client, make_admin)
    await set_timing(host, unit, **FIXED)
    r = await anon.post(f"{API}/units/{unit['id']}/quote", json={"checkin_local": day_local(10, 12), "hours": 36, "guests": 2})
    assert r.json()["available"] is False and r.json()["code"] == "INVALID_DURATION"


async def test_a_booking_on_a_fixed_unit_holds_exactly_the_fixed_times(new_client, make_admin, db):
    _, host, _, _, anon, unit = await world(new_client, make_admin)
    await set_timing(host, unit, **FIXED)
    guest = await complete_guest(new_client)
    created = (await book(guest, unit, days=10, hour=3, hours=48)).json()
    mine = (await guest.get(f"{API}/me/bookings/{created['id']}")).json()
    assert mine["check_in_label"].endswith("12:00 PM") and mine["check_out_label"].endswith("11:30 AM")
    assert mine["hours"] == 48 and mine["price"]["nights"] == 2 and mine["price"]["accommodation"] == 50000


async def test_the_next_guest_can_arrive_after_check_out_on_the_same_day(new_client, make_admin, db):
    _, host, _, _, anon, unit = await world(new_client, make_admin)
    await set_timing(host, unit, **FIXED)
    guest = await complete_guest(new_client)
    assert (await book(guest, unit, days=10, hours=24)).status_code == 201          # 12:00 on day 10 to 11:30 on day 11
    same = await anon.post(f"{API}/units/{unit['id']}/quote", json={"checkin_local": day_local(10, 12), "hours": 24, "guests": 2})
    assert same.json()["available"] is False and same.json()["code"] == "SLOT_UNAVAILABLE"
    next_day = await quote(anon, unit, days=11, hours=24)                           # check-in 12:00 on day 11 is after the 11:30 check-out
    assert next_day["available"] is True and next_day["checkin_local"] == day_local(11, 12)


async def test_other_dates_offered_when_taken_keep_the_fixed_times(new_client, make_admin, db):
    _, host, _, _, anon, unit = await world(new_client, make_admin)
    await set_timing(host, unit, **FIXED)
    await occupy(db, unit["rooms"][0]["id"], day_local(10, 12), day_local(12, 12))
    q = await anon.post(f"{API}/units/{unit['id']}/quote", json={"checkin_local": day_local(10, 12), "hours": 24, "guests": 2})
    alts = q.json()["alternatives"]
    assert q.json()["available"] is False and len(alts) == 3
    assert all(a["checkin_local"].endswith("T12:00") and a["checkout_local"].endswith("T11:30") for a in alts)
    assert len({a["checkin_local"][:10] for a in alts}) == 3


# ---------- search ----------

async def test_search_uses_the_fixed_times_of_a_fixed_unit(new_client, make_admin):
    _, host, _, _, anon, unit = await world(new_client, make_admin)
    url = f"{API}/search?checkin={day_local(10, 3)}&hours=24&guests=2"
    assert (await anon.get(url)).json()["total"] == 0                               # 3 am is outside a flexible unit's check-in window
    await set_timing(host, unit, **FIXED)
    found = (await anon.get(url)).json()
    assert found["total"] == 1                                                      # a fixed unit only needs the date
    assert found["items"][0]["units"][0]["stay_mode"] == "fixed"                    # so the result card can say "per night"


# ---------- rules for changing timing ----------

async def test_fixed_timing_needs_both_times_and_a_known_mode(new_client, make_admin):
    _, host, _, _, _, unit = await world(new_client, make_admin)
    missing = await set_timing(host, unit, stay_mode="fixed", fixed_checkin_time="12:00")
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "FIXED_TIMES_REQUIRED"
    assert (await set_timing(host, unit, stay_mode="weekly")).status_code == 422
    assert (await set_timing(host, unit, **FIXED)).status_code == 200
    back = await set_timing(host, unit, stay_mode="flexible")
    assert back.status_code == 200 and back.json()["units"][0]["stay_mode"] == "flexible"


async def test_timing_cannot_change_while_guests_have_upcoming_stays_but_other_edits_can(new_client, make_admin, db):
    _, host, _, _, _, unit = await world(new_client, make_admin)
    await set_timing(host, unit, **FIXED)
    guest = await complete_guest(new_client)
    assert (await book(guest, unit, days=10, hours=24)).status_code == 201
    blocked = await set_timing(host, unit, fixed_checkout_time="10:00")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "HAS_UPCOMING_BOOKINGS"
    assert (await set_timing(host, unit, stay_mode="flexible")).status_code == 409
    assert (await host.patch(f"{API}/host/units/{unit['id']}", json={"description": "Newly painted"}, headers=csrf_headers(host))).status_code == 200


async def test_a_copied_unit_keeps_the_fixed_timing(new_client, make_admin):
    _, host, _, prop, _, unit = await world(new_client, make_admin)
    await set_timing(host, unit, **FIXED)
    copy = await host.post(f"{API}/host/units/{unit['id']}/duplicate", headers=csrf_headers(host))
    assert copy.status_code in (200, 201), copy.text
    units = (await host.get(f"{API}/host/properties/{prop['id']}")).json()["units"]
    new = next(u for u in units if u["name"].endswith("(copy)"))
    assert (new["stay_mode"], new["fixed_checkin_time"], new["fixed_checkout_time"]) == ("fixed", "12:00", "11:30")


async def test_the_public_page_shows_the_timing_so_guests_know_before_asking(new_client, make_admin):
    _, host, _, prop, anon, unit = await world(new_client, make_admin)
    await set_timing(host, unit, **FIXED)
    public = (await anon.get(f"{API}/properties/{prop['id']}")).json()["units"][0]
    assert (public["stay_mode"], public["fixed_checkin_time"], public["fixed_checkout_time"]) == ("fixed", "12:00", "11:30")
