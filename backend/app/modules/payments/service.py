"""Paying for an accepted booking.

The rules this module enforces:
  * the amount charged always comes from the booking's stored price snapshot, never from the browser;
  * a booking is confirmed only when Stripe says the PaymentIntent succeeded. That arrives by webhook, or is read
    from Stripe by the server (`sync_payment`, the reconcile job). Never from what the browser claims;
  * `apply_intent` is the single place that acts on a PaymentIntent, and it is idempotent, so a replayed webhook, a
    webhook plus a sync, or two syncs at once change nothing the second time;
  * money that arrives for dates we no longer hold is refunded automatically."""
import asyncio
import logging
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.audit import service as audit
from app.modules.availability import service as availability
from app.modules.bookings import service as bookings
from app.modules.bookings.models import B_ACCEPTED, B_CONFIRMED, B_PAYMENT_EXPIRED, B_PAYMENT_PENDING, Booking
from app.modules.hosts.models import HostProfile
from app.modules.payments.models import (
    PAY_CANCELLED, PAY_CREATED, PAY_FAILED, PAY_PROCESSING, PAY_SUCCEEDED, Invoice, Payment, Refund,
)
from app.modules.payments.stripe_gateway import IntentInfo, get_gateway
from app.modules.properties.models import Property, RoomOccupancy, Unit
from app.modules.settings import service as settings_service
from app.modules.users.models import User

log = logging.getLogger(__name__)
OPEN = (B_ACCEPTED, B_PAYMENT_PENDING)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------- starting a payment ----------

async def _latest_payment(session: AsyncSession, booking_id: uuid.UUID) -> Payment | None:
    return (await session.execute(
        select(Payment).where(Payment.booking_id == booking_id).order_by(Payment.created_at.desc()).limit(1).with_for_update()
    )).scalars().first()


async def start_payment(session: AsyncSession, guest: User, booking_id: str) -> dict:
    """Creates (or reuses) the PaymentIntent for this booking and returns what the browser needs to show the card form."""
    await bookings.expire_due(session)
    b = await bookings._locked(session, booking_id)
    if b.guest_id != guest.id:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    if b.status not in OPEN:
        raise AppError(409, "INVALID_STATE", "This booking is not waiting for payment." if b.status != B_CONFIRMED else "This booking is already paid.")
    now = _now()
    if b.payment_deadline_at is None or b.payment_deadline_at <= now:
        raise AppError(409, "PAYMENT_EXPIRED", "The time to pay has run out.")

    gateway = get_gateway()
    payment = await _latest_payment(session, b.id)
    info: IntentInfo | None = None
    if payment is not None and payment.status != PAY_CANCELLED:
        info = await asyncio.to_thread(gateway.retrieve_payment_intent, payment.stripe_payment_intent_id)
        if info.status == "succeeded":  # paid already and we simply have not heard yet
            outcome = await apply_intent(session, info)
            if outcome == "confirmed":
                raise AppError(409, "ALREADY_PAID", "This booking is already paid.")
        if info.status == "canceled":
            payment.status = PAY_CANCELLED
            info = None
    if info is None:
        attempt = (await session.execute(select(func.count()).select_from(Payment).where(Payment.booking_id == b.id))).scalar_one() + 1
        total = b.pricing_snapshot["total"]
        prop = await session.get(Property, b.property_id)
        info = await asyncio.to_thread(
            gateway.create_payment_intent, amount=total, currency=b.currency, booking_id=str(b.id), reference=b.reference,
            description=f"NearBnB booking {b.reference}: {prop.name}", idempotency_key=f"booking-{b.id}-attempt-{attempt}",
        )
        payment = Payment(booking_id=b.id, stripe_payment_intent_id=info.id, amount=total, currency=b.currency, attempt=attempt)
        session.add(payment)
    if b.status == B_ACCEPTED:
        bookings.transition(session, b, B_PAYMENT_PENDING, actor_id=guest.id, note="payment started")
    await session.flush()
    return {
        "booking_id": str(b.id), "client_secret": info.client_secret, "amount": payment.amount, "currency": b.currency,
        "publishable_key": get_settings().stripe_publishable_key or None, "fake": gateway.is_fake,
        "seconds_to_pay": bookings._seconds_left(b.payment_deadline_at, now),
    }


# ---------- acting on what Stripe says ----------

