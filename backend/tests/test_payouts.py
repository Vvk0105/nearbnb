import asyncio
import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.modules.bookings import service as booking_service
from app.modules.bookings.models import Booking, BookingEvent
from app.modules.hosts.models import HostProfile
from app.modules.payments import stripe_gateway as gw
from app.modules.payments.models import Payment, Refund
from app.modules.payments.stripe_gateway import RealStripeGateway, refund_from_stripe
from app.modules.payouts import service as payouts
from app.modules.payouts.models import PayoutRecord, Transfer
from tests.conftest import csrf_headers
from tests.helpers import API
from tests.test_bookings import emails
from tests.test_cancellations import Spy, cancel, get, paid_world, shift, spy  # noqa: F401  (spy is a fixture)
from tests.test_payments import row


async def release(now=None):
    async with SessionLocal() as s:
        counts = await payouts.release_due(s, now)
        await s.commit()
    return counts


def transfers():
    return [c for c in Spy.calls if c[0] == "transfer"]


async def due_world(new_client, make_admin, db, **kw):
    """A paid booking whose payout is already due (check-in was 30 hours ago, delay is 24 hours)."""
    w = await paid_world(new_client, make_admin, **kw)
    await shift(db, w.id, -30)
    return w


# ---------- when a payout is released ----------

async def test_the_host_is_paid_their_share_once_after_check_in_plus_the_delay(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    assert await release() == {"paid": 0, "failed": 0, "skipped": 0}            # stay is ten days away
    await shift(db, w.id, -10)
    assert await release() == {"paid": 0, "failed": 0, "skipped": 0}            # started, but the 24 hour delay has not passed
    detail = await get(w.host, f"/host/bookings/{w.id}")
    assert detail["payout"]["state"] == "scheduled" and detail["payout"]["amount"] == 22000
    await shift(db, w.id, -30)
    assert await release() == {"paid": 1, "failed": 0, "skipped": 0}

    (t,) = await row(db, Transfer)
    assert t.amount == 22000 and t.status == "transferred" and t.stripe_transfer_id.startswith("tr_spy_") and t.transferred_at
    (_, call), = transfers()
    host_account = (await row(db, HostProfile))[0].stripe_account_id        # read straight away: row() expires earlier objects
    charge = (await row(db, Payment))[0].stripe_charge_id
    assert call["destination"] == host_account and call["source_transaction"] == charge and call["transfer_group"] == w.b["reference"]
    assert call["amount"] == 22000 and call["currency"] == "AUD" and call["idempotency_key"] == f"transfer-{w.id}"
    mail = (await emails(db, "payout_sent_host", "host@example.com"))[0]
    assert "$220.00" in mail.body_text and w.b["reference"] in mail.body_text
    assert (await get(w.host, f"/host/bookings/{w.id}"))["payout"]["state"] == "paid"

    assert await release() == {"paid": 0, "failed": 0, "skipped": 0} and len(transfers()) == 1      # never twice


async def test_the_payout_clock_follows_the_anchor_and_delay_stored_on_the_booking(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, -30)                                                   # checked in 30 hours ago, checks out in -6 hours
    b = (await row(db, Booking))[0]
    await db.execute(update(Booking).where(Booking.id == b.id).values(policy_snapshot={**b.policy_snapshot, "payout_anchor": "check_out", "payout_delay_hours": 48}))
    await db.commit()
    assert (await release())["paid"] == 0                                        # check-out was 6 hours ago; 48 hours must pass
    assert (await release(datetime.now(UTC) + timedelta(hours=41)))["paid"] == 0
    assert (await release(datetime.now(UTC) + timedelta(hours=43)))["paid"] == 1


async def test_a_payout_waits_while_an_admin_holds_it(new_client, make_admin, db, spy):
    w = await due_world(new_client, make_admin, db)
    h = csrf_headers(w.admin)
    r = await w.admin.post(f"{API}/admin/bookings/{w.id}/payout-hold", json={"hold": True, "note": "Guest complaint under review"}, headers=h)
    assert r.status_code == 200 and r.json()["payout"]["state"] == "held" and r.json()["payout"]["hold_note"] == "Guest complaint under review"
    assert (await release())["paid"] == 0
    assert (await get(w.host, f"/host/bookings/{w.id}"))["payout"]["state"] == "held"
    await w.admin.post(f"{API}/admin/bookings/{w.id}/payout-hold", json={"hold": False}, headers=h)
    assert (await release())["paid"] == 1
    assert (await w.guest.post(f"{API}/admin/bookings/{w.id}/payout-hold", json={"hold": True}, headers=csrf_headers(w.guest))).status_code == 403


async def test_a_suspended_or_not_yet_payable_host_is_not_paid_until_that_is_fixed(new_client, make_admin, db, spy):
    w = await due_world(new_client, make_admin, db)
    await db.execute(update(HostProfile).values(suspended_at=datetime.now(UTC)))
    await db.commit()
    assert (await release())["paid"] == 0
    await db.execute(update(HostProfile).values(suspended_at=None, stripe_payouts_enabled=False))
    await db.commit()
    assert (await release())["paid"] == 0                                        # Stripe says the host cannot receive payouts
    await db.execute(update(HostProfile).values(stripe_payouts_enabled=True))
    await db.commit()
    assert (await release())["paid"] == 1


async def test_an_open_refund_or_refund_request_blocks_the_payout(new_client, make_admin, db, spy):
    w = await due_world(new_client, make_admin, db)
    req = (await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 3000, "reason": "heating"}, headers=csrf_headers(w.host))).json()
    assert (await release())["paid"] == 0                                        # waiting for an admin's decision
    Spy.refund_status = "pending"
    await w.admin.post(f"{API}/admin/refund-requests/{req['id']}/decision", json={"decision": "approve"}, headers=csrf_headers(w.admin))
    assert (await release())["paid"] == 0                                        # the refund has not completed at Stripe yet
    await db.execute(update(Refund).values(status="succeeded"))
    await db.commit()
    assert (await release())["paid"] == 1
    assert transfers()[0][1]["amount"] == 22000 - 3000                           # an exceptional refund comes out of the host's share


