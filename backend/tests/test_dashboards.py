import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.modules.audit.models import AuditLog
from app.modules.bookings.models import Booking
from app.modules.notifications.models import EmailOutbox
from app.modules.payments.models import Payment, Refund, StripeEvent
from app.modules.properties.models import Property
from app.modules.users.models import User
from tests.conftest import PASSWORD, csrf_headers, register_and_verify
from tests.helpers import API, active_host, admin_client, build_listing, live_listing, submit_application
from tests.test_bookings import book, complete_guest, emails
from tests.test_cancellations import Spy, cancel, get, paid_world, shift, spy  # noqa: F401
from tests.test_payments import dev_complete, row
from tests.test_payouts import release


def parse(response) -> list[list[str]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv") and "attachment" in response.headers["content-disposition"]
    return list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))


# ---------- host dashboard ----------

async def test_the_host_dashboard_summarises_requests_stays_listings_and_money(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    waiting = (await book(w.guest, w.unit, days=20)).json()
    d = await get(w.host, "/host/dashboard")
    assert d["state"] == "active" and d["requests"] == {"pending": 1, "awaiting_payment": 0}
    assert [s["id"] for s in d["upcoming_stays"]] == [w.id] and d["current_stays"] == []
    assert d["listings"] == {"approved": 1}
    assert d["money"]["scheduled"] == 22000 and d["money"]["paid_out"] == 0 and d["money"]["next_payout"]["amount"] == 22000
    assert any("1 booking request" in m for m in d["needs_attention"])
    await w.host.post(f"{API}/host/bookings/{waiting['id']}/reject", json={}, headers=csrf_headers(w.host))
    assert (await get(w.host, "/host/dashboard"))["requests"]["pending"] == 0

    await shift(db, w.id, -2)                                                   # the stay has started
    d = await get(w.host, "/host/dashboard")
    assert [s["id"] for s in d["current_stays"]] == [w.id] and d["upcoming_stays"] == []


async def test_a_host_whose_stripe_access_was_lost_is_told_on_the_dashboard(new_client, make_admin, db):
    from app.modules.hosts.models import HostProfile

    admin = await admin_client(new_client, make_admin)
    host, _ = await active_host(new_client, admin)
    await db.execute(update(HostProfile).values(stripe_payouts_enabled=False))
    await db.commit()
    d = await get(host, "/host/dashboard")
    assert d["state"] == "restricted" and any("Stripe needs more information" in m for m in d["needs_attention"])


async def test_dashboards_need_a_host_account(new_client, make_admin):
    guest = await complete_guest(new_client)
    assert (await guest.get(f"{API}/host/dashboard")).status_code == 404
    assert (await guest.get(f"{API}/host/money")).status_code == 404
    assert (await (await new_client()).get(f"{API}/host/dashboard")).status_code in (401, 403)


# ---------- host money ----------

async def test_the_money_page_lists_earnings_deductions_and_payout_state_without_the_guests_total(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin, days=10)
    await shift(db, w.id, -30)
    assert (await release())["paid"] == 1
    m = await get(w.host, "/host/money")
    (item,) = m["items"]
    assert item["reference"] == w.b["reference"] and item["gross"] == 25000 and item["commission"] == 3000 and item["host_share"] == 22000
    assert item["net"] == 22000 and item["deductions"] == 0 and item["payout_state"] == "paid" and item["transferred_at"]
    assert m["summary"]["paid_out"] == 22000 and m["summary"]["scheduled"] == 0 and m["summary"]["next_payout"] is None
    for hidden in ("total", "tax", "service_fee", "guest", "guest_email", "price"):
        assert hidden not in item                                               # the host sees earnings, not what the guest paid


async def test_scheduled_money_and_the_next_payout_are_shown_and_deductions_come_off(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin, quantity=2)
    second = (await book(w.guest, w.unit, days=14)).json()
    await w.host.post(f"{API}/host/bookings/{second['id']}/accept", headers=csrf_headers(w.host))
    await dev_complete(w.guest, second["id"])
    req = (await w.host.post(f"{API}/host/bookings/{second['id']}/refund-request", json={"amount": 2000, "reason": "goodwill"}, headers=csrf_headers(w.host))).json()
    await w.admin.post(f"{API}/admin/refund-requests/{req['id']}/decision", json={"decision": "approve"}, headers=csrf_headers(w.admin))
    m = await get(w.host, "/host/money")
    by_ref = {i["reference"]: i for i in m["items"]}
    assert by_ref[second["reference"]]["deductions"] == 2000 and by_ref[second["reference"]]["net"] == 22000 - 2000
    assert m["summary"]["scheduled"] == 22000 + 20000 and m["summary"]["deductions"] == 2000
    assert m["summary"]["next_payout"]["reference"] == w.b["reference"]          # the earliest one is due first
    this_month = sum(i["net"] for i in m["items"] if datetime.fromisoformat(i["check_in"]).strftime("%Y-%m") == datetime.now(UTC).strftime("%Y-%m"))
    assert m["summary"]["this_month"] == this_month


async def test_the_month_filter_and_paging_apply_to_the_list_but_not_the_summary(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    month = datetime.fromisoformat((await get(w.host, "/host/money"))["items"][0]["check_in"]).strftime("%Y-%m")
    other = "1999-01"
    assert (await get(w.host, f"/host/money?month={month}"))["total"] == 1
    empty = await get(w.host, f"/host/money?month={other}")
    assert empty["total"] == 0 and empty["items"] == [] and empty["summary"]["scheduled"] == 22000
    assert (await w.host.get(f"{API}/host/money?month=2026-13")).status_code == 422


async def test_a_host_never_sees_another_hosts_money(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    other, _ = await active_host(new_client, w.admin, "rival@example.com")
    assert (await get(other, "/host/money"))["items"] == []
    assert (await get(other, "/host/money"))["summary"]["scheduled"] == 0
    assert parse(await other.get(f"{API}/host/money/export.csv"))[1:] == []


async def test_the_host_can_download_their_earnings_as_a_spreadsheet(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    rows = parse(await w.host.get(f"{API}/host/money/export.csv"))
    assert rows[0] == ["reference", "property", "check_in", "nights", "status", "currency", "gross", "commission", "host_share", "deductions", "net", "payout_state", "transferred_at"]
    r = dict(zip(rows[0], rows[1]))
    assert r["reference"] == w.b["reference"] and r["gross"] == "250.00" and r["commission"] == "30.00" and r["host_share"] == "220.00" and r["net"] == "220.00" and r["payout_state"] == "scheduled"
    month = datetime.fromisoformat(r["check_in"]).strftime("%Y-%m")
    assert len(parse(await w.host.get(f"{API}/host/money/export.csv?month={month}"))) == 2
    assert len(parse(await w.host.get(f"{API}/host/money/export.csv?month=1999-01"))) == 1


# ---------- admin dashboard ----------

async def test_the_admin_dashboard_counts_everything_that_needs_attention(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin, quantity=4)
    # a host application and a listing waiting for review
    applicant = await new_client()
    await submit_application(applicant, "applicant@example.com")
    host2, _ = await active_host(new_client, w.admin, "second@example.com")
    prop = await build_listing(host2, name="Waiting")
    await host2.post(f"{API}/host/properties/{prop['id']}/submit", headers=csrf_headers(host2))
    # a refund request, a failed refund, a failed payment, a failed payout, a failed webhook, a hold
    req = await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 1000, "reason": "x"}, headers=csrf_headers(w.host))
    assert req.status_code == 201
    b2 = (await book(w.guest, w.unit, days=15)).json()
    await w.host.post(f"{API}/host/bookings/{b2['id']}/accept", headers=csrf_headers(w.host))
    await dev_complete(w.guest, b2["id"])
    Spy.fail = {"refund"}
    await cancel(w.guest, b2["id"])
    await db.execute(update(Payment).where(Payment.booking_id == uuid.UUID(w.id)).values(status="failed"))
    db.add(StripeEvent(event_id="evt_bad", type="payment_intent.succeeded", payload={}, error="boom"))
    await db.commit()
    await w.admin.post(f"{API}/admin/bookings/{w.id}/payout-hold", json={"hold": True, "note": "x"}, headers=csrf_headers(w.admin))

    d = await get(w.admin, "/admin/dashboard")
    a = {x["key"]: x for x in d["alerts"]}
    assert a["applications"]["count"] == 1 and a["listings"]["count"] == 1 and a["refund_requests"]["count"] == 1 and a["failed_refunds"]["count"] == 1
    assert a["failed_payments"]["count"] == 1 and a["failed_webhooks"]["count"] == 1 and a["held_payouts"]["count"] == 1
    assert a["applications"]["severity"] == "action" and a["failed_refunds"]["severity"] == "problem" and a["suspended_users"]["severity"] == "none"
    assert a["failed_refunds"]["href"] == "/admin/refunds" and a["applications"]["href"] == "/admin/hosts"
    assert d["bookings"]["upcoming"] == 1 and d["bookings"]["cancelled_30_days"] == 1
    assert d["totals"]["hosts"] == 3 and d["totals"]["users"] >= 5 and d["recent"]


async def test_the_dashboard_money_figures_come_from_payments_refunds_and_stored_fees(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    m = (await get(w.admin, "/admin/dashboard"))["money_30_days"]
    assert m == {"gross_paid": 29563, "refunded": 0, "net_collected": 29563, "service_fees": 1875, "commission": 3000}
    await cancel(w.guest, w.id)
    m = (await get(w.admin, "/admin/dashboard"))["money_30_days"]
    assert m["gross_paid"] == 29563 and m["refunded"] == 27500 and m["net_collected"] == 2063


async def test_hosts_at_the_cancellation_alert_level_are_counted(new_client, make_admin, db, spy):
    admin = await admin_client(new_client, make_admin)
    host, _ = await active_host(new_client, admin)
    prop = await live_listing(host, admin, quantity=4)
    guest = await complete_guest(new_client)
    for d in (10, 12, 14):
        b = (await book(guest, prop["units"][0], days=d)).json()
        await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
        await dev_complete(guest, b["id"])
        await host.post(f"{API}/host/bookings/{b['id']}/cancel", json={"reason": "no"}, headers=csrf_headers(host))
    a = {x["key"]: x for x in (await get(admin, "/admin/dashboard"))["alerts"]}
    assert a["host_cancellations"]["count"] == 1 and a["host_cancellations"]["severity"] == "problem"


async def test_the_admin_screens_are_for_admins_only(new_client, make_admin):
    w = await paid_world(new_client, make_admin)
    for path in ("/admin/dashboard", "/admin/payments", "/admin/payouts", "/admin/webhooks", "/admin/users", f"/admin/bookings/{w.id}", "/admin/exports/bookings.csv"):
        assert (await w.guest.get(f"{API}{path}")).status_code == 403, path
        assert (await w.host.get(f"{API}{path}")).status_code == 403, path
        assert (await (await new_client()).get(f"{API}{path}")).status_code in (401, 403), path


# ---------- admin lists ----------

async def test_payments_can_be_filtered_and_searched_by_reference_email_and_provider_id(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    everything = await get(w.admin, "/admin/payments")
    (p,) = everything["items"]
    assert p["reference"] == w.b["reference"] and p["guest"] == "guest@example.com" and p["amount"] == 29563 and p["status"] == "succeeded" and p["intent_id"].startswith("pi_fake_")
    assert (await get(w.admin, "/admin/payments?status=failed"))["total"] == 0
    for q in (w.b["reference"], "guest@exam", p["intent_id"][:10], p["charge_id"]):
        assert (await get(w.admin, f"/admin/payments?q={q}"))["total"] == 1, q
    assert (await get(w.admin, "/admin/payments?q=nobody"))["total"] == 0
    assert (await w.admin.get(f"{API}/admin/payments?status=bogus")).status_code == 422


async def test_payouts_are_listed_by_state_with_failures_visible(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin, quantity=3)
    due = (await book(w.guest, w.unit, days=14)).json()
    await w.host.post(f"{API}/host/bookings/{due['id']}/accept", headers=csrf_headers(w.host))
    await dev_complete(w.guest, due["id"])
    await shift(db, due["id"], -30)
    Spy.fail = {"transfer"}
    await release()
    scheduled = await get(w.admin, "/admin/payouts?state=scheduled")
    failed = await get(w.admin, "/admin/payouts?state=failed")
    assert [i["reference"] for i in scheduled["items"]] == [w.b["reference"]] and scheduled["items"][0]["amount"] == 22000
    assert [i["reference"] for i in failed["items"]] == [due["reference"]] and "refused" in failed["items"][0]["error"] and failed["items"][0]["host"] == "Test Guest"
    Spy.fail = set()
    await w.admin.post(f"{API}/admin/bookings/{due['id']}/payout-retry", headers=csrf_headers(w.admin))
    assert (await release(datetime.now(UTC) + timedelta(minutes=1)))["paid"] == 1
    paid = await get(w.admin, "/admin/payouts?state=paid")
    assert [i["reference"] for i in paid["items"]] == [due["reference"]] and paid["items"][0]["stripe_transfer_id"].startswith("tr_spy_")
    await w.admin.post(f"{API}/admin/bookings/{w.id}/payout-hold", json={"hold": True, "note": "dispute"}, headers=csrf_headers(w.admin))
    assert [i["reference"] for i in (await get(w.admin, "/admin/payouts?state=held"))["items"]] == [w.b["reference"]]
    assert (await get(w.admin, "/admin/payouts"))["total"] == 2


async def test_failed_webhooks_are_listed_and_can_be_run_again(new_client, make_admin, db, spy, monkeypatch):
    w = await paid_world(new_client, make_admin)
    (payment,) = await row(db, Payment)
    event = {"id": "evt_retry", "type": "payment_intent.payment_failed", "data": {"object": {"id": payment.stripe_payment_intent_id, "status": "requires_payment_method", "amount": 29563, "currency": "aud",
             "last_payment_error": {"code": "card_declined", "message": "declined"}}}}
    db.add(StripeEvent(event_id="evt_retry", type=event["type"], payload=event, error="database was down"))
    db.add(StripeEvent(event_id="evt_fine", type="account.updated", payload={}, processed_at=datetime.now(UTC)))
    await db.commit()
    failed = await get(w.admin, "/admin/webhooks")
    assert [e["event_id"] for e in failed["items"]] == ["evt_retry"] and failed["items"][0]["error"] == "database was down"
    assert {e["event_id"] for e in (await get(w.admin, "/admin/webhooks?failed=false"))["items"]} == {"evt_retry", "evt_fine"}
    eid = failed["items"][0]["id"]
    ok = await w.admin.post(f"{API}/admin/webhooks/{eid}/reprocess", headers=csrf_headers(w.admin))
    assert ok.status_code == 200 and ok.json() == {"id": eid, "processed": True, "error": None}
    assert (await get(w.admin, "/admin/webhooks"))["total"] == 0
    assert (await w.admin.post(f"{API}/admin/webhooks/{uuid.uuid4()}/reprocess", headers=csrf_headers(w.admin))).status_code == 404
    audit = (await db.execute(select(AuditLog).where(AuditLog.action == "webhook.reprocessed"))).scalars().all()
    assert len(audit) == 1


async def test_a_webhook_that_still_fails_stays_in_the_failed_list(new_client, make_admin, db, spy, monkeypatch):
    w = await paid_world(new_client, make_admin)
    from app.modules.dashboards import router as dash_router

    async def boom(session, event):
        raise RuntimeError("still broken")

    monkeypatch.setattr(dash_router, "dispatch_event", boom)
    db.add(StripeEvent(event_id="evt_x", type="account.updated", payload={"data": {"object": {}}}, error="first failure"))
    await db.commit()
    eid = (await get(w.admin, "/admin/webhooks"))["items"][0]["id"]
    r = await w.admin.post(f"{API}/admin/webhooks/{eid}/reprocess", headers=csrf_headers(w.admin))
    assert r.json()["processed"] is False and r.json()["error"] == "still broken"
    assert (await get(w.admin, "/admin/webhooks"))["total"] == 1


# ---------- admin booking screens ----------

async def test_the_booking_detail_shows_the_whole_story(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, 60)
    await cancel(w.guest, w.id)
    d = await get(w.admin, f"/admin/bookings/{w.id}")
    assert d["status"] == "cancelled" and d["guest"]["email"] == "guest@example.com" and d["host"]["email"] == "host@example.com"
    assert [(t["from"], t["to"]) for t in d["timeline"]] == [("requested", "accepted"), ("accepted", "payment_pending"), ("payment_pending", "confirmed"), ("confirmed", "cancelled")]
    assert d["timeline"][0]["actor"] == "host@example.com" and d["timeline"][-1]["actor"] == "guest@example.com"
    assert [p["status"] for p in d["payments"]] == ["succeeded"] and d["payments"][0]["amount"] == 29563
    assert [(r["type"], r["amount"], r["status"]) for r in d["refunds"]] == [("policy", 13750, "succeeded")]
    assert d["invoice"]["number"] == "INV-000001" and d["settlement"]["host_payable"] == 11000 and d["snapshot"]["total"] == 29563
    assert {a["action"] for a in d["audit"]} >= {"booking.requested", "booking.accepted", "payment.succeeded", "booking.cancelled_by_guest"}
    assert (await w.admin.get(f"{API}/admin/bookings/{uuid.uuid4()}")).status_code == 404


async def test_bookings_can_be_found_by_reference_person_or_property(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    found = lambda q: get(w.admin, f"/admin/bookings?q={q}")  # noqa: E731
    for q in (w.b["reference"], w.b["reference"].lower()[:6], "guest@exam", "Test Guest", "host@exam", "Beach"):
        assert (await found(q))["total"] == 1, q
    assert (await found("zzz-nobody"))["total"] == 0
    assert (await get(w.admin, "/admin/bookings?status=confirmed"))["total"] == 1


async def test_a_guest_can_list_just_their_cancelled_bookings(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin, quantity=2)
    second = (await book(w.guest, w.unit, days=14)).json()
    await cancel(w.guest, w.id)
    assert [i["id"] for i in (await get(w.guest, "/me/bookings?status=cancelled"))["items"]] == [w.id]
    assert [i["id"] for i in (await get(w.host, "/host/bookings?status=cancelled"))["items"]] == [w.id]
    assert [i["id"] for i in (await get(w.guest, "/me/bookings?status=requested"))["items"]] == [second["id"]]


# ---------- users ----------

async def test_users_can_be_searched_filtered_and_inspected(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    everyone = await get(w.admin, "/admin/users")
    assert everyone["total"] == 3
    by_email = {u["email"]: u for u in everyone["items"]}
    assert by_email["host@example.com"]["is_host"] and by_email["host@example.com"]["host_state"] == "active" and by_email["admin@example.com"]["is_admin"]
    assert by_email["guest@example.com"]["bookings"] == 1 and not by_email["guest@example.com"]["is_host"]
    assert [u["email"] for u in (await get(w.admin, "/admin/users?q=guest@"))["items"]] == ["guest@example.com"]
    assert [u["email"] for u in (await get(w.admin, "/admin/users?role=host"))["items"]] == ["host@example.com"]
    assert [u["email"] for u in (await get(w.admin, "/admin/users?role=guest"))["items"]] == ["guest@example.com"]
    assert [u["email"] for u in (await get(w.admin, "/admin/users?role=admin"))["items"]] == ["admin@example.com"]
    assert (await get(w.admin, "/admin/users?status=suspended"))["total"] == 0

    uid = by_email["guest@example.com"]["id"]
    d = await get(w.admin, f"/admin/users/{uid}")
    assert d["email"] == "guest@example.com" and d["phone"] == "+61 400 222 333" and d["email_verified"] is True and d["social_links"][0]["platform"] == "Instagram"
    assert [b["id"] for b in d["recent_bookings"]] == [w.id] and d["recent_bookings"][0]["guest"]["email"] == "guest@example.com"
    assert any(h["action"] == "booking.requested" for h in d["history"])
    assert (await w.admin.get(f"{API}/admin/users/{uuid.uuid4()}")).status_code == 404


async def test_suspending_a_user_signs_them_out_blocks_login_and_emails_them(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    uid = str((await db.execute(select(User.id).where(User.email == "guest@example.com"))).scalar_one())
    h = csrf_headers(w.admin)
    assert (await w.admin.post(f"{API}/admin/users/{uid}/suspend", json={"reason": " "}, headers=h)).status_code == 422
    r = await w.admin.post(f"{API}/admin/users/{uid}/suspend", json={"reason": "Fraud report"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    assert (await w.guest.get(f"{API}/me")).status_code in (401, 403)                       # their open session no longer works
    fresh = await new_client()
    login = await fresh.post(f"{API}/auth/login", json={"email": "guest@example.com", "password": PASSWORD})
    assert login.status_code == 403 and login.json()["error"]["code"] == "ACCOUNT_SUSPENDED"
    assert "Fraud report" in (await emails(db, "account_suspended", "guest@example.com"))[0].body_text
    assert (await w.admin.post(f"{API}/admin/users/{uid}/suspend", json={"reason": "again"}, headers=h)).status_code == 409
    logged = (await db.execute(select(AuditLog).where(AuditLog.action == "user.suspended"))).scalars().all()
    assert len(logged) == 1 and logged[0].reason == "Fraud report" and logged[0].entity_id == uid

    back = await w.admin.post(f"{API}/admin/users/{uid}/reactivate", json={"reason": "Resolved"}, headers=h)
    assert back.status_code == 200 and back.json()["status"] == "active"
    assert (await fresh.post(f"{API}/auth/login", json={"email": "guest@example.com", "password": PASSWORD})).status_code == 200
    assert len(await emails(db, "account_reactivated", "guest@example.com")) == 1
    assert (await w.admin.post(f"{API}/admin/users/{uid}/reactivate", json={}, headers=h)).status_code == 409


async def test_admins_cannot_be_suspended_and_cannot_suspend_themselves(new_client, make_admin, db):
    admin = await admin_client(new_client, make_admin)
    other = await make_admin("second-admin@example.com")
    me = str((await db.execute(select(User.id).where(User.email == "admin@example.com"))).scalar_one())
    h = csrf_headers(admin)
    assert (await admin.post(f"{API}/admin/users/{me}/suspend", json={"reason": "x"}, headers=h)).json()["error"]["code"] == "CANNOT_SUSPEND_SELF"
    assert (await admin.post(f"{API}/admin/users/{other.id}/suspend", json={"reason": "x"}, headers=h)).json()["error"]["code"] == "CANNOT_SUSPEND_ADMIN"


# ---------- exports ----------

async def test_every_export_has_the_documented_columns_and_correct_amounts(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, -30)
    await release()
    exports = {kind: parse(await w.admin.get(f"{API}/admin/exports/{kind}.csv")) for kind in ("bookings", "payments", "refunds", "payouts")}
    b = dict(zip(exports["bookings"][0], exports["bookings"][1]))
    assert b["reference"] == w.b["reference"] and b["guest_email"] == "guest@example.com" and b["host_email"] == "host@example.com" and b["property"] == "Beach House"
    assert (b["accommodation"], b["service_fee"], b["tax"], b["total"], b["commission"], b["host_payable"]) == ("250.00", "18.75", "26.88", "295.63", "30.00", "220.00")
    p = dict(zip(exports["payments"][0], exports["payments"][1]))
    assert p["amount"] == "295.63" and p["status"] == "succeeded" and p["stripe_charge"].startswith("ch_fake_")
    t = dict(zip(exports["payouts"][0], exports["payouts"][1]))
    assert t["amount"] == "220.00" and t["status"] == "transferred" and t["host_email"] == "host@example.com"
    assert exports["refunds"] == [["refund_id", "booking", "type", "amount", "currency", "status", "stripe_refund_id", "host_reversal_amount", "reason", "error", "created_at"]]
    await w.admin.post(f"{API}/admin/bookings/{w.id}/cancel", json={"reason": "Fraud", "refund": "full"}, headers=csrf_headers(w.admin))
    refunds = parse(await w.admin.get(f"{API}/admin/exports/refunds.csv"))
    r = dict(zip(refunds[0], refunds[1]))
    assert r["amount"] == "295.63" and r["type"] == "admin" and r["host_reversal_amount"] == "220.00"


async def test_exports_can_be_limited_by_date_and_each_download_is_audited(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    today = datetime.now(UTC).date()
    assert len(parse(await w.admin.get(f"{API}/admin/exports/bookings.csv?from={today}&to={today}"))) == 2
    assert len(parse(await w.admin.get(f"{API}/admin/exports/bookings.csv?from={today + timedelta(days=1)}"))) == 1
    assert len(parse(await w.admin.get(f"{API}/admin/exports/bookings.csv?to={today - timedelta(days=1)}"))) == 1
    assert (await w.admin.get(f"{API}/admin/exports/bookings.csv?from=yesterday")).status_code == 422
    assert (await w.admin.get(f"{API}/admin/exports/secrets.csv")).status_code == 404
    entries = (await db.execute(select(AuditLog).where(AuditLog.action == "export.downloaded").order_by(AuditLog.id))).scalars().all()
    assert [e.entity_id for e in entries] == ["bookings"] * 3 and entries[0].after["rows"] == 1 and entries[1].after["rows"] == 0


async def test_text_that_a_spreadsheet_would_run_as_a_formula_is_neutralised(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await db.execute(update(User).where(User.email == "guest@example.com").values(full_name='=HYPERLINK("http://evil.example","click")'))
    await db.execute(update(Booking).values(closed_reason="+cmd|' /C calc'!A0"))
    await db.execute(update(Property).values(name="@SUM(1+1)"))
    await db.commit()
    rows = parse(await w.admin.get(f"{API}/admin/exports/bookings.csv"))
    b = dict(zip(rows[0], rows[1]))
    assert b["guest_name"].startswith("'=HYPERLINK") and b["closed_reason"].startswith("'+cmd") and b["property"] == "'@SUM(1+1)"
    host_rows = parse(await w.host.get(f"{API}/host/money/export.csv"))
    assert host_rows[1][1] == "'@SUM(1+1)"
