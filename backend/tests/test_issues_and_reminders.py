import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.modules.audit.models import AuditLog
from app.modules.bookings import service as booking_service
from app.modules.bookings.models import Booking
from app.modules.hosts import service as host_service
from app.modules.hosts.models import HostProfile
from app.modules.issues.models import Issue
from tests.conftest import csrf_headers
from tests.helpers import API
from tests.test_bookings import book, complete_guest, emails
from tests.test_cancellations import Spy, get, paid_world, shift, spy  # noqa: F401
from tests.test_payouts import release
from tests.test_payments import row

DESCRIPTION = "The heating did not work and the host did not answer."


async def report(client, who, booking_id, category="property_condition", body=DESCRIPTION):
    path = "/me/bookings" if who == "guest" else "/host/bookings"
    return await client.post(f"{API}{path}/{booking_id}/issues", json={"category": category, "body": body}, headers=csrf_headers(client))


async def patch(admin, issue_id, **body):
    return await admin.patch(f"{API}/admin/issues/{issue_id}", json=body, headers=csrf_headers(admin))


# ---------- reporting a problem ----------

async def test_a_guest_can_report_a_problem_and_everyone_is_told(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    r = await report(w.guest, "guest", w.id)
    assert r.status_code == 201
    issue = r.json()
    assert issue["status"] == "open" and issue["category"] == "property_condition" and issue["role"] == "guest" and issue["resolution"] is None
    assert "admin_note" not in issue and "blocks_payout" not in issue                                  # internal fields never reach the reporter
    ack = (await emails(db, "issue_received_reporter", "guest@example.com"))[0].body_text
    assert DESCRIPTION in ack and "property condition" in ack
    alert = (await emails(db, "issue_reported_admin", "admin@example.com"))[0].body_text
    assert w.b["reference"] in alert and "Beach House" in alert and DESCRIPTION in alert
    mine = await get(w.guest, "/me/issues")
    assert [i["id"] for i in mine] == [issue["id"]] and mine[0]["reference"] == w.b["reference"] and mine[0]["property"] == "Beach House"
    detail = await get(w.guest, f"/me/bookings/{w.id}")
    assert [i["id"] for i in detail["issues"]] == [issue["id"]] and detail["can_report_issue"] is True
    assert (await get(w.host, f"/host/bookings/{w.id}"))["issues"] == []                              # the host sees only their own reports


async def test_a_host_can_report_a_problem_with_host_categories(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    assert (await report(w.host, "host", w.id, category="property_condition")).status_code == 422       # that is a guest category
    assert (await report(w.guest, "guest", w.id, category="damage")).status_code == 422                 # and that is a host one
    r = await report(w.host, "host", w.id, category="damage", body="Broken window in the bedroom after the stay.")
    assert r.status_code == 201 and r.json()["role"] == "host"
    assert [i["category"] for i in await get(w.host, "/me/issues")] == ["damage"]
    assert await get(w.guest, "/me/issues") == []
    assert (await emails(db, "issue_received_reporter", "host@example.com"))[0].body_text
    assert await emails(db, "issue_received_reporter", "guest@example.com") == []


async def test_reports_are_validated_and_limited_to_the_two_people_on_a_paid_booking(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    assert (await report(w.guest, "guest", w.id, body="too short")).status_code == 422
    assert (await report(w.guest, "guest", w.id, category="nonsense")).status_code == 422
    assert (await report(w.guest, "guest", w.id, body="x" * 4000)).status_code == 422
    other = await complete_guest(new_client, "other@example.com")
    assert (await report(other, "guest", w.id)).status_code == 404
    assert (await report(w.host, "guest", w.id)).status_code == 404                                    # a host cannot file as the guest
    assert (await report(await new_client(), "guest", w.id)).status_code in (401, 403)
    unpaid = (await book(w.guest, w.unit, days=20)).json()
    r = await report(w.guest, "guest", unpaid["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert (await get(w.guest, f"/me/bookings/{unpaid['id']}"))["can_report_issue"] is False
    assert (await get(w.guest, "/meta/issue-categories"))["host"][0] == "guest_behaviour"


async def test_a_problem_can_be_reported_after_the_stay_or_a_cancellation(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, -60)
    async with SessionLocal() as s:
        await booking_service.advance_status(s)
        await s.commit()
    assert (await report(w.guest, "guest", w.id)).status_code == 201


async def test_a_problem_can_be_reported_after_a_cancellation(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await w.guest.post(f"{API}/me/bookings/{w.id}/cancel", json={}, headers=csrf_headers(w.guest))
    assert (await report(w.guest, "guest", w.id, category="payment_or_refund", body="I have not received my refund yet.")).status_code == 201


# ---------- the admin queue ----------

async def test_admins_work_an_issue_from_open_to_resolved_and_the_reporter_is_told(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    issue = (await report(w.guest, "guest", w.id)).json()
    queue = await get(w.admin, "/admin/issues?status=open")
    assert queue["total"] == 1
    i = queue["items"][0]
    assert i["reporter"]["email"] == "guest@example.com" and i["host"]["email"] == "host@example.com" and i["reference"] == w.b["reference"] and i["admin_note"] is None

    r = await patch(w.admin, issue["id"], status="in_progress", admin_note="Called the host, waiting for a reply.")
    assert r.status_code == 200 and r.json()["status"] == "in_progress" and r.json()["admin_note"] == "Called the host, waiting for a reply."
    assert "in progress" in (await emails(db, "issue_updated_reporter", "guest@example.com"))[0].body_text
    assert (await get(w.admin, "/admin/issues?status=open_work"))["total"] == 1

    assert (await patch(w.admin, issue["id"], status="resolved")).json()["error"]["code"] == "RESOLUTION_REQUIRED"
    done = await patch(w.admin, issue["id"], status="resolved", resolution="The host has fixed the heating and we have credited you.")
    assert done.status_code == 200 and done.json()["status"] == "resolved" and done.json()["resolved_at"]
    mails = [m.body_text for m in await emails(db, "issue_updated_reporter", "guest@example.com")]
    assert any("fixed the heating" in text and "resolved" in text for text in mails)

    mine = (await get(w.guest, "/me/issues"))[0]
    assert mine["status"] == "resolved" and mine["resolution"] == "The host has fixed the heating and we have credited you." and "admin_note" not in mine
    assert "waiting for a reply" not in str(await get(w.guest, f"/me/bookings/{w.id}"))               # internal notes stay internal
    assert (await patch(w.admin, issue["id"], status="dismissed", resolution="x")).status_code == 409
    assert (await get(w.admin, "/admin/issues?status=open_work"))["total"] == 0
    logged = (await db.execute(select(AuditLog).where(AuditLog.action == "issue.updated").order_by(AuditLog.id))).scalars().all()
    assert len(logged) == 2 and logged[-1].after["status"] == "resolved"        # the rejected attempt left no entry


async def test_an_issue_can_be_dismissed_with_a_message_and_status_changes_are_validated(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    issue = (await report(w.host, "host", w.id, category="other", body="Guest arrived two hours late without notice.")).json()
    assert (await patch(w.admin, issue["id"], status="open")).status_code == 422                       # not a status you can move to
    assert (await w.admin.patch(f"{API}/admin/issues/{issue['id']}", json={"status": "bogus"}, headers=csrf_headers(w.admin))).status_code == 422
    d = await patch(w.admin, issue["id"], status="dismissed", resolution="This is not something we can act on, but thank you for telling us.")
    assert d.status_code == 200 and d.json()["status"] == "dismissed"
    assert "not something we can act on" in (await emails(db, "issue_updated_reporter", "host@example.com"))[0].body_text
    assert (await patch(w.admin, issue["id"], status="in_progress")).status_code == 409
    assert (await patch(w.admin, "00000000-0000-0000-0000-000000000000", status="in_progress")).status_code == 404


async def test_the_queue_can_be_filtered_and_searched(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    a = (await report(w.guest, "guest", w.id, body="The heating did not work at all.")).json()
    b = (await report(w.host, "host", w.id, category="damage", body="Broken window in the bedroom.")).json()
    await patch(w.admin, b["id"], status="dismissed", resolution="Noted.")
    assert (await get(w.admin, "/admin/issues"))["total"] == 2
    assert [i["id"] for i in (await get(w.admin, "/admin/issues?status=open"))["items"]] == [a["id"]]
    assert [i["id"] for i in (await get(w.admin, "/admin/issues?status=dismissed"))["items"]] == [b["id"]]
    assert (await get(w.admin, "/admin/issues?q=heating"))["total"] == 1
    assert (await get(w.admin, f"/admin/issues?q={w.b['reference']}"))["total"] == 2
    assert (await get(w.admin, "/admin/issues?q=guest@exam"))["total"] == 1
    assert (await w.admin.get(f"{API}/admin/issues?status=bogus")).status_code == 422


async def test_admin_issue_screens_are_for_admins_only(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    issue = (await report(w.guest, "guest", w.id)).json()
    for client in (w.guest, w.host, await new_client()):
        assert (await client.get(f"{API}/admin/issues")).status_code in (401, 403)
        assert (await client.patch(f"{API}/admin/issues/{issue['id']}", json={"status": "in_progress"}, headers=csrf_headers(client))).status_code in (401, 403)


async def test_the_dashboard_counts_problems_that_are_still_open(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    issue = (await report(w.guest, "guest", w.id)).json()
    alert = {a["key"]: a for a in (await get(w.admin, "/admin/dashboard"))["alerts"]}["open_issues"]
    assert alert["count"] == 1 and alert["href"] == "/admin/issues" and alert["severity"] == "action"
    await patch(w.admin, issue["id"], status="resolved", resolution="Sorted.")
    assert {a["key"]: a for a in (await get(w.admin, "/admin/dashboard"))["alerts"]}["open_issues"]["count"] == 0


# ---------- an issue can hold the host's payout ----------

async def test_the_payout_waits_while_a_flagged_issue_is_open_and_goes_ahead_once_it_is_closed(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    issue = (await report(w.guest, "guest", w.id, category="safety", body="The balcony railing was loose and unsafe.")).json()
    await shift(db, w.id, -30)
    await patch(w.admin, issue["id"], blocks_payout=True, admin_note="Waiting for an inspection report")
    assert (await release())["paid"] == 0
    assert (await get(w.admin, f"/admin/issues?status=open"))["items"][0]["blocks_payout"] is True
    await patch(w.admin, issue["id"], status="in_progress")
    assert (await release())["paid"] == 0                                                              # still open work, still held
    await patch(w.admin, issue["id"], status="resolved", resolution="Inspection passed; the railing has been fixed.")
    assert (await get(w.admin, "/admin/issues?status=resolved"))["items"][0]["blocks_payout"] is False   # a closed issue never holds money
    assert (await release())["paid"] == 1
    assert len([c for c in Spy.calls if c[0] == "transfer"]) == 1


async def test_an_unflagged_issue_does_not_stop_the_payout(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await report(w.guest, "guest", w.id)
    await shift(db, w.id, -30)
    assert (await release())["paid"] == 1


async def test_a_closed_issue_cannot_be_flagged_again(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    issue = (await report(w.guest, "guest", w.id)).json()
    await patch(w.admin, issue["id"], status="dismissed", resolution="No action needed.")
    r = await patch(w.admin, issue["id"], blocks_payout=True)
    assert r.status_code == 409


# ---------- stay reminders ----------

async def reminders(now=None):
    async with SessionLocal() as s:
        sent = await booking_service.send_stay_reminders(s, now or datetime.now(UTC))
        await s.commit()
    return sent


async def test_a_check_in_reminder_goes_to_both_sides_once_when_the_stay_is_near(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    assert await reminders() == 0                                                                      # ten days away
    await shift(db, w.id, 30)
    assert await reminders() == 0                                                                      # 30 hours: outside the 24 hour default
    await shift(db, w.id, 20)
    assert await reminders() == 1
    guest = (await emails(db, "checkin_reminder_guest", "guest@example.com"))[0].body_text
    host = (await emails(db, "checkin_reminder_host", "host@example.com"))[0].body_text
    assert "host@example.com" in guest and "+61 400 111 222" in guest and w.b["reference"] in guest and "Australia/Sydney" in guest
    assert "Test Guest" in host and "guest@example.com" in host
    assert await reminders() == 0 and len(await emails(db, "checkin_reminder_guest")) == 1


async def test_the_reminder_time_follows_the_setting(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, 40)
    assert await reminders() == 0
    await w.admin.put(f"{API}/admin/settings/reminder.checkin_hours_before", json={"value": 48, "reason": "test"}, headers=csrf_headers(w.admin))
    assert await reminders() == 1


async def test_nobody_is_reminded_about_a_booking_that_is_not_confirmed_or_already_started(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    host2 = (await book(w.guest, w.unit, days=20)).json()                                              # only a request
    await db.execute(update(Booking).where(Booking.id == uuid.UUID(host2["id"])).values(check_in=datetime.now(UTC) + timedelta(hours=5)))
    await db.commit()
    await shift(db, w.id, -2)                                                                          # the confirmed one has already started
    assert await reminders() == 0
    assert await emails(db, "checkin_reminder_guest") == []


async def test_a_check_out_reminder_goes_to_the_guest_shortly_before_they_leave(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, -10)                                                                         # started, leaves in 14 hours
    assert await reminders() == 0
    await shift(db, w.id, -22)                                                                         # leaves in about 2 hours
    assert await reminders() == 1
    text = (await emails(db, "checkout_reminder_guest", "guest@example.com"))[0].body_text
    assert "Check-out is" in text and w.b["reference"] in text
    assert await emails(db, "checkout_reminder_guest", "host@example.com") == []
    assert await reminders() == 0


async def test_reminders_run_with_the_regular_minute_job(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, 10)
    async with SessionLocal() as s:
        sent = await booking_service.send_reminders(s)
        await s.commit()
    assert sent >= 1 and len(await emails(db, "checkin_reminder_guest")) == 1


# ---------- "Stripe needs more from you" ----------

def account_payload(account_id, *, ready: bool):
    return {"id": account_id, "details_submitted": ready, "payouts_enabled": ready, "capabilities": {"transfers": "active" if ready else "inactive"},
            "requirements": {"currently_due": [] if ready else ["external_account", "individual.verification.document"]}}


async def test_the_host_is_emailed_once_when_stripe_stops_letting_them_receive_money(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    account = (await row(db, HostProfile))[0].stripe_account_id
    async with SessionLocal() as s:
        await host_service.handle_account_updated(s, account_payload(account, ready=True))
        await s.commit()
    assert await emails(db, "host_stripe_action_needed") == []                                         # still fine: nothing to say
    async with SessionLocal() as s:
        await host_service.handle_account_updated(s, account_payload(account, ready=False))
        await s.commit()
    mails = await emails(db, "host_stripe_action_needed", "host@example.com")
    assert len(mails) == 1 and "external account" in mails[0].body_text and "/host/payouts" in mails[0].body_text
    async with SessionLocal() as s:
        await host_service.handle_account_updated(s, account_payload(account, ready=False))          # Stripe repeats itself: no second email
        await s.commit()
    assert len(await emails(db, "host_stripe_action_needed")) == 1
    async with SessionLocal() as s:                                                                    # fixed, then lost again: a new email
        await host_service.handle_account_updated(s, account_payload(account, ready=True))
        await host_service.handle_account_updated(s, account_payload(account, ready=False))
        await s.commit()
    assert len(await emails(db, "host_stripe_action_needed")) == 2


async def test_a_host_who_was_never_ready_gets_no_action_needed_email(new_client, make_admin, db):
    from tests.helpers import admin_client, approved_host_without_stripe

    admin = await admin_client(new_client, make_admin)
    host, _ = await approved_host_without_stripe(new_client, admin)
    await host.post(f"{API}/host/stripe/onboarding-link", headers=csrf_headers(host))
    account = (await row(db, HostProfile))[0].stripe_account_id
    async with SessionLocal() as s:
        await host_service.handle_account_updated(s, account_payload(account, ready=False))
        await s.commit()
    assert await emails(db, "host_stripe_action_needed") == []