async def apply_intent(session: AsyncSession, info: IntentInfo) -> str:
    """The one place a PaymentIntent's state changes our data. Returns what happened:
    'confirmed' | 'refunded' | 'failed' | 'processing' | 'cancelled' | 'duplicate' | 'unknown' | 'noop'."""
    payment = (await session.execute(select(Payment).where(Payment.stripe_payment_intent_id == info.id).with_for_update())).scalar_one_or_none()
    if payment is None:
        return "unknown"  # another project's payment on a shared Stripe account, or not ours
    b = await bookings._locked(session, str(payment.booking_id))

    if info.status == "succeeded":
        if payment.status == PAY_SUCCEEDED:
            return "duplicate"
        return await _on_succeeded(session, payment, b, info)
    if payment.status == PAY_SUCCEEDED:
        return "duplicate"  # a late "failed" or "processing" event must never undo a success
    if info.status == "processing":
        payment.status = PAY_PROCESSING
        return "processing"
    if info.status == "canceled":
        payment.status = PAY_CANCELLED
        return "cancelled"
    if info.failure_code or info.failure_message:
        payment.status, payment.failure_code, payment.failure_message = PAY_FAILED, info.failure_code, info.failure_message
        if b.status == B_PAYMENT_PENDING:  # back to "pay now", the guest may try another card until the deadline
            bookings.transition(session, b, B_ACCEPTED, note=f"payment failed: {info.failure_code or 'declined'}")
        await bookings._notify(session, b, "guest", "payment_failed_guest")
        audit.record(session, action="payment.failed", entity_type="booking", entity_id=b.id, after={"code": info.failure_code})
        return "failed"
    return "noop"


async def _on_succeeded(session: AsyncSession, payment: Payment, b: Booking, info: IntentInfo) -> str:
    now = _now()
    payment.status, payment.stripe_charge_id, payment.succeeded_at = PAY_SUCCEEDED, info.charge_id, now
    payment.failure_code = payment.failure_message = None
    hold = (await session.execute(select(RoomOccupancy).where(RoomOccupancy.booking_id == b.id, RoomOccupancy.kind == "hold").with_for_update())).scalar_one_or_none()

    if info.amount != payment.amount or (info.currency and info.currency != payment.currency):
        log.error("payment %s amount mismatch: stripe %s %s, expected %s %s", payment.id, info.amount, info.currency, payment.amount, payment.currency)
        return await _refund_late(session, payment, b, "The amount paid did not match the booking.")
    if b.status not in OPEN or hold is None:
        return await _refund_late(session, payment, b, "Paid after the dates were released.")

    bookings.transition(session, b, B_CONFIRMED, note="payment succeeded")
    b.confirmed_at = now
    hold.kind, hold.expires_at = "booking", None  # the same row, same room, same period: no gap in which anyone else can take it
    invoice = await issue_invoice(session, b, payment)
    audit.record(session, action="payment.succeeded", entity_type="booking", entity_id=b.id, after={"amount": payment.amount, "invoice": invoice.number})
    await bookings._notify(session, b, "guest", "payment_confirmed_guest")
    await bookings._notify(session, b, "host", "booking_confirmed_host")
    return "confirmed"


async def _refund_late(session: AsyncSession, payment: Payment, b: Booking, reason: str) -> str:
    """Money arrived for dates we no longer hold. Give it back in full and tell the guest."""
    refund = Refund(booking_id=b.id, payment_id=payment.id, amount=payment.amount, reason=reason, type="late_payment")
    session.add(refund)
    await session.flush()
    try:
        info = await asyncio.to_thread(
            get_gateway().refund_payment, intent_id=payment.stripe_payment_intent_id, amount=payment.amount, reason=reason,
            idempotency_key=f"late-refund-{payment.id}",
        )
        refund.stripe_refund_id = info.id
        refund.status = "succeeded" if info.status == "succeeded" else "pending"
    except Exception as exc:  # noqa: BLE001  (an admin sees failed refunds; Phase 7 adds the queue)
        refund.status, refund.error = "failed", str(exc)[:500]
        log.exception("automatic refund failed for payment %s", payment.id)
    if b.status in OPEN:
        b.closed_reason = reason
        bookings.transition(session, b, B_PAYMENT_EXPIRED, note=reason)
    audit.record(session, action="payment.refunded_late", entity_type="booking", entity_id=b.id, after={"amount": payment.amount, "status": refund.status})
    await bookings._notify(session, b, "guest", "payment_late_refund_guest")
    return "refunded"


async def sync_payment(session: AsyncSession, guest: User, booking_id: str) -> str:
    """Asks Stripe (server to server) where the guest's payment stands and applies it. Used when the guest returns from
    paying, so the screen is right even if the webhook is a few seconds behind."""
    b = await session.get(Booking, uuid.UUID(booking_id)) if _is_uuid(booking_id) else None
    if b is None or b.guest_id != guest.id:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    payment = await _latest_payment(session, b.id)
    if payment is None:
        return "noop"
    info = await asyncio.to_thread(get_gateway().retrieve_payment_intent, payment.stripe_payment_intent_id)
    return await apply_intent(session, info)


