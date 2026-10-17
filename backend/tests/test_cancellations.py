import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update

from app.core.db import SessionLocal
from app.modules.bookings.models import Booking
from app.modules.cancellations.policy import calculate_refund, pct_for
from app.modules.cancellations.service import validate_tiers
from app.modules.hosts.models import HostProfile
from app.modules.notifications.models import EmailOutbox
from app.modules.payments import refunds as refund_module
from app.modules.payments import stripe_gateway as gw
from app.modules.payments.models import Refund, RefundRequest
from app.modules.payments.stripe_gateway import RefundInfo
from app.modules.pricing.service import TaxSpec, compute_price
from tests.conftest import csrf_headers
from tests.helpers import API, admin_client, active_host, live_listing
from tests.test_bookings import book, complete_guest, emails
from tests.test_payments import dev_complete, row

TAX = TaxSpec("GST", 1000, False, ("accommodation", "cleaning", "service_fee"))
MODERATE = [{"hours_before": 120, "refund_pct": 100}, {"hours_before": 24, "refund_pct": 50}]
NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


def pricing(**kw):
    args = dict(price_per_24h=25000, hours=24, cleaning_fee=0, service_fee_percent=7.5, tax=TAX, commission_percent=12.0)
    args.update(kw)
    return compute_price(**args)


def quote(hours_before: float, mode="policy", tiers=MODERATE, svc_refundable=False, **kw):
    return calculate_refund(pricing=pricing(**kw), tiers=tiers, policy_name="Moderate", check_in=NOW + timedelta(hours=hours_before), now=NOW,
                            mode=mode, service_fee_refundable=svc_refundable)


# ---------- the refund maths (pure) ----------

class TestPolicyTiers:
    def test_the_percentage_steps_down_as_check_in_gets_closer(self):
        assert [pct_for(MODERATE, h) for h in (500, 120, 119.9, 24, 23.9, 1, 0)] == [100, 100, 50, 50, 0, 0, 0]

    def test_no_tiers_means_no_refund(self):
        assert pct_for([], 1000) == 0

    def test_tiers_are_validated(self):
        assert validate_tiers([{"hours_before": 24, "refund_pct": 50}, {"hours_before": 120, "refund_pct": 100}]) == [
            {"hours_before": 120, "refund_pct": 100}, {"hours_before": 24, "refund_pct": 50}]
        for bad in ("x", [{"hours_before": 1}], [{"hours_before": -1, "refund_pct": 10}], [{"hours_before": 5, "refund_pct": 101}],
                    [{"hours_before": 24, "refund_pct": 10}, {"hours_before": 120, "refund_pct": 5}],       # refund going up as check-in nears
                    [{"hours_before": 24, "refund_pct": 10}, {"hours_before": 24, "refund_pct": 5}],
                    [{"hours_before": 1.5, "refund_pct": 10}], [{"hours_before": i, "refund_pct": 0} for i in range(8)]):
            with pytest.raises(Exception) as err:
                validate_tiers(bad)
            assert getattr(err.value, "code", "") == "INVALID_TIERS"


