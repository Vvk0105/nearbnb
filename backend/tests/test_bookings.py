import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update

from app.modules.bookings import service as booking_service
from app.modules.bookings.models import Booking
from app.modules.hosts.models import HostProfile
from app.modules.notifications.models import EmailOutbox
from app.modules.properties.models import RoomOccupancy, Unit
from tests.conftest import csrf_headers, register_and_verify
from tests.helpers import API, active_host, admin_client, day_local, live_listing


async def complete_guest(new_client, email: str = "guest@example.com", *, profile: bool = True):
    c = await new_client()
    await register_and_verify(c, email)
    if profile:
        h = csrf_headers(c)
        r = await c.patch(f"{API}/me", json={"phone": "+61 400 222 333", "dob": "1990-05-05"}, headers=h)
        assert r.status_code == 200, r.text
        r = await c.post(f"{API}/me/social-links", json={"url": "instagram.com/some.guest"}, headers=h)
        assert r.status_code == 201, r.text
    return c


async def world(new_client, make_admin, **listing):
    admin = await admin_client(new_client, make_admin)
    host, host_id = await active_host(new_client, admin)
    prop = await live_listing(host, admin, **listing)
    return admin, host, host_id, prop, prop["units"][0]


def body(days=10, hour=14, hours=24, guests=2):
    return {"checkin_local": day_local(days, hour), "hours": hours, "guests": guests}


async def book(client, unit, **kw):
    return await client.post(f"{API}/units/{unit['id']}/book", json=body(**kw), headers=csrf_headers(client))


async def emails(db, template: str | None = None, to: str | None = None) -> list[EmailOutbox]:
    q = select(EmailOutbox).order_by(EmailOutbox.created_at)
    if template:
        q = q.where(EmailOutbox.template == template)
    if to:
        q = q.where(EmailOutbox.to_email == to)
    return list((await db.execute(q)).scalars())


async def holds(db, booking_id: str) -> list[tuple]:
    rows = await db.execute(text("select kind, expires_at from room_occupancy where booking_id = :b"), {"b": booking_id})
    return list(rows)


async def quote(client, unit, **kw):
    r = await client.post(f"{API}/units/{unit['id']}/quote", json=body(**kw))
    return r.json()


async def set_booking(db, booking_id: str, **values):
    await db.execute(update(Booking).where(Booking.id == uuid.UUID(booking_id)).values(**values))
    await db.commit()


# ---------- creating a request ----------

async def test_a_request_holds_a_room_snapshots_the_price_and_emails_both_sides(new_client, make_admin, db):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    r = await book(guest, unit)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "requested" and b["reference"].startswith("NB-") and b["guests"] == 2
    assert b["check_in_local"] == day_local(10) and b["check_out_local"] == day_local(11) and b["timezone"] == "Australia/Sydney"
    assert 0 < b["seconds_to_respond"] <= 30 * 60
    assert b["price"]["total"] == 29563 and "commission" not in b["price"] and "host_payable" not in b["price"]
    assert b["host"]["first_name"] and "email" not in b["host"] and "checkin_instructions" not in b

    row = (await db.execute(select(Booking))).scalar_one()
    assert row.pricing_snapshot["commission"] == 3000 and row.pricing_snapshot["host_payable"] == 22000
    assert row.pricing_snapshot["total"] == 29563
    assert row.policy_snapshot["response_minutes"] == 30 and row.policy_snapshot["payment_minutes"] == 30
    assert row.guest_snapshot["social_links"][0]["platform"] == "Instagram" and row.guest_snapshot["age"] >= 18
    kinds = await holds(db, b["id"])
    assert len(kinds) == 1 and kinds[0][0] == "hold" and kinds[0][1] is not None

    assert len(await emails(db, "booking_request_host", "host@example.com")) == 1
    assert len(await emails(db, "booking_request_guest", "guest@example.com")) == 1
    # the room is no longer offered to anyone else
    anon = await new_client()
    q = await quote(anon, unit)
    assert q["available"] is False and q["message"].startswith("Someone is staying")


async def test_login_is_required(new_client, make_admin):
    _, _, _, _, unit = await world(new_client, make_admin)
    anon = await new_client()
    r = await anon.post(f"{API}/units/{unit['id']}/book", json=body())
    assert r.status_code in (401, 403)


