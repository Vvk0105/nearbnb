import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.modules.audit.models import AuditLog
from app.modules.bookings import service as booking_service
from app.modules.bookings.models import Booking
from app.modules.reviews import service as review_service
from app.modules.reviews.models import Review
from tests.conftest import csrf_headers
from tests.helpers import API, active_host
from tests.test_bookings import book, complete_guest, emails
from tests.test_cancellations import get, paid_world, shift, spy  # noqa: F401
from tests.test_payments import dev_complete, row


async def finish_stay(db, w, hours_ago: float = 60):
    """The stay is over: moves the dates into the past and lets the clock job complete the booking."""
    await shift(db, w.id, -hours_ago)
    async with SessionLocal() as s:
        await booking_service.advance_status(s)
        await s.commit()
    assert (await get(w.guest, f"/me/bookings/{w.id}"))["status"] == "completed"


async def completed(new_client, make_admin, db, **kw):
    w = await paid_world(new_client, make_admin, **kw)
    await finish_stay(db, w)
    return w


async def rate(client, who, booking_id, rating=5, comment="Lovely place"):
    path = "/me/bookings" if who == "guest" else "/host/bookings"
    return await client.post(f"{API}{path}/{booking_id}/review", json={"rating": rating, "comment": comment}, headers=csrf_headers(client))


async def process(now=None):
    async with SessionLocal() as s:
        counts = await review_service.process(s, now)
        await s.commit()
    return counts


# ---------- the window ----------

