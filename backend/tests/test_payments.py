import asyncio
import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.modules.bookings import service as booking_service
from app.modules.bookings.models import B_CONFIRMED, B_REQUESTED, Booking, BookingEvent
from app.modules.notifications.models import EmailOutbox
from app.modules.payments import service as payments
from app.modules.payments import stripe_gateway as gw
from app.modules.payments.models import Invoice, Payment, Refund
from app.modules.payments.stripe_gateway import IntentInfo, RealStripeGateway, intent_from_stripe
from tests.conftest import csrf_headers
from tests.helpers import API
from tests.test_bookings import book, complete_guest, emails, world

SECRET = "whsec_pay_test"


async def accepted(new_client, make_admin, **listing):
    """A world with one booking request that the host has accepted. Returns (host, guest, unit, booking)."""
    _, host, _, _, unit = await world(new_client, make_admin, **listing)
    guest = await complete_guest(new_client)
    b = (await book(guest, unit)).json()
    r = await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
    assert r.status_code == 200, r.text
    return host, guest, unit, b


async def pay(guest, booking_id: str):
    return await guest.post(f"{API}/me/bookings/{booking_id}/pay", headers=csrf_headers(guest))


async def dev_complete(guest, booking_id: str):
    return await guest.post(f"{API}/me/bookings/{booking_id}/pay/dev-complete", headers=csrf_headers(guest))


def intent_event(event_id: str, kind: str, intent_id: str, *, amount: int, status: str, error: dict | None = None, charge: str | None = "ch_test_1") -> dict:
    obj = {"id": intent_id, "object": "payment_intent", "status": status, "amount": amount, "currency": "aud", "latest_charge": charge, "metadata": {}}
    if error:
        obj["last_payment_error"] = error
    return {"id": event_id, "object": "event", "type": f"payment_intent.{kind}", "data": {"object": obj}}