async def test_an_incomplete_profile_blocks_the_request_and_says_what_is_missing(new_client, make_admin):
    _, _, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client, profile=False)
    ready = (await guest.get(f"{API}/me/booking-readiness")).json()
    assert ready["ready"] is False and {m["field"] for m in ready["missing"]} == {"dob", "phone", "social_url"}
    r = await book(guest, unit)
    assert r.status_code == 422 and r.json()["error"]["code"] == "PROFILE_INCOMPLETE"
    assert "date of birth" in r.json()["error"]["message"] and "social media" in r.json()["error"]["message"]


async def test_required_fields_follow_the_admin_setting(new_client, make_admin):
    admin, _, _, _, unit = await world(new_client, make_admin)
    r = await admin.put(f"{API}/admin/settings/guest.required_fields", json={"value": ["full_name"], "reason": "test"}, headers=csrf_headers(admin))
    assert r.status_code == 200, r.text
    guest = await complete_guest(new_client, profile=False)
    assert (await book(guest, unit)).status_code == 201


async def test_you_cannot_book_your_own_listing(new_client, make_admin):
    _, host, _, _, unit = await world(new_client, make_admin)
    h = csrf_headers(host)
    await host.patch(f"{API}/me", json={"phone": "+61 400 222 333", "dob": "1985-01-01"}, headers=h)
    await host.post(f"{API}/me/social-links", json={"url": "instagram.com/the.host"}, headers=h)
    r = await book(host, unit)
    assert r.status_code == 403 and r.json()["error"]["code"] == "OWN_LISTING"


async def test_stay_rules_are_enforced_on_the_server(new_client, make_admin):
    _, _, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    cases = [
        (dict(hours=36), "INVALID_DURATION"), (dict(hours=24 * 30), "STAY_TOO_LONG"), (dict(guests=9), "OVER_CAPACITY"),
        (dict(hour=3), "OUTSIDE_CHECKIN_HOURS"), (dict(days=-2), "IN_THE_PAST"), (dict(days=400), "TOO_FAR_AHEAD"),
    ]
    for kw, code in cases:
        r = await book(guest, unit, **kw)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, (kw, r.text)


async def test_a_guest_can_have_only_a_few_open_requests(new_client, make_admin):
    _, _, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    for d in (10, 12, 14):
        assert (await book(guest, unit, days=d)).status_code == 201
    r = await book(guest, unit, days=16)
    assert r.status_code == 409 and r.json()["error"]["code"] == "TOO_MANY_PENDING"


async def test_back_to_back_requests_are_fine_but_overlaps_are_not(new_client, make_admin):
    _, _, _, _, unit = await world(new_client, make_admin)
    a, b = await complete_guest(new_client, "a@example.com"), await complete_guest(new_client, "b@example.com")
    assert (await book(a, unit, days=10)).status_code == 201
    assert (await book(b, unit, days=11)).status_code == 201          # starts exactly when the first one ends
    r = await book(b, unit, days=10, hour=20)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SLOT_UNAVAILABLE" and "Someone is staying" in r.json()["error"]["message"]


async def test_hotel_rooms_are_assigned_one_per_request(new_client, make_admin):
    _, host, _, _, unit = await world(new_client, make_admin, quantity=2)
    g = [await complete_guest(new_client, f"g{i}@example.com") for i in range(3)]
    ids = []
    for c in g[:2]:
        r = await book(c, unit)
        assert r.status_code == 201
        ids.append(r.json()["id"])
    assert (await book(g[2], unit)).status_code == 409
    listing = (await host.get(f"{API}/host/bookings")).json()["items"]
    assert sorted(i["room"] for i in listing) == ["1", "2"]


async def test_simultaneous_requests_for_the_same_room_have_exactly_one_winner(new_client, make_admin, db):
    _, _, _, _, unit = await world(new_client, make_admin)
    guests = [await complete_guest(new_client, f"race{i}@example.com") for i in range(6)]
    results = await asyncio.gather(*(book(c, unit) for c in guests))
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409, 409, 409, 409, 409], [r.text for r in results]
    assert all(r.json()["error"]["code"] == "SLOT_UNAVAILABLE" for r in results if r.status_code == 409)
    assert len((await db.execute(select(Booking))).scalars().all()) == 1
    assert (await db.execute(text("select count(*) from room_occupancy where kind = 'hold'"))).scalar_one() == 1