async def test_completing_a_stay_opens_a_14_day_review_window(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    b = (await row(db, Booking))[0]
    assert b.review_closes_at is not None and b.review_closes_at - b.check_out == timedelta(days=14)
    for client, who in ((w.guest, "me"), (w.host, "host")):
        info = (await get(client, f"/{who}/bookings/{w.id}"))["review"]
        assert info["open"] is True and info["can_review"] is True and info["mine"] is None and info["theirs"] is None


async def test_a_stay_that_is_not_over_has_no_review_section(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    assert (await get(w.guest, f"/me/bookings/{w.id}"))["review"] is None
    r = await rate(w.guest, "guest", w.id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_COMPLETED"


async def test_the_window_length_follows_the_setting(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await w.admin.put(f"{API}/admin/settings/review.window_days", json={"value": 3, "reason": "test"}, headers=csrf_headers(w.admin))
    await finish_stay(db, w)
    b = (await row(db, Booking))[0]
    assert b.review_closes_at - b.check_out == timedelta(days=3)


# ---------- writing a review ----------

async def test_reviews_are_validated_owned_and_one_per_side(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    assert (await rate(w.guest, "guest", w.id, rating=0)).status_code == 422
    assert (await rate(w.guest, "guest", w.id, rating=6)).status_code == 422
    assert (await rate(w.guest, "guest", w.id, comment="x" * 2001)).status_code == 422
    other = await complete_guest(new_client, "other@example.com")
    assert (await rate(other, "guest", w.id)).status_code == 404
    assert (await rate(w.host, "guest", w.id)).status_code == 404                 # a host cannot post the guest's review
    assert (await rate(w.guest, "host", w.id)).status_code == 404                 # nor the other way round
    ok = await rate(w.guest, "guest", w.id, rating=4, comment="  Clean and quiet.  ")
    assert ok.status_code == 201
    mine = ok.json()["review"]["mine"]
    assert mine["rating"] == 4 and mine["comment"] == "Clean and quiet." and mine["status"] == "submitted"
    again = await rate(w.guest, "guest", w.id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_REVIEWED"
    assert (await get(w.guest, f"/me/bookings/{w.id}"))["review"]["can_review"] is False


async def test_a_comment_is_optional(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    assert (await rate(w.guest, "guest", w.id, rating=5, comment="")).status_code == 201


async def test_you_cannot_review_after_the_window_closes_or_a_cancelled_booking(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    await db.execute(update(Booking).values(review_closes_at=datetime.now(UTC) - timedelta(minutes=1)))
    await db.commit()
    late = await rate(w.guest, "guest", w.id)
    assert late.status_code == 409 and late.json()["error"]["code"] == "WINDOW_CLOSED"
    assert (await get(w.guest, f"/me/bookings/{w.id}"))["review"]["can_review"] is False


async def test_a_cancelled_booking_cannot_be_reviewed(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await w.guest.post(f"{API}/me/bookings/{w.id}/cancel", json={}, headers=csrf_headers(w.guest))
    r = await rate(w.guest, "guest", w.id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_COMPLETED"


# ---------- blind publishing ----------

async def test_reviews_stay_hidden_until_both_are_in_and_then_publish_together(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    pid = w.prop["id"]
    await rate(w.guest, "guest", w.id, rating=5, comment="Wonderful stay, spotless.")
    assert (await get(w.guest, f"/properties/{pid}/reviews"))["items"] == []                      # not public yet
    assert (await get(w.host, f"/host/bookings/{w.id}"))["review"]["theirs"] is None              # the host cannot peek either
    assert (await row(db, Review))[0].status == "submitted"

    r = await rate(w.host, "host", w.id, rating=3, comment="Left the kitchen messy.")
    assert r.status_code == 201
    statuses = {x.direction: x.status for x in await row(db, Review)}
    assert statuses == {"guest_to_host": "published", "host_to_guest": "published"}
    public = await get(w.guest, f"/properties/{pid}/reviews")
    assert public["count"] == 1 and public["average"] == 5.0
    assert public["items"][0]["comment"] == "Wonderful stay, spotless." and public["items"][0]["author"] == "Test" and public["items"][0]["month"]
    assert (await get(w.host, f"/host/bookings/{w.id}"))["review"]["theirs"] == {"rating": 5, "comment": "Wonderful stay, spotless."}


async def test_the_guest_can_never_read_what_the_host_wrote_about_them(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    await rate(w.guest, "guest", w.id)
    await rate(w.host, "host", w.id, rating=2, comment="SECRET-HOST-OPINION")
    guest_view = await get(w.guest, f"/me/bookings/{w.id}")
    assert guest_view["review"]["theirs"] is None and guest_view["review"]["mine"]["rating"] == 5
    everything = json.dumps([guest_view, await get(w.guest, f"/properties/{w.prop['id']}/reviews"), await get(w.guest, "/me/bookings"), await get(w.guest, "/me/issues")])
    assert "SECRET-HOST-OPINION" not in everything
    anon = await new_client()
    assert "SECRET-HOST-OPINION" not in json.dumps(await get(anon, f"/properties/{w.prop['id']}/reviews"))
    assert "SECRET-HOST-OPINION" not in json.dumps((await anon.get(f"{API}/properties/{w.prop['id']}")).json())


async def test_a_single_review_publishes_when_the_window_closes(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    await rate(w.guest, "guest", w.id, rating=4, comment="Good")
    assert (await process())["published"] == 0                                                    # window still open
    closes = (await row(db, Booking))[0].review_closes_at
    assert (await process(closes - timedelta(minutes=1)))["published"] == 0
    assert (await process(closes + timedelta(minutes=1)))["published"] == 1
    assert (await get(w.guest, f"/properties/{w.prop['id']}/reviews"))["count"] == 1
    assert (await process(closes + timedelta(hours=1)))["published"] == 0                         # nothing left to do


async def test_the_property_rating_is_the_average_of_published_reviews(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db, quantity=2)
    second = (await book(w.guest, w.unit, days=14)).json()
    await w.host.post(f"{API}/host/bookings/{second['id']}/accept", headers=csrf_headers(w.host))
    await dev_complete(w.guest, second["id"])
    await shift(db, second["id"], -200)                                                     # a different week, so the two stays do not overlap
    async with SessionLocal() as s:
        await booking_service.advance_status(s)
        await s.commit()
    for bid, rating in ((w.id, 5), (second["id"], 4)):
        await rate(w.guest, "guest", bid, rating=rating)
        await rate(w.host, "host", bid, rating=5)
    public = await get(w.guest, f"/properties/{w.prop['id']}/reviews")
    assert public["average"] == 4.5 and public["count"] == 2 and [i["rating"] for i in public["items"]] in ([4, 5], [5, 4])
    page = await get(w.guest, f"/properties/{w.prop['id']}")
    assert page["rating"] == {"average": 4.5, "count": 2}
    found = await get(w.guest, "/search?q=Bondi")
    assert found["items"][0]["rating"] == {"average": 4.5, "count": 2}
    assert (await w.guest.get(f"{API}/properties/not-a-uuid/reviews")).status_code == 404


async def test_a_property_without_reviews_has_no_rating(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    assert (await get(w.guest, f"/properties/{w.prop['id']}"))["rating"] is None
    assert (await get(w.guest, "/search?q=Bondi"))["items"][0]["rating"] is None
    empty = await get(w.guest, f"/properties/{w.prop['id']}/reviews")
    assert empty["average"] is None and empty["count"] == 0 and empty["items"] == []


# ---------- invitations and reminders ----------

async def test_both_sides_are_invited_once_when_the_stay_is_over(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    assert (await process())["invited"] == 1
    closes = (await row(db, Booking))[0].review_closes_at.strftime("%d %b %Y")
    guest_text = (await emails(db, "review_invite_guest", "guest@example.com"))[0].body_text        # read at once: row() expires earlier objects
    host_text = (await emails(db, "review_invite_host", "host@example.com"))[0].body_text
    assert closes in guest_text and f"/account/trips/{w.id}" in guest_text and "blind" in guest_text
    assert "private" in host_text and "Hannah Host" not in guest_text
    assert (await process())["invited"] == 0 and len(await emails(db, "review_invite_guest")) == 1


async def test_only_people_who_have_not_reviewed_get_a_reminder_near_the_end(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    await process()
    await rate(w.guest, "guest", w.id)
    closes = (await row(db, Booking))[0].review_closes_at
    assert (await process(closes - timedelta(days=5)))["reminded"] == 0                           # too early
    assert (await process(closes - timedelta(days=2)))["reminded"] == 1
    assert await emails(db, "review_reminder_guest") == []                                       # the guest already reviewed
    assert len(await emails(db, "review_reminder_host", "host@example.com")) == 1
    assert (await process(closes - timedelta(days=1)))["reminded"] == 0                           # once only


async def test_no_reminders_when_both_have_reviewed_or_the_window_has_closed(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    await process()
    await rate(w.guest, "guest", w.id)
    await rate(w.host, "host", w.id)
    closes = (await row(db, Booking))[0].review_closes_at
    await process(closes - timedelta(days=2))
    assert await emails(db, "review_reminder_guest") == [] and await emails(db, "review_reminder_host") == []


# ---------- what a host learns about a guest ----------

async def test_a_host_sees_how_other_hosts_rated_a_guest_who_asks_to_book(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    await rate(w.guest, "guest", w.id)
    await rate(w.host, "host", w.id, rating=2, comment="Noisy and left a mess.")
    host_b, _ = await active_host(new_client, w.admin, "hostb@example.com")
    from tests.helpers import live_listing

    prop_b = await live_listing(host_b, w.admin, name="Other House")
    request = (await book(w.guest, prop_b["units"][0], days=20)).json()
    guest_card = (await get(host_b, f"/host/bookings/{request['id']}"))["guest"]
    assert guest_card["reviews"] == {"count": 1, "average": 2.0, "completed_stays": 1, "items": [{"rating": 2, "comment": "Noisy and left a mess.", "month": guest_card["reviews"]["items"][0]["month"]}]}
    assert "email" not in guest_card                                                              # still no contact details before payment


async def test_a_guest_with_no_history_shows_an_empty_summary(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin, days=10)
    card = (await get(w.host, f"/host/bookings/{w.id}"))["guest"]["reviews"]
    assert card == {"count": 0, "average": None, "completed_stays": 0, "items": []}


# ---------- admin moderation ----------

async def test_admins_can_find_hide_and_restore_reviews_with_a_reason(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    await rate(w.guest, "guest", w.id, rating=1, comment="Terrible, avoid.")
    await rate(w.host, "host", w.id, rating=4, comment="Fine guest.")
    h = csrf_headers(w.admin)
    both = await get(w.admin, "/admin/reviews")
    assert both["total"] == 2
    assert [r["comment"] for r in (await get(w.admin, "/admin/reviews?direction=guest_to_host"))["items"]] == ["Terrible, avoid."]
    assert (await get(w.admin, "/admin/reviews?q=Terrible"))["total"] == 1 and (await get(w.admin, f"/admin/reviews?q={w.b['reference']}"))["total"] == 2
    item = (await get(w.admin, "/admin/reviews?direction=guest_to_host"))["items"][0]
    assert item["reference"] == w.b["reference"] and item["status"] == "published" and item["author_email"] == "guest@example.com"

    assert (await w.admin.post(f"{API}/admin/reviews/{item['id']}/hide", json={"reason": " "}, headers=h)).status_code == 422
    assert (await w.guest.post(f"{API}/admin/reviews/{item['id']}/hide", json={"reason": "x"}, headers=csrf_headers(w.guest))).status_code == 403
    assert (await w.admin.post(f"{API}/admin/reviews/{item['id']}/hide", json={"reason": "Abusive language"}, headers=h)).json()["status"] == "hidden"
    public = await get(w.guest, f"/properties/{w.prop['id']}/reviews")
    assert public["items"] == [] and public["count"] == 0 and public["average"] is None             # hidden reviews leave the rating too
    assert (await get(w.guest, f"/properties/{w.prop['id']}"))["rating"] is None
    assert (await get(w.host, f"/host/bookings/{w.id}"))["review"]["theirs"] is None
    assert (await w.admin.post(f"{API}/admin/reviews/{item['id']}/hide", json={"reason": "again"}, headers=h)).status_code == 409
    assert (await get(w.admin, "/admin/reviews?status=hidden"))["items"][0]["hidden_reason"] == "Abusive language"

    assert (await w.admin.post(f"{API}/admin/reviews/{item['id']}/restore", json={"reason": "Reviewed again"}, headers=h)).json()["status"] == "published"
    assert (await get(w.guest, f"/properties/{w.prop['id']}/reviews"))["count"] == 1
    assert (await w.admin.post(f"{API}/admin/reviews/{item['id']}/restore", json={}, headers=h)).status_code == 409
    logged = (await db.execute(select(AuditLog).where(AuditLog.action.in_(("review.hidden", "review.restored"))).order_by(AuditLog.id))).scalars().all()
    assert [a.action for a in logged] == ["review.hidden", "review.restored"] and logged[0].reason == "Abusive language"


async def test_hiding_a_review_before_it_publishes_keeps_it_hidden_and_restoring_puts_it_back_to_waiting(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    await rate(w.guest, "guest", w.id, comment="Nope")
    rid = (await get(w.admin, "/admin/reviews"))["items"][0]["id"]
    h = csrf_headers(w.admin)
    await w.admin.post(f"{API}/admin/reviews/{rid}/hide", json={"reason": "Spam"}, headers=h)
    await rate(w.host, "host", w.id)                                                              # the other side arrives; the hidden one stays hidden
    assert (await get(w.admin, "/admin/reviews?status=hidden"))["total"] == 1
    assert (await get(w.guest, f"/properties/{w.prop['id']}/reviews"))["count"] == 0
    assert (await w.admin.post(f"{API}/admin/reviews/{rid}/restore", json={}, headers=h)).json()["status"] == "submitted"
    assert (await w.admin.post(f"{API}/admin/reviews/{uuid.uuid4()}/hide", json={"reason": "x"}, headers=h)).status_code == 404


async def test_admin_review_screens_are_for_admins(new_client, make_admin, db, spy):
    w = await completed(new_client, make_admin, db)
    for client in (w.guest, w.host, await new_client()):
        assert (await client.get(f"{API}/admin/reviews")).status_code in (401, 403)