class TestRefundAmounts:
    def test_full_refund_window_returns_accommodation_and_its_tax_but_not_the_service_fee(self):
        q = quote(200)
        assert (q.refund_pct, q.accommodation, q.service_fee) == (100, 25000, 0)
        assert q.tax == 2500                      # 10% of the accommodation: the tax on the service fee is not returned
        assert q.total == 27500
        assert (q.host_accommodation, q.host_commission, q.host_payable) == (0, 0, 0)

    def test_half_refund_window_splits_between_guest_and_host(self):
        q = quote(60)
        assert (q.refund_pct, q.accommodation, q.tax, q.total) == (50, 12500, 1250, 13750)
        assert (q.host_accommodation, q.host_commission, q.host_payable) == (12500, 1500, 11000)   # 12% commission on what the host keeps

    def test_inside_the_last_window_nothing_is_refunded_and_the_host_keeps_it_all(self):
        q = quote(10)
        assert (q.refund_pct, q.total) == (0, 0)
        assert q.host_payable == 25000 - 3000

    def test_after_check_in_nothing_is_refunded(self):
        assert quote(-5).total == 0

    def test_a_refundable_service_fee_is_refunded_by_the_same_percentage(self):
        q = quote(200, svc_refundable=True)
        assert (q.service_fee, q.tax, q.total) == (1875, 2688, 29563)        # everything: exactly what was paid
        half = quote(60, svc_refundable=True)
        assert half.service_fee == 938 and half.total == 14782                # half of 29563, rounded

    def test_cleaning_fee_follows_the_percentage_and_the_commission_base(self):
        q = quote(60, cleaning_fee=4000)
        assert q.cleaning_fee == 2000 and q.host_cleaning_fee == 2000 and q.host_commission == 1500   # commission on accommodation only
        both = quote(60, cleaning_fee=4000, commission_base="accommodation_and_cleaning")
        assert both.host_commission == round((12500 + 2000) * 0.12) and both.host_payable == 12500 + 2000 - both.host_commission

    def test_full_mode_returns_exactly_what_was_paid_and_pays_the_host_nothing(self):
        p = pricing(cleaning_fee=4000)
        q = calculate_refund(pricing=p, tiers=MODERATE, policy_name="x", check_in=NOW + timedelta(hours=1), now=NOW, mode="full")
        assert q.total == p["total"] and q.refund_pct == 100 and q.host_payable == 0

    def test_tax_inclusive_prices_do_not_get_tax_added_on_top(self):
        inc = TaxSpec("GST", 1000, True, ("accommodation", "cleaning", "service_fee"))
        q = quote(200, tax=inc)
        assert q.tax == 0 and q.total == 25000
        assert quote(200, tax=inc, mode="full").total == pricing(tax=inc)["total"]

    def test_the_refund_can_never_exceed_what_was_paid(self):
        for h in (500, 100, 30, 5):
            for svc in (True, False):
                assert quote(h, svc_refundable=svc).total <= pricing()["total"]

    def test_older_bookings_without_stored_tax_details_are_treated_as_taxed_on_everything(self):
        p = pricing()
        for key in ("tax_applies_to", "commission_base", "rounding"):
            p.pop(key)
        q = calculate_refund(pricing=p, tiers=MODERATE, policy_name="x", check_in=NOW + timedelta(hours=200), now=NOW)
        assert q.total == 27500


# ---------- helpers for the flow tests ----------

async def paid_world(new_client, make_admin, *, days=10, quantity=1, guest_email="guest@example.com"):
    """A confirmed, paid booking. Returns a namespace of everything a test needs."""
    admin = await admin_client(new_client, make_admin)
    host, host_id = await active_host(new_client, admin)
    prop = await live_listing(host, admin, quantity=quantity)
    unit = prop["units"][0]
    guest = await complete_guest(new_client, guest_email)
    b = (await book(guest, unit, days=days)).json()
    assert (await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))).status_code == 200
    assert (await dev_complete(guest, b["id"])).json()["outcome"] == "confirmed"
    return type("W", (), dict(admin=admin, host=host, host_id=host_id, prop=prop, unit=unit, guest=guest, b=b, id=b["id"]))


async def shift(db, booking_id: str, hours_from_now: float, nights: int = 1):
    """Moves a booking's stay so check-in is `hours_from_now` away (negative = already started)."""
    ci = datetime.now(UTC) + timedelta(hours=hours_from_now)
    co = ci + timedelta(hours=24 * nights)
    await db.execute(update(Booking).where(Booking.id == uuid.UUID(booking_id)).values(check_in=ci, check_out=co))
    await db.execute(text("update room_occupancy set period = tstzrange(:a, :b, '[)') where booking_id = :id"), {"a": ci, "b": co, "id": booking_id})
    await db.commit()


async def cancel(guest, booking_id, expected=None):
    return await guest.post(f"{API}/me/bookings/{booking_id}/cancel", json={"expected_refund": expected}, headers=csrf_headers(guest))


async def get(client, path):
    r = await client.get(f"{API}{path}")
    assert r.status_code == 200, r.text
    return r.json()