async def test_simultaneous_requests_fill_every_room_and_no_more(new_client, make_admin, db):
    _, _, _, _, unit = await world(new_client, make_admin, quantity=2)
    guests = [await complete_guest(new_client, f"race{i}@example.com") for i in range(5)]
    results = await asyncio.gather(*(book(c, unit) for c in guests))
    assert sorted(r.status_code for r in results) == [201, 201, 409, 409, 409], [r.text for r in results]
    rooms = {row[0] for row in await db.execute(text("select room_id from room_occupancy where kind = 'hold'"))}
    assert len(rooms) == 2


# ---------- host decisions ----------

async def test_accepting_moves_the_deadline_to_payment_and_emails_the_guest(new_client, make_admin, db):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    b = (await book(guest, unit)).json()

    detail = (await host.get(f"{API}/host/bookings/{b['id']}")).json()
    assert detail["price"]["commission"] == 3000 and detail["price"]["host_payable"] == 22000
    assert detail["guest"]["full_name"] == "Test Guest" and detail["guest"]["social_links"][0]["url"].startswith("https://instagram.com/")
    assert "phone" not in detail["guest"] and "email" not in detail["guest"]          # contact details only after payment

    r = await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted" and r.json()["seconds_to_pay"] > 0

    row = (await db.execute(select(Booking))).scalar_one()
    (kind, expires), = await holds(db, b["id"])
    assert kind == "hold" and expires == row.payment_deadline_at
    assert timedelta(minutes=29) < row.payment_deadline_at - row.accepted_at <= timedelta(minutes=30)
    assert len(await emails(db, "booking_accepted_guest", "guest@example.com")) == 1

    mine = (await guest.get(f"{API}/me/bookings/{b['id']}")).json()
    assert mine["status"] == "accepted" and mine["seconds_to_pay"] > 0
    again = await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATE"


async def test_rejecting_releases_the_dates_and_tells_the_guest_why(new_client, make_admin, db):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    b = (await book(guest, unit)).json()
    r = await host.post(f"{API}/host/bookings/{b['id']}/reject", json={"note": "Sorry, we are renovating."}, headers=csrf_headers(host))
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert await holds(db, b["id"]) == []
    assert (await quote(await new_client(), unit))["available"] is True
    mail = (await emails(db, "booking_rejected_guest", "guest@example.com"))[0]
    assert "renovating" in mail.body_text
    mine = (await guest.get(f"{API}/me/bookings/{b['id']}")).json()
    assert mine["status"] == "rejected" and mine["closed_reason"] == "Sorry, we are renovating." and mine["seconds_to_respond"] is None


async def test_a_host_only_sees_and_decides_their_own_requests(new_client, make_admin):
    admin, host, _, _, unit = await world(new_client, make_admin)
    other, _ = await active_host(new_client, admin, "other@example.com")
    guest = await complete_guest(new_client)
    b = (await book(guest, unit)).json()
    assert (await other.get(f"{API}/host/bookings")).json()["items"] == []
    assert (await other.get(f"{API}/host/bookings/{b['id']}")).status_code == 404
    assert (await other.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(other))).status_code == 404
    assert (await other.post(f"{API}/host/bookings/{b['id']}/reject", json={}, headers=csrf_headers(other))).status_code == 404
    assert (await guest.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(guest))).status_code == 404  # not a host


async def test_a_suspended_host_cannot_accept(new_client, make_admin, db):
    _, host, host_id, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    b = (await book(guest, unit)).json()
    await db.execute(update(HostProfile).where(HostProfile.id == uuid.UUID(host_id)).values(suspended_at=datetime.now(UTC)))
    await db.commit()
    r = await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
    assert r.status_code == 403 and r.json()["error"]["code"] == "HOST_NOT_ACTIVE"


# ---------- guest withdraw ----------