async def test_a_failed_refund_also_holds_the_payout_until_it_is_sorted_out(new_client, make_admin, db, spy):
    w = await due_world(new_client, make_admin, db)
    req = (await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 3000, "reason": "heating"}, headers=csrf_headers(w.host))).json()
    Spy.fail = {"refund"}
    await w.admin.post(f"{API}/admin/refund-requests/{req['id']}/decision", json={"decision": "approve"}, headers=csrf_headers(w.admin))
    assert (await release())["paid"] == 0


async def test_a_booking_refunded_down_to_nothing_pays_the_host_nothing(new_client, make_admin, db, spy):
    w = await due_world(new_client, make_admin, db)
    req = (await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": 29563, "reason": "uninhabitable"}, headers=csrf_headers(w.host))).json()
    await w.admin.post(f"{API}/admin/refund-requests/{req['id']}/decision", json={"decision": "approve"}, headers=csrf_headers(w.admin))
    assert await release() == {"paid": 0, "failed": 0, "skipped": 1} and transfers() == []
    (t,) = await row(db, Transfer)
    assert t.status == "skipped" and t.amount == 0
    assert await release() == {"paid": 0, "failed": 0, "skipped": 0}


async def test_a_late_cancellation_still_pays_the_host_what_the_guest_forfeited(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, 60)
    assert (await cancel(w.guest, w.id)).status_code == 200                      # 50% refund; the host keeps the other half minus commission
    assert (await release())["paid"] == 0                                        # the stay was due in 60 hours
    await shift(db, w.id, -30)                                                   # the original check-in has long passed
    assert (await release())["paid"] == 1
    assert transfers()[0][1]["amount"] == 11000


async def test_a_cancellation_with_a_full_refund_never_creates_a_payout(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await cancel(w.guest, w.id)
    await shift(db, w.id, -100)
    assert await release() == {"paid": 0, "failed": 0, "skipped": 0} and await row(db, Transfer) == []


# ---------- failures and retries ----------

async def test_a_failed_transfer_is_retried_with_a_growing_delay_and_admins_are_told_when_it_keeps_failing(new_client, make_admin, db, spy):
    w = await due_world(new_client, make_admin, db)
    Spy.fail = {"transfer"}
    now = datetime.now(UTC)
    assert (await release(now))["failed"] == 1
    (t,) = await row(db, Transfer)
    assert t.status == "failed" and t.attempts == 1 and "refused" in t.error and t.next_attempt_at > now
    assert (await get(w.host, f"/host/bookings/{w.id}"))["payout"]["state"] == "failed"
    assert await release(now + timedelta(minutes=1)) == {"paid": 0, "failed": 0, "skipped": 0}       # not yet time to retry

    for attempt in range(2, 6):
        now += timedelta(hours=7)
        assert (await release(now))["failed"] == 1
    assert (await row(db, Transfer))[0].attempts == 5
    alert = (await emails(db, "payout_failed_admin", "admin@example.com"))[0]
    assert w.b["reference"] in alert.body_text and "refused" in alert.body_text
    now += timedelta(hours=7)
    assert await release(now) == {"paid": 0, "failed": 0, "skipped": 0}                              # gives up until an admin steps in

    Spy.fail = set()
    r = await w.admin.post(f"{API}/admin/bookings/{w.id}/payout-retry", headers=csrf_headers(w.admin))
    assert r.status_code == 200
    assert (await release(now + timedelta(minutes=1)))["paid"] == 1
    assert (await row(db, Transfer))[0].status == "transferred"
    assert len({c[1]["idempotency_key"] for c in Spy.calls if c[0] == "transfer"}) == 1               # the same Stripe key every time: never a double payout


async def test_two_payout_runs_at_the_same_moment_pay_once(new_client, make_admin, db, spy):
    await due_world(new_client, make_admin, db)
    results = await asyncio.gather(release(), release())
    assert sum(r["paid"] for r in results) == 1 and len(transfers()) == 1


# ---------- taking money back after a payout ----------

async def paid_out(new_client, make_admin, db):
    w = await due_world(new_client, make_admin, db)
    assert (await release())["paid"] == 1
    return w


async def test_an_admin_cancellation_after_payout_takes_the_hosts_share_back_before_refunding(new_client, make_admin, db, spy):
    w = await paid_out(new_client, make_admin, db)
    r = await w.admin.post(f"{API}/admin/bookings/{w.id}/cancel", json={"reason": "Fraud", "refund": "full"}, headers=csrf_headers(w.admin))
    assert r.status_code == 200 and r.json()["refund"]["state"] == "refunded"
    steps = [c[0] for c in Spy.calls if c[0] in ("reverse", "refund")]
    assert steps == ["reverse", "refund"]                                         # the reversal comes first
    reverse = [c[1] for c in Spy.calls if c[0] == "reverse"][0]
    assert reverse["amount"] == 22000 and reverse["transfer_id"].startswith("tr_spy_")
    (t,) = await row(db, Transfer)
    assert t.status == "reversed" and t.reversed_amount == 22000 and t.reversed_at
    (refund,) = await row(db, Refund)
    assert refund.host_reversal_amount == 22000 and refund.stripe_reversal_id.startswith("trr_spy_")
    assert (await get(w.admin, "/admin/bookings"))["items"][0]["payout"]["state"] == "reversed"


async def test_an_exceptional_refund_after_payout_reverses_exactly_that_amount_and_never_more_than_was_paid(new_client, make_admin, db, spy):
    w = await paid_out(new_client, make_admin, db)
    h = csrf_headers(w.host)

    async def exceptional(amount):
        req = (await w.host.post(f"{API}/host/bookings/{w.id}/refund-request", json={"amount": amount, "reason": "goodwill"}, headers=h)).json()
        r = await w.admin.post(f"{API}/admin/refund-requests/{req['id']}/decision", json={"decision": "approve"}, headers=csrf_headers(w.admin))
        assert r.status_code == 200, r.text

    await exceptional(5000)
    (t,) = await row(db, Transfer)
    assert t.reversed_amount == 5000 and t.status == "transferred"
    await exceptional(20000)                                                      # more than the 17000 the host has left
    (t,) = await row(db, Transfer)
    assert t.reversed_amount == 22000 and t.status == "reversed"
    assert [c[1]["amount"] for c in Spy.calls if c[0] == "reverse"] == [5000, 17000]
    assert [c[1]["amount"] for c in Spy.calls if c[0] == "refund"] == [5000, 20000]       # the guest still gets all of what was approved


async def test_a_failed_reversal_stops_the_refund_and_a_retry_does_both_without_repeating_work(new_client, make_admin, db, spy):
    w = await paid_out(new_client, make_admin, db)
    Spy.fail = {"reverse"}
    r = await w.admin.post(f"{API}/admin/bookings/{w.id}/cancel", json={"reason": "Fraud", "refund": "full"}, headers=csrf_headers(w.admin))
    assert r.status_code == 200 and r.json()["refund"]["state"] == "failed"
    assert [c[0] for c in Spy.calls if c[0] == "refund"] == []                    # the guest is not refunded until the host's money is back
    (refund,) = await row(db, Refund)
    assert refund.status == "failed" and "cannot reverse" in refund.error

    Spy.fail = set()
    ok = await w.admin.post(f"{API}/admin/refunds/{refund.id}/retry", headers=csrf_headers(w.admin))
    assert ok.json()["status"] == "succeeded"
    assert [c[0] for c in Spy.calls if c[0] in ("reverse", "refund")] == ["reverse", "reverse", "refund"]   # the first reverse failed, the second worked
    again = await w.admin.post(f"{API}/admin/refunds/{refund.id}/retry", headers=csrf_headers(w.admin))
    assert again.status_code == 409


# ---------- by the clock ----------

async def test_bookings_check_in_and_complete_by_the_clock(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    async def advance():
        async with SessionLocal() as s:
            moved = await booking_service.advance_status(s)
            await s.commit()
            return moved

    assert await advance() == 0
    await shift(db, w.id, -2)
    assert await advance() == 1 and (await get(w.guest, f"/me/bookings/{w.id}"))["status"] == "checked_in"
    assert await advance() == 1 and (await get(w.guest, f"/me/bookings/{w.id}"))["status"] == "checked_in"      # still inside the stay: nothing new happens
    await shift(db, w.id, -30)
    assert await advance() == 1 and (await get(w.guest, f"/me/bookings/{w.id}"))["status"] == "completed"
    assert await advance() == 0
    events = [(e.from_status, e.to_status) for e in (await db.execute(select(BookingEvent).where(BookingEvent.booking_id == uuid.UUID(w.id)).order_by(BookingEvent.at))).scalars()]
    assert events[-2:] == [("confirmed", "checked_in"), ("checked_in", "completed")]


async def test_a_stay_that_is_already_over_goes_straight_to_completed(new_client, make_admin, db, spy):
    w = await paid_world(new_client, make_admin)
    await shift(db, w.id, -100)
    async with SessionLocal() as s:
        assert await booking_service.advance_status(s) == 1
        await s.commit()
    assert (await get(w.guest, f"/me/bookings/{w.id}"))["status"] == "completed"
    assert (await release())["paid"] == 1                                         # completed bookings are paid too


# ---------- Stripe's own bank payouts ----------

def signed(secret, payload):
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


async def test_payout_webhooks_are_recorded_against_the_host(new_client, make_admin, db, spy, monkeypatch):
    w = await paid_world(new_client, make_admin)
    host = (await row(db, HostProfile))[0]
    host_account, host_pk = host.stripe_account_id, host.id
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", "whsec_payouts")
    anon = await new_client()

    async def send(event_id, kind, account, **payout):
        obj = {"id": "po_123", "object": "payout", "amount": 22000, "currency": "aud", "status": "pending", "arrival_date": 1893456000, **payout}
        body, headers = signed("whsec_payouts", {"id": event_id, "object": "event", "type": f"payout.{kind}", "account": account, "data": {"object": obj}})
        return await anon.post(f"{API}/webhooks/stripe", content=body, headers=headers)

    assert (await send("evt_p1", "created", host_account)).status_code == 200
    assert (await send("evt_p2", "paid", host_account, status="paid")).status_code == 200
    assert (await send("evt_p3", "created", "acct_someone_elses", id="po_999")).status_code == 200
    assert (await send("evt_p2", "paid", host_account, status="paid")).json().get("duplicate") is True
    records = await row(db, PayoutRecord)
    assert len(records) == 1 and records[0].status == "paid" and records[0].amount == 22000 and records[0].currency == "AUD" and records[0].host_id == host_pk
    assert records[0].arrival_date.year == 2030
    assert (await get(w.host, "/host/payout-records"))[0]["status"] == "paid"
    assert (await get(w.admin, f"/admin/hosts/{w.host_id}"))["bank_payouts"][0]["status"] == "paid"
    failed = await send("evt_p4", "failed", host_account, status="failed", failure_code="account_closed")
    assert failed.status_code == 200 and (await get(w.host, "/host/payout-records"))[0]["failure_code"] == "account_closed"


# ---------- views ----------

async def test_money_facts_are_shown_to_the_right_people_only(new_client, make_admin, db, spy):
    w = await due_world(new_client, make_admin, db)
    await release()
    guest_view = await get(w.guest, f"/me/bookings/{w.id}")
    assert "payout" not in guest_view and "commission" not in json.dumps(guest_view)
    host_view = await get(w.host, f"/host/bookings/{w.id}")
    assert host_view["payout"]["state"] == "paid" and host_view["payout"]["amount"] == 22000 and host_view["price"]["commission"] == 3000
    admin_view = (await get(w.admin, "/admin/bookings"))["items"][0]
    assert admin_view["payout"]["state"] == "paid" and admin_view["payment"]["status"] == "succeeded"


# ---------- the Stripe wiring (stand-in client, no network) ----------

class Recorder:
    def __init__(self):
        self.calls = []
        outer = self

        class Transfers:
            def create(self, params, options=None):
                outer.calls.append(("transfers.create", params, options))
                return SimpleNamespace(id="tr_1")

            class reversals:
                @staticmethod
                def create(transfer_id, params, options=None):
                    outer.calls.append(("reversals.create", transfer_id, params, options))
                    return SimpleNamespace(id="trr_1")

        class Refunds:
            def retrieve(self, refund_id):
                return SimpleNamespace(to_dict=lambda: {"id": refund_id, "status": "failed", "failure_reason": "expired_or_canceled_card"})

        self.v1 = SimpleNamespace(transfers=Transfers(), refunds=Refunds())


def real(rec) -> RealStripeGateway:
    g = RealStripeGateway.__new__(RealStripeGateway)
    g._client = rec
    return g


def test_a_transfer_is_tied_to_the_original_charge_and_carries_an_idempotency_key():
    rec = Recorder()
    assert real(rec).create_transfer(amount=22000, currency="AUD", destination="acct_1", source_transaction="ch_1", transfer_group="NB-X", idempotency_key="transfer-b1") == "tr_1"
    (_, params, options), = rec.calls
    assert params == {"amount": 22000, "currency": "aud", "destination": "acct_1", "transfer_group": "NB-X", "source_transaction": "ch_1"}
    assert options == {"idempotency_key": "transfer-b1"}
    rec.calls.clear()
    real(rec).create_transfer(amount=1, currency="AUD", destination="acct_1", source_transaction=None, transfer_group="NB-X", idempotency_key="k")
    assert "source_transaction" not in rec.calls[0][1]


def test_a_transfer_reversal_names_the_transfer_and_amount():
    rec = Recorder()
    assert real(rec).reverse_transfer(transfer_id="tr_1", amount=500, idempotency_key="reverse-r1") == "trr_1"
    assert rec.calls == [("reversals.create", "tr_1", {"amount": 500}, {"idempotency_key": "reverse-r1"})]


def test_refund_statuses_are_read_from_stripe():
    assert real(Recorder()).retrieve_refund("re_1").status == "failed"
    assert refund_from_stripe({"id": "re_2", "status": "pending"}).status == "pending"
    assert refund_from_stripe({"id": "re_3"}).status == "succeeded"
    assert refund_from_stripe({"id": "re_4", "status": "failed", "failure_reason": "lost_or_stolen_card"}).failure_reason == "lost_or_stolen_card"
    assert gw.FakeStripeGateway().create_transfer(amount=1, currency="AUD", destination="a", source_transaction=None, transfer_group="g", idempotency_key="k").startswith("tr_fake_")