class Spy(gw.FakeStripeGateway):
    """A gateway that remembers every call, and can be told to fail or answer differently."""

    calls: list = []
    refund_status = "succeeded"
    fail: set = set()

    def refund_payment(self, **kw):
        Spy.calls.append(("refund", kw))
        if "refund" in Spy.fail:
            raise RuntimeError("stripe refused the refund")
        return RefundInfo(id=f"re_spy_{len(Spy.calls)}", status=Spy.refund_status)

    def reverse_transfer(self, **kw):
        Spy.calls.append(("reverse", kw))
        if "reverse" in Spy.fail:
            raise RuntimeError("cannot reverse")
        return f"trr_spy_{len(Spy.calls)}"

    def create_transfer(self, **kw):
        Spy.calls.append(("transfer", kw))
        if "transfer" in Spy.fail:
            raise RuntimeError("transfer refused")
        return f"tr_spy_{len(Spy.calls)}"


@pytest.fixture
def spy(monkeypatch):
    Spy.calls, Spy.refund_status, Spy.fail = [], "succeeded", set()
    from app.modules.payouts import service as payouts_service

    for module in (refund_module, payouts_service):
        monkeypatch.setattr(module, "get_gateway", lambda: Spy())
    return Spy


# ---------- guest cancels ----------

async def test_the_preview_shows_the_policy_hours_and_amounts(new_client, make_admin, db):
    w = await paid_world(new_client, make_admin)
    p = await get(w.guest, f"/me/bookings/{w.id}/cancel-preview")
    assert p["policy"]["name"] == "Moderate" and p["policy"]["tiers"] == MODERATE and p["paid"] == 29563
    assert p["refund"]["refund_pct"] == 100 and p["refund"]["total"] == 27500 and p["refund"]["service_fee"] == 0
    assert p["service_fee_refundable"] is False and p["refund"]["hours_before"] > 200
    await shift(db, w.id, 60)
    p = await get(w.guest, f"/me/bookings/{w.id}/cancel-preview")
    assert p["refund"]["refund_pct"] == 50 and p["refund"]["total"] == 13750