async def reconcile_pending(session: AsyncSession, older_than_seconds: int = 60, limit: int = 100) -> int:
    """Safety net for missed webhooks: payments still open at Stripe are re-read and applied."""
    cutoff = _now().timestamp() - older_than_seconds
    rows = (await session.execute(
        select(Payment).join(Booking, Booking.id == Payment.booking_id)
        .where(Payment.status.in_((PAY_CREATED, PAY_PROCESSING, PAY_FAILED)), Booking.status.in_(OPEN), Payment.stripe_payment_intent_id.not_like("pi_fake_%"))
        .order_by(Payment.created_at).limit(limit)
    )).scalars().all()
    changed = 0
    for payment in rows:
        if payment.created_at.timestamp() > cutoff:
            continue
        try:
            info = await asyncio.to_thread(get_gateway().retrieve_payment_intent, payment.stripe_payment_intent_id)
            if await apply_intent(session, info) in ("confirmed", "refunded", "failed", "cancelled", "processing"):
                changed += 1
        except Exception:  # noqa: BLE001
            log.exception("reconcile failed for payment %s", payment.id)
    return changed


async def dev_complete(session: AsyncSession, guest: User, booking_id: str) -> str:
    """Development only (fake gateway): pretends Stripe reported success, through exactly the same code as a real webhook."""
    settings = get_settings()
    if settings.stripe_secret_key or settings.is_production:
        raise AppError(403, "NOT_AVAILABLE", "Only available with the fake Stripe gateway in development.")
    started = await start_payment(session, guest, booking_id)
    payment = await _latest_payment(session, uuid.UUID(started["booking_id"]))
    info = IntentInfo(id=payment.stripe_payment_intent_id, status="succeeded", amount=payment.amount, currency=payment.currency,
                      charge_id=f"ch_fake_{uuid.uuid4().hex[:16]}")
    return await apply_intent(session, info)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


# ---------- invoice ----------

def _line(label: str, amount: int) -> dict:
    return {"description": label, "amount": amount}


async def issue_invoice(session: AsyncSession, b: Booking, payment: Payment) -> Invoice:
    existing = (await session.execute(select(Invoice).where(Invoice.booking_id == b.id))).scalar_one_or_none()
    if existing is not None:
        return existing
    prop = await session.get(Property, b.property_id)
    unit = await session.get(Unit, b.unit_id)
    guest = await session.get(User, b.guest_id)
    p = b.pricing_snapshot
    tz = ZoneInfo(b.timezone)
    per_night = p["accommodation"] // p["nights"] if p["nights"] else 0
    money = bookings.money
    lines = [_line(f"Accommodation: {p['nights']} night{'s' if p['nights'] != 1 else ''} at {money(per_night, b.currency)}", p["accommodation"])]
    if p["cleaning_fee"]:
        lines.append(_line("Cleaning fee", p["cleaning_fee"]))
    if p["service_fee"]:
        lines.append(_line(f"Service fee ({p['service_fee_percent']:g}%)", p["service_fee"]))
    seq = (await session.execute(select(func.nextval("invoice_number_seq")))).scalar_one()
    data = {
        "seller": {
            "name": await settings_service.get_value(session, "invoice.seller_name"),
            "tax_id": await settings_service.get_value(session, "invoice.seller_tax_id"),
            "address": await settings_service.get_value(session, "invoice.seller_address"),
        },
        "buyer": {"name": guest.full_name, "email": guest.email},
        "stay": {
            "reference": b.reference, "property": prop.name, "unit": unit.name,
            "address": ", ".join(x for x in (prop.address_line1, prop.suburb, prop.state, prop.postcode) if x),
            "check_in": availability.fmt_local(b.check_in, tz), "check_out": availability.fmt_local(b.check_out, tz),
            "timezone": b.timezone, "hours": b.hours, "nights": p["nights"], "guests": b.guests,
        },
        "lines": lines,
        "tax": {"name": p.get("tax_name"), "rate_percent": p.get("tax_rate_percent"), "amount": p["tax"], "inclusive": p.get("tax_inclusive", False)},
        "total": p["total"], "currency": b.currency,
        "payment": {"reference": payment.stripe_charge_id or payment.stripe_payment_intent_id, "paid_at": (payment.succeeded_at or _now()).isoformat()},
        "footer": await settings_service.get_value(session, "invoice.footer_note"),
    }
    invoice = Invoice(booking_id=b.id, seq=seq, number=f"INV-{seq:06d}", data=data)
    session.add(invoice)
    await session.flush()
    return invoice


async def get_invoice(session: AsyncSession, guest: User, booking_id: str) -> dict:
    if not _is_uuid(booking_id):
        raise AppError(404, "NOT_FOUND", "Invoice not found.")
    row = (await session.execute(
        select(Invoice, Booking).join(Booking, Booking.id == Invoice.booking_id).where(Booking.id == uuid.UUID(booking_id), Booking.guest_id == guest.id)
    )).first()
    if row is None:
        raise AppError(404, "NOT_FOUND", "Invoice not found.")
    invoice, _ = row
    return {"number": invoice.number, "issued_at": invoice.issued_at.isoformat(), **invoice.data}