async def test_a_guest_can_withdraw_before_and_after_acceptance(new_client, make_admin, db):
    _, host, _, _, unit = await world(new_client, make_admin, quantity=2)
    guest = await complete_guest(new_client)
    first = (await book(guest, unit, days=10)).json()
    second = (await book(guest, unit, days=12)).json()
    r = await guest.post(f"{API}/me/bookings/{first['id']}/withdraw", headers=csrf_headers(guest))
    assert r.status_code == 200 and r.json()["status"] == "withdrawn" and await holds(db, first["id"]) == []
    await host.post(f"{API}/host/bookings/{second['id']}/accept", headers=csrf_headers(host))
    r = await guest.post(f"{API}/me/bookings/{second['id']}/withdraw", headers=csrf_headers(guest))
    assert r.status_code == 200 and await holds(db, second["id"]) == []
    assert len(await emails(db, "booking_withdrawn_host", "host@example.com")) == 2
    again = await guest.post(f"{API}/me/bookings/{first['id']}/withdraw", headers=csrf_headers(guest))
    assert again.status_code == 409


async def test_guests_cannot_see_or_withdraw_each_others_bookings(new_client, make_admin):
    _, _, _, _, unit = await world(new_client, make_admin)
    a, b = await complete_guest(new_client, "a@example.com"), await complete_guest(new_client, "b@example.com")
    booking = (await book(a, unit)).json()
    assert (await b.get(f"{API}/me/bookings/{booking['id']}")).status_code == 404
    assert (await b.get(f"{API}/me/bookings")).json()["items"] == []
    assert (await b.post(f"{API}/me/bookings/{booking['id']}/withdraw", headers=csrf_headers(b))).status_code == 404


# ---------- expiry and reminders ----------

async def test_an_unanswered_request_expires_and_frees_the_room(new_client, make_admin, db):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    b = (await book(guest, unit)).json()
    past = datetime.now(UTC) - timedelta(minutes=1)
    await set_booking(db, b["id"], expires_at=past)
    await db.execute(update(RoomOccupancy).where(RoomOccupancy.booking_id == uuid.UUID(b["id"])).values(expires_at=past))
    await db.commit()

    from app.core.db import SessionLocal
    async with SessionLocal() as s:
        assert await booking_service.expire_due(s) == 1
        await s.commit()
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "expired"
    assert await holds(db, b["id"]) == []
    assert len(await emails(db, "booking_expired_guest", "guest@example.com")) == 1
    assert len(await emails(db, "booking_expired_host", "host@example.com")) == 1
    late = await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
    assert late.status_code == 409
    assert (await quote(await new_client(), unit))["available"] is True


async def test_listing_bookings_closes_expired_ones_even_if_the_job_has_not_run(new_client, make_admin, db):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    b = (await book(guest, unit)).json()
    await set_booking(db, b["id"], expires_at=datetime.now(UTC) - timedelta(seconds=5))
    items = (await host.get(f"{API}/host/bookings?group=pending")).json()["items"]
    assert items == []
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "expired"


async def test_a_stale_hold_never_blocks_a_new_request(new_client, make_admin, db):
    _, _, _, _, unit = await world(new_client, make_admin)
    a, b = await complete_guest(new_client, "a@example.com"), await complete_guest(new_client, "b@example.com")
    first = (await book(a, unit)).json()
    past = datetime.now(UTC) - timedelta(minutes=5)
    await set_booking(db, first["id"], expires_at=past)
    await db.execute(update(RoomOccupancy).where(RoomOccupancy.booking_id == uuid.UUID(first["id"])).values(expires_at=past))
    await db.commit()
    # no job ran, the dead hold is still in the table, yet the next guest gets the dates
    r = await book(b, unit)
    assert r.status_code == 201, r.text
    assert (await a.get(f"{API}/me/bookings/{first['id']}")).json()["status"] == "expired"


async def test_an_unpaid_acceptance_expires_after_the_payment_deadline(new_client, make_admin, db):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    b = (await book(guest, unit)).json()
    await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
    past = datetime.now(UTC) - timedelta(minutes=1)
    await set_booking(db, b["id"], payment_deadline_at=past)
    await db.execute(update(RoomOccupancy).where(RoomOccupancy.booking_id == uuid.UUID(b["id"])).values(expires_at=past))
    await db.commit()
    from app.core.db import SessionLocal
    async with SessionLocal() as s:
        assert await booking_service.expire_due(s) == 1
        await s.commit()
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "payment_expired"
    assert await holds(db, b["id"]) == []
    assert len(await emails(db, "booking_payment_expired_guest", "guest@example.com")) == 1