async def test_cancelling_early_refunds_by_policy_frees_the_dates_and_emails_both(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    r = await cancel(w.guest, w.id, expected=27500)
    assert r.status_code == 200, r.text
    mine = r.json()
    assert mine["status"] == "cancelled" and mine["cancelled"]["by"] == "guest" and mine["cancelled"]["refund_pct"] == 100
    assert mine["refund"] == {"refunded": 27500, "pending": 0, "failed": 0, "state": "refunded"}

    (refund,) = await row(db, Refund)
    assert refund.type == "policy" and refund.amount == 27500 and refund.status == "succeeded" and refund.stripe_refund_id.startswith("re_spy_")
    assert [c[0] for c in Spy.calls] == ["refund"] and Spy.calls[0][1]["amount"] == 27500 and Spy.calls[0][1]["idempotency_key"] == f"refund-{refund.id}"

    anon = await new_client()
    q = (await anon.post(f"{API}/units/{w.unit['id']}/quote", json={"checkin_local": w.b["check_in_local"], "hours": 24, "guests": 2})).json()
    assert q["available"] is True                                           # the dates are open again
    guest_mail = (await emails(db, "booking_cancelled_guest", "guest@example.com"))[0]
    assert "$275.00" in guest_mail.body_text and "100%" in guest_mail.body_text and "Moderate" in guest_mail.body_text
    host_mail = (await emails(db, "booking_cancelled_host", "host@example.com"))[0]
    assert "cancelled their booking" in host_mail.body_text and "$0.00" in host_mail.body_text
    detail = await get(w.host, f"/host/bookings/{w.id}")
    assert detail["status"] == "cancelled" and detail["payout"]["state"] == "none"


async def test_cancelling_in_the_middle_window_refunds_half_and_the_host_keeps_the_rest(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, 60)
    r = await cancel(w.guest, w.id)
    assert r.status_code == 200 and r.json()["cancelled"]["refund_pct"] == 50
    (refund,) = await row(db, Refund)
    assert refund.amount == 13750
    b = (await row(db, Booking))[0]
    assert b.settlement["host_payable"] == 11000 and b.settlement["refund"]["host_commission"] == 1500
    detail = await get(w.host, f"/host/bookings/{w.id}")
    assert detail["payout"]["state"] == "scheduled" and detail["payout"]["amount"] == 11000


async def test_cancelling_too_late_refunds_nothing_but_still_frees_the_dates(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, 10)
    r = await cancel(w.guest, w.id)
    assert r.status_code == 200 and r.json()["cancelled"]["refund_pct"] == 0
    assert await row(db, Refund) == [] and Spy.calls == []
    assert "No refund is due" in (await emails(db, "booking_cancelled_guest", "guest@example.com"))[0].body_text
    b = (await row(db, Booking))[0]
    assert b.settlement["host_payable"] == 22000                           # the host is paid in full for a late cancellation


async def test_the_guest_is_asked_again_if_the_refund_changed_while_they_were_deciding(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, 60)
    r = await cancel(w.guest, w.id, expected=27500)                         # they saw the 100% amount
    assert r.status_code == 409 and r.json()["error"]["code"] == "REFUND_CHANGED" and "$137.50" in r.json()["error"]["message"]
    assert (await get(w.guest, f"/me/bookings/{w.id}"))["status"] == "confirmed" and Spy.calls == []
    assert (await cancel(w.guest, w.id, expected=13750)).status_code == 200


async def test_only_the_guest_can_cancel_and_only_a_confirmed_stay_once(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    other = await complete_guest(new_client, "other@example.com")
    assert (await cancel(other, w.id)).status_code == 404
    assert (await get(w.guest, f"/me/bookings/{w.id}/cancel-preview"))["refund"]
    assert (await other.get(f"{API}/me/bookings/{w.id}/cancel-preview")).status_code == 404
    assert (await cancel(w.guest, w.id)).status_code == 200
    again = await cancel(w.guest, w.id)
    assert again.status_code == 409 and "already cancelled" in again.json()["error"]["message"]
    assert len(await row(db, Refund)) == 1


async def test_you_cannot_cancel_a_stay_that_has_started_or_was_never_paid(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, -2)
    r = await cancel(w.guest, w.id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "TOO_LATE"

    admin = w.admin
    host2, _ = await active_host(new_client, admin, "second@example.com")
    prop2 = await live_listing(host2, admin, name="Second")
    guest2 = await complete_guest(new_client, "g2@example.com")
    unpaid = (await book(guest2, prop2["units"][0])).json()
    assert (await cancel(guest2, unpaid["id"])).status_code == 409           # still only a request


async def test_two_cancellations_at_the_same_moment_refund_once(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    results = await asyncio.gather(cancel(w.guest, w.id), cancel(w.guest, w.id))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert len(await row(db, Refund)) == 1 and len([c for c in Spy.calls if c[0] == "refund"]) == 1


# ---------- host and admin cancel ----------

async def test_a_host_cancellation_refunds_the_guest_in_full_and_pays_the_host_nothing(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    h = csrf_headers(w.host)
    assert (await w.host.post(f"{API}/host/bookings/{w.id}/cancel", json={"reason": " "}, headers=h)).status_code == 422
    r = await w.host.post(f"{API}/host/bookings/{w.id}/cancel", json={"reason": "Burst pipe, sorry."}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["payout"]["state"] == "none"
    (refund,) = await row(db, Refund)
    assert refund.type == "host_cancel" and refund.amount == 29563           # everything, service fee included
    mine = await get(w.guest, f"/me/bookings/{w.id}")
    assert mine["cancelled"] == {"by": "host", "at": mine["cancelled"]["at"], "reason": "Burst pipe, sorry.", "policy": "Moderate", "refund_pct": 100}
    mail = (await emails(db, "booking_cancelled_guest", "guest@example.com"))[0]
    assert "Burst pipe, sorry." in mail.body_text and "$295.63" in mail.body_text and "the host had to cancel" in mail.body_text
    assert (await w.host.post(f"{API}/host/bookings/{w.id}/cancel", json={"reason": "again"}, headers=h)).status_code == 409


async def test_a_host_cannot_cancel_someone_elses_booking(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    other, _ = await active_host(new_client, w.admin, "rival@example.com")
    assert (await other.post(f"{API}/host/bookings/{w.id}/cancel", json={"reason": "x"}, headers=csrf_headers(other))).status_code == 404


async def test_host_cancellations_are_counted_and_the_admins_are_alerted_at_the_threshold(new_client, make_admin, db, spy):
    admin = await admin_client(new_client, make_admin)
    host, host_id = await active_host(new_client, admin)
    prop = await live_listing(host, admin, quantity=4)
    guest = await complete_guest(new_client)
    ids = []
    for d in (10, 12, 14):
        b = (await book(guest, prop["units"][0], days=d)).json()
        await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
        await dev_complete(guest, b["id"])
        ids.append(b["id"])
    for i, bid in enumerate(ids):
        r = await host.post(f"{API}/host/bookings/{bid}/cancel", json={"reason": f"reason {i}"}, headers=csrf_headers(host))
        assert r.status_code == 200
        alerts = await emails(db, "host_cancellation_alert_admin", "admin@example.com")
        assert len(alerts) == (1 if i >= 2 else 0)                                # the default threshold is 3, alerted once when reached
    stats = (await get(admin, f"/admin/hosts/{host_id}"))["cancellations"]
    assert stats["total"] == 3 and stats["last_30_days"] == 3 and stats["last_90_days"] == 3 and stats["alert"] is True and stats["threshold"] == 3
    assert [i["reason"] for i in stats["items"]] == ["reason 2", "reason 1", "reason 0"]
    assert "3 confirmed bookings" in alerts[0].body_text


async def test_a_guest_cancellation_does_not_count_against_the_host(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await cancel(w.guest, w.id)
    assert (await get(w.admin, f"/admin/hosts/{w.host_id}"))["cancellations"]["total"] == 0


async def test_an_admin_can_cancel_by_policy_or_in_full_with_a_reason(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, 60)
    h = csrf_headers(w.admin)
    assert (await w.admin.post(f"{API}/admin/bookings/{w.id}/cancel", json={"reason": "", "refund": "full"}, headers=h)).status_code == 422
    assert (await w.guest.post(f"{API}/admin/bookings/{w.id}/cancel", json={"reason": "x", "refund": "full"}, headers=csrf_headers(w.guest))).status_code == 403
    r = await w.admin.post(f"{API}/admin/bookings/{w.id}/cancel", json={"reason": "Property condemned", "refund": "full"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    (refund,) = await row(db, Refund)
    assert refund.type == "admin" and refund.amount == 29563
    assert r.json()["refund"]["refunded"] == 29563 and r.json()["payout"]["state"] == "none"


async def test_an_admin_can_cancel_by_policy(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, 60)
    r = await w.admin.post(f"{API}/admin/bookings/{w.id}/cancel", json={"reason": "Guest complaint", "refund": "policy"}, headers=csrf_headers(w.admin))
    assert r.status_code == 200 and (await row(db, Refund))[0].amount == 13750


async def test_an_admin_can_cancel_a_stay_that_is_in_progress(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, -2)
    async with SessionLocal() as s:                                            # the clock-driven job marks it checked in
        from app.modules.bookings import service as booking_service

        assert await booking_service.advance_status(s) == 1
        await s.commit()
    assert (await get(w.admin, "/admin/bookings"))["items"][0]["status"] == "checked_in"
    r = await w.admin.post(f"{API}/admin/bookings/{w.id}/cancel", json={"reason": "Emergency", "refund": "full"}, headers=csrf_headers(w.admin))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


# ---------- refunds that do not go smoothly ----------

async def test_a_failed_refund_does_not_undo_the_cancellation_and_can_be_retried(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    Spy.fail = {"refund"}
    r = await cancel(w.guest, w.id)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["refund"]["state"] == "failed"
    (refund,) = await row(db, Refund)
    assert refund.status == "failed" and "refused" in refund.error
    failed = await get(w.admin, "/admin/refunds?status=failed")
    assert [f["id"] for f in failed] == [str(refund.id)] and failed[0]["reference"] == w.b["reference"] and "refused" in failed[0]["error"]

    Spy.fail = set()
    ok = await w.admin.post(f"{API}/admin/refunds/{refund.id}/retry", headers=csrf_headers(w.admin))
    assert ok.status_code == 200 and ok.json()["status"] == "succeeded"
    retried = [c for c in Spy.calls if c[0] == "refund"]
    assert len(retried) == 2 and retried[0][1]["idempotency_key"] == retried[1][1]["idempotency_key"]    # same key: Stripe cannot refund twice
    again = await w.admin.post(f"{API}/admin/refunds/{refund.id}/retry", headers=csrf_headers(w.admin))
    assert again.status_code == 409
    assert (await w.guest.post(f"{API}/admin/refunds/{refund.id}/retry", headers=csrf_headers(w.guest))).status_code == 403


async def test_a_pending_refund_is_completed_by_stripe_webhook_or_by_the_reconcile_job(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    Spy.refund_status = "pending"
    r = await cancel(w.guest, w.id)
    assert r.json()["refund"]["state"] == "pending"
    (refund,) = await row(db, Refund)
    async with SessionLocal() as s:
        assert await refund_module.apply_refund_event(s, RefundInfo(id=refund.stripe_refund_id, status="succeeded")) == "succeeded"
        assert await refund_module.apply_refund_event(s, RefundInfo(id=refund.stripe_refund_id, status="succeeded")) == "unchanged"
        assert await refund_module.apply_refund_event(s, RefundInfo(id=refund.stripe_refund_id, status="failed")) == "duplicate"   # stale news never undoes it
        assert await refund_module.apply_refund_event(s, RefundInfo(id="re_other_project", status="succeeded")) == "unknown"
        await s.commit()
    assert (await row(db, Refund))[0].status == "succeeded"
    assert (await get(w.guest, f"/me/bookings/{w.id}"))["refund"]["state"] == "refunded"


async def test_the_reconcile_job_finishes_refunds_whose_webhook_never_arrived(new_client, make_admin, db, spy, monkeypatch):
    w = await paid_world(new_client, make_admin)
    Spy.refund_status = "pending"
    await cancel(w.guest, w.id)
    await db.execute(update(Refund).values(stripe_refund_id="re_real_1", created_at=datetime.now(UTC) - timedelta(minutes=5)))
    await db.commit()

    class Done(gw.FakeStripeGateway):
        def retrieve_refund(self, refund_id):
            return RefundInfo(id=refund_id, status="succeeded")

    monkeypatch.setattr(refund_module, "get_gateway", lambda: Done())
    async with SessionLocal() as s:
        assert await refund_module.reconcile_pending_refunds(s) == 1
        await s.commit()
    assert (await row(db, Refund))[0].status == "succeeded"
    async with SessionLocal() as s:
        assert await refund_module.reconcile_pending_refunds(s) == 0


async def test_refund_webhooks_update_refunds_and_are_ignored_for_other_projects(new_client, make_admin, db, spy, monkeypatch):
    import hashlib, hmac, json, time

    from app.core.config import get_settings

    w = await paid_world(new_client, make_admin)
    Spy.refund_status = "pending"
    await cancel(w.guest, w.id)
    (refund,) = await row(db, Refund)
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_refund")

    def post(event):
        body = json.dumps(event).encode()
        ts = str(int(time.time()))
        sig = hmac.new(b"whsec_refund", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        return body, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}

    anon = await new_client()
    for event_id, obj in (("evt_r1", {"id": refund.stripe_refund_id, "status": "succeeded"}), ("evt_r2", {"id": "re_not_ours", "status": "failed"})):
        body, headers = post({"id": event_id, "object": "event", "type": "refund.updated", "data": {"object": obj}})
        assert (await anon.post(f"{API}/webhooks/stripe", content=body, headers=headers)).status_code == 200
    assert (await row(db, Refund))[0].status == "succeeded"


# ---------- exceptional refunds (host asks, admin decides) ----------

async def test_a_host_can_ask_for_a_refund_and_an_admin_approves_it_out_of_the_hosts_earnings(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    h = csrf_headers(w.host)
    assert (await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 5000, "reason": " "}, headers=h)).status_code == 422
    assert (await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 99999, "reason": "too much"}, headers=h)).status_code == 422
    r = await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 5000, "reason": "The heating was broken"}, headers=h)
    assert r.status_code == 201 and r.json()["status"] == "pending"
    dup = await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 1000, "reason": "again"}, headers=h)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "ALREADY_REQUESTED"
    assert len(await emails(db, "refund_requested_admin", "admin@example.com")) == 1

    queue = await get(w.admin, "/admin/refund-requests?status=pending")
    assert len(queue) == 1 and queue[0]["amount"] == 5000 and queue[0]["reference"] == w.b["reference"] and queue[0]["paid"] == 29563
    assert (await get(w.host, f"/host/bookings/{w.id}/refund-requests"))[0]["status"] == "pending"

    done = await w.admin.post(f"{API}/admin/refund-requests/{queue[0]['id']}/decision", json={"decision": "approve", "note": "Fair", "amount": 4000}, headers=csrf_headers(w.admin))
    assert done.status_code == 200 and done.json()["status"] == "approved"
    (refund,) = await row(db, Refund)
    assert refund.type == "exceptional" and refund.amount == 4000 and refund.status == "succeeded"          # the admin granted less than asked
    assert "$40.00" in (await emails(db, "refund_issued_guest", "guest@example.com"))[0].body_text
    assert "approved" in (await emails(db, "refund_request_decided_host", "host@example.com"))[0].body_text
    assert (await w.admin.post(f"{API}/admin/refund-requests/{queue[0]['id']}/decision", json={"decision": "reject", "note": "x"}, headers=csrf_headers(w.admin))).status_code == 409


async def test_a_rejected_request_needs_a_reason_and_refunds_nothing(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    req = (await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 5000, "reason": "complaint"}, headers=csrf_headers(w.host))).json()
    d = f"{API}/admin/refund-requests/{req['id']}/decision"
    assert (await w.admin.post(d, json={"decision": "reject"}, headers=csrf_headers(w.admin))).status_code == 422
    ok = await w.admin.post(d, json={"decision": "reject", "note": "Not enough evidence"}, headers=csrf_headers(w.admin))
    assert ok.status_code == 200 and ok.json()["status"] == "rejected"
    assert await row(db, Refund) == [] and Spy.calls == []
    assert "Not enough evidence" in (await emails(db, "refund_request_decided_host", "host@example.com"))[0].body_text
    assert (await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 100, "reason": "new request"}, headers=csrf_headers(w.host))).status_code == 201


async def test_refund_requests_are_limited_to_the_hosts_own_paid_bookings(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    other, _ = await active_host(new_client, w.admin, "rival@example.com")
    body = {"amount": 100, "reason": "x"}
    assert (await other.post(f"{API}/host/bookings/{w.id}/refund-request", json=body, headers=csrf_headers(other))).status_code == 404
    assert (await w.guest.post(f"{API}/host/bookings/{w.id}/refund-request", json=body, headers=csrf_headers(w.guest))).status_code == 404
    await cancel(w.guest, w.id)
    r = await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json=body, headers=csrf_headers(w.host))
    assert r.status_code == 409                                                   # cancelled bookings are already settled
    assert (await w.guest.get(f"{API}/admin/refund-requests")).status_code == 403


async def test_the_remaining_refundable_amount_shrinks_with_each_refund(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    h = csrf_headers(w.host)
    first = (await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 20000, "reason": "one"}, headers=h)).json()
    await w.admin.post(f"{API}/admin/refund-requests/{first['id']}/decision", json={"decision": "approve"}, headers=csrf_headers(w.admin))
    over = await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 10000, "reason": "two"}, headers=h)
    assert over.status_code == 422 and "$95.63" in over.json()["error"]["message"]
    assert (await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 9563, "reason": "two"}, headers=h)).status_code == 201