def signed(payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


@pytest.fixture
def webhook_secret():
    settings = get_settings()
    settings.stripe_webhook_secret = SECRET
    yield
    settings.stripe_webhook_secret = ""


async def send(client, payload: dict):
    body, headers = signed(payload)
    return await client.post(f"{API}/webhooks/stripe", content=body, headers=headers)


async def row(db, model, **where):
    q = select(model)
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    db.expire_all()
    return (await db.execute(q)).scalars().all()


# ---------- starting a payment ----------

async def test_paying_starts_one_payment_for_exactly_the_stored_total(new_client, make_admin, db):
    _, guest, _, b = await accepted(new_client, make_admin)
    r = await pay(guest, b["id"])
    assert r.status_code == 200, r.text
    started = r.json()
    assert started["amount"] == 29563 and started["currency"] == "AUD" and started["fake"] is True and started["client_secret"]
    assert 0 < started["seconds_to_pay"] <= 30 * 60

    (payment,) = await row(db, Payment)
    assert payment.amount == 29563 and payment.status == "created" and payment.attempt == 1
    mine = (await guest.get(f"{API}/me/bookings/{b['id']}")).json()
    assert mine["status"] == "payment_pending" and mine["can_pay"] is True and mine["can_withdraw"] is False
    assert mine["payment"] == {"status": "created", "failure_message": None}

    again = (await pay(guest, b["id"])).json()
    assert again["client_secret"] == started["client_secret"]               # same payment is reused, never charged twice
    assert len(await row(db, Payment)) == 1


async def test_only_the_guest_can_pay_and_only_once_accepted(new_client, make_admin):
    _, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    other = await complete_guest(new_client, "other@example.com")
    b = (await book(guest, unit)).json()
    early = await pay(guest, b["id"])
    assert early.status_code == 409 and early.json()["error"]["code"] == "INVALID_STATE"
    await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
    assert (await pay(other, b["id"])).status_code == 404
    assert (await pay(host, b["id"])).status_code == 404
    assert (await pay(await new_client(), b["id"])).status_code in (401, 403)
    assert (await pay(guest, b["id"])).status_code == 200


async def test_the_pay_option_closes_with_the_payment_deadline(new_client, make_admin, db):
    _, guest, _, b = await accepted(new_client, make_admin)
    await db.execute(update(Booking).where(Booking.id == uuid.UUID(b["id"])).values(payment_deadline_at=datetime.now(UTC) - timedelta(seconds=5)))
    await db.commit()
    r = await pay(guest, b["id"])
    assert r.status_code == 409
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["can_pay"] is False


# ---------- success ----------

async def test_a_successful_payment_confirms_the_booking_converts_the_hold_and_issues_an_invoice(new_client, make_admin, db):
    host, guest, unit, b = await accepted(new_client, make_admin)
    r = await dev_complete(guest, b["id"])
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "confirmed" and r.json()["booking"]["status"] == "confirmed"

    mine = (await guest.get(f"{API}/me/bookings/{b['id']}")).json()
    assert mine["status"] == "confirmed" and mine["can_pay"] is False and mine["seconds_to_pay"] is None
    assert mine["invoice_number"] == "INV-000001" and mine["confirmed_at"]
    assert mine["host"]["email"] == "host@example.com" and mine["host"]["phone"] == "+61 400 111 222"
    assert "price" in mine and "commission" not in mine["price"]

    (kind, expires), = await _occupancy(db, b["id"])
    assert kind == "booking" and expires is None                          # same row: the room was never free in between
    anon = await new_client()
    q = (await anon.post(f"{API}/units/{unit['id']}/quote", json={"checkin_local": b["check_in_local"], "hours": 24, "guests": 2})).json()
    assert q["available"] is False and q["message"].startswith("Someone is staying")

    detail = (await host.get(f"{API}/host/bookings/{b['id']}")).json()
    assert detail["status"] == "confirmed" and detail["guest"]["email"] == "guest@example.com" and detail["guest"]["phone"] == "+61 400 222 333"

    guest_mail = (await emails(db, "payment_confirmed_guest", "guest@example.com"))[0]
    assert "host@example.com" in guest_mail.body_text and "+61 400 111 222" in guest_mail.body_text and f"/account/trips/{b['id']}/invoice" in guest_mail.body_text
    host_mail = (await emails(db, "booking_confirmed_host", "host@example.com"))[0]
    assert "guest@example.com" in host_mail.body_text and "+61 400 222 333" in host_mail.body_text and "$220.00" in host_mail.body_text

    events = [(e.from_status, e.to_status) for e in await row(db, BookingEvent, booking_id=uuid.UUID(b["id"]))]
    assert events == [("requested", "accepted"), ("accepted", "payment_pending"), ("payment_pending", "confirmed")]


async def _occupancy(db, booking_id: str):
    db.expire_all()
    return list(await db.execute(text("select kind, expires_at from room_occupancy where booking_id = :b"), {"b": booking_id}))


async def test_the_invoice_is_a_frozen_copy_the_guest_can_read(new_client, make_admin, db):
    admin, host, _, _, unit = await world(new_client, make_admin)
    guest = await complete_guest(new_client)
    b = (await book(guest, unit)).json()
    await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
    await admin.put(f"{API}/admin/settings/invoice.seller_name", json={"value": "NearBnB Pty Ltd", "reason": "test"}, headers=csrf_headers(admin))
    await admin.put(f"{API}/admin/settings/invoice.seller_tax_id", json={"value": "ABN 12 345 678 901", "reason": "test"}, headers=csrf_headers(admin))
    await dev_complete(guest, b["id"])

    inv = (await guest.get(f"{API}/me/bookings/{b['id']}/invoice")).json()
    assert inv["number"] == "INV-000001" and inv["total"] == 29563 and inv["currency"] == "AUD"
    assert inv["seller"]["name"] == "NearBnB Pty Ltd" and inv["seller"]["tax_id"] == "ABN 12 345 678 901"
    assert inv["buyer"] == {"name": "Test Guest", "email": "guest@example.com"}
    assert inv["stay"]["reference"] == b["reference"] and inv["stay"]["nights"] == 1 and "1 Beach Rd" in inv["stay"]["address"]
    assert [l["amount"] for l in inv["lines"]] == [25000, 1875]
    assert inv["tax"] == {"name": "GST", "rate_percent": 10.0, "amount": 2688, "inclusive": False}
    assert sum(l["amount"] for l in inv["lines"]) + inv["tax"]["amount"] == inv["total"]
    assert inv["payment"]["reference"].startswith("ch_fake_")

    await admin.put(f"{API}/admin/settings/invoice.seller_name", json={"value": "Someone Else", "reason": "test"}, headers=csrf_headers(admin))
    assert (await guest.get(f"{API}/me/bookings/{b['id']}/invoice")).json()["seller"]["name"] == "NearBnB Pty Ltd"   # already issued: unchanged

    other = await complete_guest(new_client, "peeker@example.com")
    assert (await other.get(f"{API}/me/bookings/{b['id']}/invoice")).status_code == 404
    assert (await host.get(f"{API}/me/bookings/{b['id']}/invoice")).status_code == 404


async def test_invoice_numbers_count_up_and_a_booking_gets_only_one(new_client, make_admin, db):
    _, host, _, _, unit = await world(new_client, make_admin, quantity=2)
    guest = await complete_guest(new_client)
    ids = []
    for d in (10, 14):
        b = (await book(guest, unit, days=d)).json()
        await host.post(f"{API}/host/bookings/{b['id']}/accept", headers=csrf_headers(host))
        await dev_complete(guest, b["id"])
        await dev_complete(guest, b["id"])                                   # asking again is harmless
        ids.append(b["id"])
    numbers = sorted(i.number for i in await row(db, Invoice))
    assert numbers == ["INV-000001", "INV-000002"]


# ---------- webhooks: idempotent and signed ----------

async def test_a_replayed_webhook_changes_nothing(new_client, make_admin, db, webhook_secret):
    _, guest, _, b = await accepted(new_client, make_admin)
    started = (await pay(guest, b["id"])).json()
    (payment,) = await row(db, Payment)
    anon = await new_client()
    event = intent_event("evt_pay_1", "succeeded", payment.stripe_payment_intent_id, amount=started["amount"], status="succeeded")

    assert (await send(anon, event)).json() == {"received": True}
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "confirmed"
    replay = await send(anon, event)
    assert replay.json().get("duplicate") is True
    same_but_new_id = await send(anon, intent_event("evt_pay_2", "succeeded", payment.stripe_payment_intent_id, amount=started["amount"], status="succeeded"))
    assert same_but_new_id.status_code == 200

    assert len(await row(db, Invoice)) == 1
    assert len(await emails(db, "payment_confirmed_guest")) == 1 and len(await emails(db, "booking_confirmed_host")) == 1
    assert len(await row(db, BookingEvent, booking_id=uuid.UUID(b["id"]), to_status=B_CONFIRMED)) == 1
    (payment,) = await row(db, Payment)
    assert payment.status == "succeeded" and payment.stripe_charge_id == "ch_test_1"


async def test_payment_webhooks_need_a_valid_signature_and_ignore_other_projects_payments(new_client, make_admin, db, webhook_secret):
    _, guest, _, b = await accepted(new_client, make_admin)
    started = (await pay(guest, b["id"])).json()
    (payment,) = await row(db, Payment)
    anon = await new_client()
    forged = json.dumps(intent_event("evt_forged", "succeeded", payment.stripe_payment_intent_id, amount=started["amount"], status="succeeded")).encode()
    bad = await anon.post(f"{API}/webhooks/stripe", content=forged, headers={"stripe-signature": "t=1,v1=deadbeef", "content-type": "application/json"})
    assert bad.status_code == 400
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "payment_pending"
    foreign = await send(anon, intent_event("evt_foreign", "succeeded", "pi_belongs_to_another_project", amount=100, status="succeeded"))
    assert foreign.status_code == 200
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "payment_pending"


async def test_a_failed_payment_can_be_retried_until_the_deadline(new_client, make_admin, db, webhook_secret):
    _, guest, _, b = await accepted(new_client, make_admin)
    started = (await pay(guest, b["id"])).json()
    (payment,) = await row(db, Payment)
    anon = await new_client()
    error = {"code": "card_declined", "decline_code": "insufficient_funds", "message": "Your card has insufficient funds."}
    await send(anon, intent_event("evt_f1", "payment_failed", payment.stripe_payment_intent_id, amount=started["amount"], status="requires_payment_method", error=error, charge=None))

    mine = (await guest.get(f"{API}/me/bookings/{b['id']}")).json()
    assert mine["status"] == "accepted" and mine["can_pay"] is True
    assert mine["payment"] == {"status": "failed", "failure_message": "Your card has insufficient funds."}
    mail = (await emails(db, "payment_failed_guest", "guest@example.com"))[0]
    assert f"/account/trips/{b['id']}/pay" in mail.body_text

    retry = await pay(guest, b["id"])
    assert retry.status_code == 200 and retry.json()["client_secret"] == started["client_secret"]   # same intent, new card
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "payment_pending"
    await send(anon, intent_event("evt_s1", "succeeded", payment.stripe_payment_intent_id, amount=started["amount"], status="succeeded"))
    final = (await guest.get(f"{API}/me/bookings/{b['id']}")).json()
    assert final["status"] == "confirmed" and final["payment"]["failure_message"] is None


async def test_a_late_failure_message_never_undoes_a_success(new_client, make_admin, db, webhook_secret):
    _, guest, _, b = await accepted(new_client, make_admin)
    started = (await pay(guest, b["id"])).json()
    (payment,) = await row(db, Payment)
    anon = await new_client()
    await send(anon, intent_event("evt_a", "succeeded", payment.stripe_payment_intent_id, amount=started["amount"], status="succeeded"))
    await send(anon, intent_event("evt_b", "payment_failed", payment.stripe_payment_intent_id, amount=started["amount"], status="requires_payment_method",
                                  error={"code": "card_declined", "message": "late"}, charge=None))
    await send(anon, intent_event("evt_c", "processing", payment.stripe_payment_intent_id, amount=started["amount"], status="processing"))
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "confirmed"
    (payment,) = await row(db, Payment)
    assert payment.status == "succeeded"


async def test_two_confirmations_at_the_same_moment_confirm_once(new_client, make_admin, db):
    _, guest, _, b = await accepted(new_client, make_admin)
    await pay(guest, b["id"])
    (payment,) = await row(db, Payment)
    info = IntentInfo(id=payment.stripe_payment_intent_id, status="succeeded", amount=payment.amount, currency="AUD", charge_id="ch_x")

    async def apply():
        async with SessionLocal() as s:
            outcome = await payments.apply_intent(s, info)
            await s.commit()
            return outcome

    outcomes = sorted(await asyncio.gather(apply(), apply(), apply()))
    assert outcomes == ["confirmed", "duplicate", "duplicate"]
    assert len(await row(db, Invoice)) == 1 and len(await emails(db, "payment_confirmed_guest")) == 1


# ---------- too late, wrong amount ----------

async def test_a_payment_that_arrives_after_the_dates_were_released_is_refunded(new_client, make_admin, db, webhook_secret):
    host, guest, unit, b = await accepted(new_client, make_admin)
    started = (await pay(guest, b["id"])).json()
    (payment,) = await row(db, Payment)
    past = datetime.now(UTC) - timedelta(minutes=1)
    await db.execute(update(Booking).where(Booking.id == uuid.UUID(b["id"])).values(payment_deadline_at=past))
    await db.execute(text("update room_occupancy set expires_at = :p where booking_id = :b"), {"p": past, "b": b["id"]})
    await db.commit()
    async with SessionLocal() as s:
        assert await booking_service.expire_due(s) == 1
        await s.commit()
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "payment_expired"
    assert (await row(db, Payment))[0].status == "cancelled"
    other = await complete_guest(new_client, "taker@example.com")
    assert (await book(other, unit)).status_code == 201                       # someone else took the dates

    anon = await new_client()
    await send(anon, intent_event("evt_late", "succeeded", payment.stripe_payment_intent_id, amount=started["amount"], status="succeeded"))
    mine = (await guest.get(f"{API}/me/bookings/{b['id']}")).json()
    assert mine["status"] == "payment_expired" and mine["invoice_number"] is None
    (refund,) = await row(db, Refund)
    assert refund.type == "late_payment" and refund.amount == 29563 and refund.status == "succeeded" and refund.stripe_refund_id.startswith("re_fake_")
    assert len(await emails(db, "payment_late_refund_guest", "guest@example.com")) == 1
    assert await row(db, Invoice) == [] and await emails(db, "booking_confirmed_host", "host@example.com") == []


async def test_a_wrong_amount_is_refunded_not_confirmed(new_client, make_admin, db, webhook_secret):
    _, guest, _, b = await accepted(new_client, make_admin)
    await pay(guest, b["id"])
    (payment,) = await row(db, Payment)
    await send(await new_client(), intent_event("evt_amt", "succeeded", payment.stripe_payment_intent_id, amount=100, status="succeeded"))
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "payment_expired"
    (refund,) = await row(db, Refund)
    assert refund.reason == "The amount paid did not match the booking."
    assert await row(db, Invoice) == []


async def test_a_failed_automatic_refund_is_recorded_for_an_admin(new_client, make_admin, db, monkeypatch):
    _, guest, _, b = await accepted(new_client, make_admin)
    await pay(guest, b["id"])
    (payment,) = await row(db, Payment)

    def boom(self, **_):
        raise RuntimeError("stripe is down")

    monkeypatch.setattr(gw.FakeStripeGateway, "refund_payment", boom)
    async with SessionLocal() as s:
        await payments.apply_intent(s, IntentInfo(id=payment.stripe_payment_intent_id, status="succeeded", amount=1, currency="AUD"))
        await s.commit()
    (refund,) = await row(db, Refund)
    assert refund.status == "failed" and "stripe is down" in refund.error


# ---------- cancelling an unpaid payment ----------

async def test_withdrawing_cancels_a_failed_payment_so_it_cannot_be_paid_later(new_client, make_admin, db, webhook_secret):
    host, guest, _, b = await accepted(new_client, make_admin)
    started = (await pay(guest, b["id"])).json()
    (payment,) = await row(db, Payment)
    await send(await new_client(), intent_event("evt_w", "payment_failed", payment.stripe_payment_intent_id, amount=started["amount"],
                                                status="requires_payment_method", error={"code": "card_declined", "message": "no"}, charge=None))
    r = await guest.post(f"{API}/me/bookings/{b['id']}/withdraw", headers=csrf_headers(guest))
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    assert (await row(db, Payment))[0].status == "cancelled"


async def test_you_cannot_withdraw_while_a_payment_is_in_progress(new_client, make_admin):
    _, guest, _, b = await accepted(new_client, make_admin)
    await pay(guest, b["id"])
    r = await guest.post(f"{API}/me/bookings/{b['id']}/withdraw", headers=csrf_headers(guest))
    assert r.status_code == 409


async def test_an_unpaid_payment_window_that_closes_cancels_the_payment(new_client, make_admin, db):
    _, guest, _, b = await accepted(new_client, make_admin)
    await pay(guest, b["id"])
    past = datetime.now(UTC) - timedelta(minutes=1)
    await db.execute(update(Booking).where(Booking.id == uuid.UUID(b["id"])).values(payment_deadline_at=past))
    await db.commit()
    async with SessionLocal() as s:
        assert await booking_service.expire_due(s) == 1
        await s.commit()
    assert (await row(db, Payment))[0].status == "cancelled"
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "payment_expired"


# ---------- state machine ----------

async def test_the_state_machine_refuses_impossible_moves(new_client, make_admin, db):
    _, guest, _, b = await accepted(new_client, make_admin)
    async with SessionLocal() as s:
        booking = await s.get(Booking, uuid.UUID(b["id"]))
        with pytest.raises(Exception) as err:
            booking_service.transition(s, booking, B_REQUESTED)
        assert "cannot become" in str(err.value)
        with pytest.raises(Exception):
            booking_service.transition(s, booking, "completed")


# ---------- reconcile (missed webhooks) ----------

async def test_reconcile_confirms_a_payment_whose_webhook_never_arrived(new_client, make_admin, db, monkeypatch):
    _, guest, _, b = await accepted(new_client, make_admin)
    await pay(guest, b["id"])
    real_id = "pi_real_123"
    await db.execute(update(Payment).values(stripe_payment_intent_id=real_id, created_at=datetime.now(UTC) - timedelta(minutes=5)))
    await db.commit()

    class Paid(gw.FakeStripeGateway):
        def retrieve_payment_intent(self, intent_id):
            return IntentInfo(id=intent_id, status="succeeded", amount=29563, currency="AUD", charge_id="ch_real_9")

    monkeypatch.setattr(payments, "get_gateway", lambda: Paid())
    async with SessionLocal() as s:
        assert await payments.reconcile_pending(s) == 1
        await s.commit()
    assert (await guest.get(f"{API}/me/bookings/{b['id']}")).json()["status"] == "confirmed"
    async with SessionLocal() as s:
        assert await payments.reconcile_pending(s) == 0                      # nothing left to do


async def test_reconcile_leaves_brand_new_and_fake_payments_alone(new_client, make_admin, db, monkeypatch):
    _, guest, _, b = await accepted(new_client, make_admin)
    await pay(guest, b["id"])
    monkeypatch.setattr(payments, "get_gateway", lambda: (_ for _ in ()).throw(AssertionError("should not call Stripe")))
    async with SessionLocal() as s:
        assert await payments.reconcile_pending(s) == 0
    await db.execute(update(Payment).values(stripe_payment_intent_id="pi_real_new"))
    await db.commit()
    async with SessionLocal() as s:
        assert await payments.reconcile_pending(s, older_than_seconds=3600) == 0   # real, but younger than the cut-off


async def test_sync_reads_stripe_not_the_browser(new_client, make_admin, db, monkeypatch):
    _, guest, _, b = await accepted(new_client, make_admin)
    await pay(guest, b["id"])
    r = await guest.post(f"{API}/me/bookings/{b['id']}/pay/sync", headers=csrf_headers(guest))
    assert r.json()["outcome"] == "noop" and r.json()["booking"]["status"] == "payment_pending"     # Stripe says: not paid

    class Paid(gw.FakeStripeGateway):
        def retrieve_payment_intent(self, intent_id):
            return IntentInfo(id=intent_id, status="succeeded", amount=29563, currency="AUD", charge_id="ch_s")

    monkeypatch.setattr(payments, "get_gateway", lambda: Paid())
    r = await guest.post(f"{API}/me/bookings/{b['id']}/pay/sync", headers=csrf_headers(guest))
    assert r.json()["outcome"] == "confirmed" and r.json()["booking"]["status"] == "confirmed"
    other = await complete_guest(new_client, "nosy@example.com")
    assert (await other.post(f"{API}/me/bookings/{b['id']}/pay/sync", headers=csrf_headers(other))).status_code == 404


async def test_the_development_shortcut_is_refused_when_real_stripe_keys_are_set(new_client, make_admin, monkeypatch):
    _, guest, _, b = await accepted(new_client, make_admin)
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_something")
    r = await dev_complete(guest, b["id"])
    assert r.status_code == 403


# ---------- the Stripe wiring itself (stand-in client, no network) ----------

class Recorder:
    def __init__(self):
        self.calls = []
        outer = self

        class PI:
            def create(self, params, options=None):
                outer.calls.append(("pi.create", params, options))
                return SimpleNamespace(to_dict=lambda: {"id": "pi_1", "status": "requires_payment_method", "client_secret": "pi_1_secret", "amount": params["amount"], "currency": params["currency"]})

            def retrieve(self, intent_id):
                return SimpleNamespace(to_dict=lambda: {"id": intent_id, "status": "succeeded", "amount": 500, "currency": "aud", "latest_charge": {"id": "ch_9"}})

            def cancel(self, intent_id):
                outer.calls.append(("pi.cancel", intent_id))

        class Refunds:
            def create(self, params, options=None):
                outer.calls.append(("refund.create", params, options))
                return SimpleNamespace(id="re_1")

        self.v1 = SimpleNamespace(payment_intents=PI(), refunds=Refunds())


def real_gateway(recorder: Recorder) -> RealStripeGateway:
    g = RealStripeGateway.__new__(RealStripeGateway)
    g._client = recorder
    return g


def test_the_real_gateway_charges_the_platform_with_a_transfer_group_and_an_idempotency_key():
    rec = Recorder()
    info = real_gateway(rec).create_payment_intent(amount=29563, currency="AUD", booking_id="b-1", reference="NB-ABCD2345", description="d", idempotency_key="booking-b-1-attempt-1")
    (_, params, options), = rec.calls
    assert params["amount"] == 29563 and params["currency"] == "aud" and params["transfer_group"] == "NB-ABCD2345"
    assert params["metadata"] == {"booking_id": "b-1", "reference": "NB-ABCD2345"} and params["automatic_payment_methods"] == {"enabled": True}
    assert "transfer_data" not in params and "on_behalf_of" not in params       # separate charges and transfers: the host is paid later
    assert options == {"idempotency_key": "booking-b-1-attempt-1"}
    assert info.id == "pi_1" and info.client_secret == "pi_1_secret"


def test_the_real_gateway_refunds_in_full_with_an_idempotency_key():
    rec = Recorder()
    assert real_gateway(rec).refund_payment(intent_id="pi_1", amount=500, reason="late", idempotency_key="late-refund-1") == "re_1"
    (_, params, options), = rec.calls
    assert params["payment_intent"] == "pi_1" and params["amount"] == 500 and options == {"idempotency_key": "late-refund-1"}


def test_intents_are_read_the_same_from_the_api_and_from_a_webhook():
    api = intent_from_stripe(SimpleNamespace(to_dict=lambda: {"id": "pi_1", "status": "succeeded", "amount": 500, "currency": "aud", "latest_charge": {"id": "ch_9"}}))
    hook = intent_from_stripe({"id": "pi_1", "status": "succeeded", "amount": 500, "currency": "aud", "latest_charge": "ch_9", "metadata": {"a": "b"}})
    assert api.charge_id == hook.charge_id == "ch_9" and api.currency == hook.currency == "AUD" and hook.metadata == {"a": "b"}
    failed = intent_from_stripe({"id": "pi_2", "status": "requires_payment_method", "last_payment_error": {"code": "card_declined", "decline_code": "lost_card", "message": "Declined"}})
    assert failed.failure_code == "lost_card" and failed.failure_message == "Declined" and failed.charge_id is None