async def test_reminders_go_out_once_at_the_halfway_point(new_client, make_admin, db):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    b = (await book(guest, unit)).json()
    from app.core.db import SessionLocal
    async with SessionLocal() as s:
        assert await booking_service.send_reminders(s) == 0           # a fresh request is not yet halfway
        await s.commit()
    now = datetime.now(UTC)   # a 30 minute window that is 20 minutes used
    await set_booking(db, b["id"], requested_at=now - timedelta(minutes=20), expires_at=now + timedelta(minutes=10))
    async with SessionLocal() as s:
        assert await booking_service.send_reminders(s) == 1
        await s.commit()
    async with SessionLocal() as s:
        assert await booking_service.send_reminders(s) == 0           # only once
        await s.commit()
    assert len(await emails(db, "booking_reminder_host", "host@example.com")) == 1

    await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
    await set_booking(db, b["id"], accepted_at=now - timedelta(minutes=20), payment_deadline_at=now + timedelta(minutes=10))
    async with SessionLocal() as s:
        assert await booking_service.send_reminders(s) == 1
        await s.commit()
    assert len(await emails(db, "booking_payment_reminder_guest", "guest@example.com")) == 1


# ---------- snapshots, lists, admin ----------

async def test_later_price_changes_do_not_alter_an_existing_booking(new_client, make_admin, db):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    b = (await book(guest, unit)).json()
    await db.execute(update(Unit).where(Unit.id == uuid.UUID(unit["id"])).values(price_per_24h=99900, cleaning_fee=5000))
    await db.commit()
    mine = (await guest.get(f"{API}/me/bookings/{b['id']}")).json()
    assert mine["price"]["accommodation"] == 25000 and mine["price"]["total"] == 29563


async def test_lists_are_grouped_and_counted(new_client, make_admin):
    admin, host, _, _, unit = await world(new_client, make_admin, quantity=3)
    guest = await complete_guest(new_client)
    ids = [(await book(guest, unit, days=d)).json()["id"] for d in (10, 12, 14)]
    await host.post(f"{API}/host/bookings/{ids[0]}/accept", headers=csrf_headers(host))
    await host.post(f"{API}/host/bookings/{ids[1]}/reject", json={}, headers=csrf_headers(host))
    data = (await host.get(f"{API}/host/bookings")).json()
    assert data["counts"] == {"pending": 1, "awaiting_payment": 1, "upcoming": 0, "past": 1} and data["total"] == 3
    assert [i["id"] for i in (await host.get(f"{API}/host/bookings?group=pending")).json()["items"]] == [ids[2]]
    assert [i["id"] for i in (await guest.get(f"{API}/me/bookings?group=past")).json()["items"]] == [ids[1]]
    assert (await host.get(f"{API}/host/bookings?group=nonsense")).status_code == 422

    everything = (await admin.get(f"{API}/admin/bookings")).json()
    assert everything["total"] == 3 and "policy" in everything["items"][0] and everything["items"][0]["guest"]["email"] == "guest@example.com"
    assert (await guest.get(f"{API}/admin/bookings")).status_code == 403
    assert (await admin.get(f"{API}/admin/bookings?status=rejected")).json()["total"] == 1


async def test_the_unit_page_calendar_marks_requested_days(new_client, make_admin):
    _, _, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    assert (await book(guest, unit, days=10, hours=48)).status_code == 201       # day 10 14:00 to day 12 14:00
    anon = await new_client()
    seen: dict[str, str] = {}
    for d in (10, 12):
        month = day_local(d)[:7]
        days = (await anon.get(f"{API}/units/{unit['id']}/availability/calendar?month={month}")).json()["days"]
        seen.update({x["date"]: x["status"] for x in days})
    assert seen[day_local(10)[:10]] == "partial" and seen[day_local(11)[:10]] == "unavailable" and seen[day_local(12)[:10]] == "partial"