# ---------- cancellation policy templates ----------

async def test_the_default_policies_exist_and_everyone_can_list_the_active_ones(client, db):
    listing = (await client.get(f"{API}/meta/cancellation")).json()
    assert [t["key"] for t in listing] == ["flexible", "moderate", "strict"]
    assert listing[1]["tiers"] == MODERATE


async def test_admins_manage_policies_and_a_change_applies_to_new_bookings_only(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    a = w.admin
    h = csrf_headers(a)
    templates = {t["key"]: t for t in await get(a, "/admin/cancellation-templates")}
    mod = templates["moderate"]
    r = await a.patch(f"{API}/admin/cancellation-templates/{mod['id']}", json={"tiers": [{"hours_before": 48, "refund_pct": 100}]}, headers=h)
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["tiers"] == [{"hours_before": 48, "refund_pct": 100}]
    same = await a.patch(f"{API}/admin/cancellation-templates/{mod['id']}", json={"name": "Moderate plus"}, headers=h)
    assert same.json()["version"] == 2                                             # only a change to the steps is a new version

    p = await get(w.guest, f"/me/bookings/{w.id}/cancel-preview")                   # the booking keeps the policy it was made under
    assert p["policy"]["tiers"] == MODERATE and p["refund"]["refund_pct"] == 100

    bad = await a.patch(f"{API}/admin/cancellation-templates/{mod['id']}", json={"tiers": [{"hours_before": 24, "refund_pct": 10}, {"hours_before": 48, "refund_pct": 5}]}, headers=h)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_TIERS"
    created = await a.post(f"{API}/admin/cancellation-templates", json={"key": "Super_Flex", "name": "Super flexible", "description": "Anytime", "tiers": [{"hours_before": 0, "refund_pct": 100}], "strictness": 1}, headers=h)
    assert created.status_code == 201 and created.json()["key"] == "super_flex"
    assert (await a.post(f"{API}/admin/cancellation-templates", json={"key": "super_flex", "name": "x", "tiers": [], "strictness": 1}, headers=h)).status_code == 409
    assert (await w.guest.get(f"{API}/admin/cancellation-templates")).status_code == 403


async def test_the_default_policy_cannot_be_switched_off_and_an_inactive_one_falls_back_to_the_default(new_client, make_admin, db, spy):
    admin = await admin_client(new_client, make_admin)
    h = csrf_headers(admin)
    templates = {t["key"]: t for t in await get(admin, "/admin/cancellation-templates")}
    refuse = await admin.patch(f"{API}/admin/cancellation-templates/{templates['moderate']['id']}", json={"active": False}, headers=h)
    assert refuse.status_code == 409 and refuse.json()["error"]["code"] == "IS_DEFAULT"
    host, _ = await active_host(new_client, admin)
    prop = await live_listing(host, admin)
    r = await host.patch(f"{API}/host/properties/{prop['id']}", json={"cancellation_template_key": "flexible"}, headers=csrf_headers(host))
    assert r.status_code == 200, r.text
    await admin.patch(f"{API}/admin/cancellation-templates/{templates['flexible']['id']}", json={"active": False}, headers=h)
    guest = await complete_guest(new_client)
    b = (await book(guest, prop["units"][0])).json()
    row_ = (await row(db, Booking))[0]
    assert row_.policy_snapshot["cancellation"]["key"] == "moderate"               # the host's choice was switched off, so the default applies
    assert [t["key"] for t in (await guest.get(f"{API}/meta/cancellation")).json()] == ["moderate", "strict"]
    assert b["status"] == "requested"


async def test_hosts_choose_a_policy_within_the_admin_limit_and_guests_see_it(new_client, make_admin, db):
    admin = await admin_client(new_client, make_admin)
    host, _ = await active_host(new_client, admin)
    prop = await live_listing(host, admin)
    hh = csrf_headers(host)
    assert (await host.patch(f"{API}/host/properties/{prop['id']}", json={"cancellation_template_key": "nonsense"}, headers=hh)).status_code == 422
    await admin.put(f"{API}/admin/settings/cancellation.guardrail_template", json={"value": "moderate", "reason": "test"}, headers=csrf_headers(admin))
    tooharsh = await host.patch(f"{API}/host/properties/{prop['id']}", json={"cancellation_template_key": "strict"}, headers=hh)
    assert tooharsh.status_code == 422 and tooharsh.json()["error"]["code"] == "CANCELLATION_TOO_STRICT"
    assert (await host.patch(f"{API}/host/properties/{prop['id']}", json={"cancellation_template_key": "flexible"}, headers=hh)).status_code == 200
    public = (await (await new_client()).get(f"{API}/properties/{prop['id']}")).json()
    assert public["cancellation"]["key"] == "flexible" and public["cancellation"]["tiers"] == [{"hours_before": 24, "refund_pct": 100}]


async def test_a_host_chosen_policy_is_the_one_stored_on_new_bookings(new_client, make_admin, db):
    admin = await admin_client(new_client, make_admin)
    host, _ = await active_host(new_client, admin)
    prop = await live_listing(host, admin)
    await host.patch(f"{API}/host/properties/{prop['id']}", json={"cancellation_template_key": "flexible"}, headers=csrf_headers(host))
    guest = await complete_guest(new_client)
    await book(guest, prop["units"][0])
    snap = (await row(db, Booking))[0].policy_snapshot["cancellation"]
    assert snap["key"] == "flexible" and snap["tiers"] == [{"hours_before": 24, "refund_pct": 100}] and snap["version"] == 1
