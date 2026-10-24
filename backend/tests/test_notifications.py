"""The bell: every moment that emails someone also leaves a line in their notifications."""
import uuid

from app.modules.notifications import inbox
from tests.conftest import csrf_headers, register_and_verify
from tests.helpers import API, admin_client, submit_application
from tests.test_bookings import book, complete_guest, world
from tests.test_cancellations import get, paid_world, spy  # noqa: F401


async def post(client, path, body=None):
    return await client.post(f"{API}{path}", json=body, headers=csrf_headers(client))


def kinds(listing) -> list[str]:
    return [n["kind"] for n in listing["items"]]


# ---------- what creates one ----------

async def test_a_booking_request_leaves_a_message_in_the_hosts_bell_and_in_the_guests(new_client, make_admin):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    booking = (await book(guest, unit)).json()

    mine = await get(host, "/me/notifications")
    newest = mine["items"][0]
    assert newest["kind"] == "booking_request_host" and newest["tone"] == "action" and newest["link"] == "/host/requests"
    assert newest["read"] is False and newest["title"] and newest["created_at"]

    theirs = await get(guest, "/me/notifications")
    first = theirs["items"][0]
    assert first["kind"] == "booking_request_guest" and first["tone"] == "news" and first["link"] == f"/account/trips/{booking['id']}"


async def test_hosting_decisions_reach_the_host_with_the_reviewers_note_and_a_link(new_client, make_admin):
    admin = await admin_client(new_client, make_admin)
    applicant = await new_client()
    data = await submit_application(applicant, "applicant@example.com")
    assert "host_submitted" in kinds(await get(applicant, "/me/notifications"))

    asked = await post(admin, f"/admin/hosts/{data['id']}/decision", {"decision": "request_changes", "note": "ID is blurry"})
    assert asked.status_code == 200, asked.text
    item = (await get(applicant, "/me/notifications"))["items"][0]
    assert (item["kind"], item["tone"], item["body"], item["link"]) == ("host_changes_requested", "action", "ID is blurry", "/host/apply")

    assert (await post(applicant, "/host/application/submit", {"accept_host_terms": True})).status_code == 200
    assert (await post(admin, f"/admin/hosts/{data['id']}/decision", {"decision": "approve"})).status_code == 200
    item = (await get(applicant, "/me/notifications"))["items"][0]
    assert (item["kind"], item["tone"], item["link"]) == ("host_approved", "news", "/host")


async def test_listing_approval_is_news_for_the_host(new_client, make_admin):
    _, host, _, _, _ = await world(new_client, make_admin)               # the world approves the host and the listing
    listing = await get(host, "/me/notifications")
    approved = next(n for n in listing["items"] if n["kind"] == "listing_approved")
    assert approved["tone"] == "news" and approved["link"] == "/host/properties"
    assert {"host_approved", "host_active"} <= set(kinds(listing))


async def test_administrators_get_messages_that_lead_to_the_right_admin_page(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    asked = await post(w.host, f"/host/bookings/{w.id}/refund-request", {"amount": 1000, "reason": "broken heater"})
    assert asked.status_code == 201, asked.text
    admin_list = await get(w.admin, "/me/notifications")
    item = next(n for n in admin_list["items"] if n["kind"] == "refund_requested_admin")
    assert item["link"] == "/admin/refunds" and item["tone"] == "action"


async def test_codes_and_security_emails_are_never_shown_in_the_bell(new_client):
    c = await new_client()
    await register_and_verify(c, "newbie@example.com")                       # this sent a verification code by email
    assert (await get(c, "/me/notifications"))["items"] == []
    assert (await get(c, "/me/notifications/unread-count"))["unread"] == 0


def test_only_listed_emails_become_notifications_and_links_use_the_booking():
    user = uuid.uuid4()
    assert inbox.build(user, "verify_email", "Verify", {"code": "123456"}) is None
    assert inbox.build(user, "reset_password", "Reset", {}) is None
    assert inbox.build(user, "password_changed", "Changed", {}) is None
    assert inbox.build(user, "no_such_template", "x", {}) is None
    n = inbox.build(user, "booking_accepted_guest", "Accepted", {"booking_id": "b-1", "note": "see you soon"})
    assert (n.link, n.tone, n.title, n.body) == ("/account/trips/b-1/pay", "action", "Accepted", "see you soon")
    assert inbox.build(user, "booking_accepted_guest", "x", {"booking_id": "b-1"}).body == ""
    assert len(inbox.build(user, "host_rejected", "t" * 500, {"note": "n" * 3000}).title) == 300


# ---------- reading and marking ----------

async def test_each_person_sees_only_their_own_and_cannot_touch_anyone_elses(new_client, make_admin):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    await book(guest, unit)
    host_item = (await get(host, "/me/notifications"))["items"][0]
    guest_ids = {n["id"] for n in (await get(guest, "/me/notifications"))["items"]}
    assert host_item["id"] not in guest_ids
    stolen = await post(guest, f"/me/notifications/{host_item['id']}/read")
    assert stolen.status_code == 404                                         # looks exactly like one that does not exist
    assert (await get(host, "/me/notifications"))["items"][0]["read"] is False
    assert (await post(guest, f"/me/notifications/{uuid.uuid4()}/read")).status_code == 404
    assert (await post(guest, "/me/notifications/not-an-id/read")).status_code == 404


async def test_reading_one_or_all_updates_the_count_and_the_unread_filter(new_client, make_admin):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    await book(guest, unit)
    before = (await get(host, "/me/notifications/unread-count"))["unread"]
    assert before >= 4                                                       # approvals and the request itself
    listing = await get(host, "/me/notifications")
    assert listing["unread"] == before and listing["total"] == before

    one = await post(host, f"/me/notifications/{listing['items'][0]['id']}/read")
    assert one.status_code == 200 and one.json()["unread"] == before - 1
    again = await post(host, f"/me/notifications/{listing['items'][0]['id']}/read")
    assert again.json()["unread"] == before - 1                              # reading twice changes nothing
    unread_only = await get(host, "/me/notifications?unread=true")
    assert unread_only["total"] == before - 1 and all(not n["read"] for n in unread_only["items"])

    done = await post(host, "/me/notifications/read-all")
    assert done.status_code == 200 and done.json() == {"marked": before - 1, "unread": 0}
    assert (await get(host, "/me/notifications?unread=true"))["items"] == []
    assert (await get(host, "/me/notifications"))["total"] == before         # they stay in the list, just read
    assert (await post(guest, "/me/notifications/read-all")).json()["unread"] == 0   # and one person's "read all" never touches another's


async def test_the_list_is_newest_first_and_paged(new_client, make_admin):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    await book(guest, unit)
    everything = await get(host, "/me/notifications?page_size=100")
    times = [n["created_at"] for n in everything["items"]]
    assert times == sorted(times, reverse=True) and everything["items"][0]["kind"] == "booking_request_host"
    first = await get(host, "/me/notifications?page=1&page_size=2")
    second = await get(host, "/me/notifications?page=2&page_size=2")
    assert len(first["items"]) == 2 and first["total"] == everything["total"]
    assert {n["id"] for n in first["items"]}.isdisjoint({n["id"] for n in second["items"]})
    stranger = await new_client()
    assert (await stranger.get(f"{API}/me/notifications")).status_code == 401
    assert (await host.get(f"{API}/me/notifications?page_size=500")).status_code == 422
