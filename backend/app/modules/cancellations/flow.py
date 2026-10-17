"""Cancelling a confirmed booking (guest, host or admin), the refund that follows, and exceptional host refunds.

Who gets what:
  * guest cancels      -> refund by the booking's stored policy; the host keeps the rest (minus commission)
  * host cancels       -> guest gets everything back; the host is paid nothing; the cancellation is counted against the host
  * admin cancels      -> a reason is required and the admin chooses policy or full refund
  * exceptional refund -> a host asks, an admin approves, and the amount comes out of the host's earnings"""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.audit import service as audit
from app.modules.bookings import service as bookings
from app.modules.bookings.models import B_CANCELLED, B_CHECKED_IN, B_COMPLETED, B_CONFIRMED, Booking
from app.modules.cancellations import service as templates
from app.modules.cancellations.policy import RefundQuote, calculate_refund
from app.modules.hosts.models import HostProfile
from app.modules.payments.models import (
    PAY_SUCCEEDED, RR_APPROVED, RR_PENDING, RR_REJECTED, Payment, Refund, RefundRequest,
)
from app.modules.payments.refunds import execute_refund
from app.modules.properties.models import RoomOccupancy
from app.modules.settings import service as settings_service
from app.modules.users.models import User


def _now() -> datetime:
    return datetime.now(UTC)


async def _policy(session: AsyncSession, b: Booking) -> dict:
    """The cancellation policy this booking was made under. Bookings made before policies were stored fall back to the host's current one."""
    stored = b.policy_snapshot.get("cancellation")
    if stored:
        return stored
    return await templates.snapshot_for(session, b.policy_snapshot.get("cancellation_template"))


async def quote_for(session: AsyncSession, b: Booking, mode: str, now: datetime | None = None) -> RefundQuote:
    policy = await _policy(session, b)
    return calculate_refund(
        pricing=b.pricing_snapshot, tiers=policy["tiers"], policy_name=policy["name"], check_in=b.check_in, now=now or _now(), mode=mode,
        service_fee_refundable=bool(b.policy_snapshot.get("service_fee_refundable", False)),
    )


# ---------- preview ----------

async def preview(session: AsyncSession, guest: User, booking_id: str) -> dict:
    b = await bookings._locked(session, booking_id)
    if b.guest_id != guest.id:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    if b.status != B_CONFIRMED:
        raise AppError(409, "INVALID_STATE", "Only a confirmed booking can be cancelled." if b.status != B_CANCELLED else "This booking is already cancelled.")
    quote = await quote_for(session, b, "policy")
    policy = await _policy(session, b)
    return {"booking_id": str(b.id), "currency": b.currency, "paid": b.pricing_snapshot["total"], "policy": {"name": policy["name"], "tiers": policy["tiers"]},
            "service_fee_refundable": bool(b.policy_snapshot.get("service_fee_refundable", False)), "refund": quote.to_dict()}


# ---------- cancelling ----------

async def _succeeded_payment(session: AsyncSession, b: Booking) -> Payment:
    payment = (await session.execute(select(Payment).where(Payment.booking_id == b.id, Payment.status == PAY_SUCCEEDED))).scalars().first()
    if payment is None:
        raise AppError(409, "NOT_PAID", "This booking has no completed payment to refund.")
    return payment


async def _cancel(session: AsyncSession, b: Booking, *, by: str, actor: User, reason: str | None, mode: str, ip: str | None) -> tuple[Booking, RefundQuote]:
    now = _now()
    payment = await _succeeded_payment(session, b)
    quote = await quote_for(session, b, mode, now)
    b.closed_reason = reason
    b.cancelled_at, b.cancelled_by, b.cancelled_by_role = now, actor.id, by
    bookings.transition(session, b, B_CANCELLED, actor_id=actor.id, note=f"cancelled by {by}" + (f": {reason}" if reason else ""))
    b.settlement = {"by": by, "mode": mode, "refund": quote.to_dict(), "host_payable": quote.host_payable, "cancelled_at": now.isoformat()}
    # The dates are free again straight away.
    await session.execute(delete(RoomOccupancy).where(RoomOccupancy.booking_id == b.id, RoomOccupancy.kind == "booking"))

    refund = None
    if quote.total > 0:
        refund = Refund(booking_id=b.id, payment_id=payment.id, amount=quote.total, type={"guest": "policy", "host": "host_cancel", "admin": "admin"}[by],
                        reason=reason or f"Cancelled by {by} ({quote.policy_name}, {quote.refund_pct}%)", initiated_by=actor.id)
        session.add(refund)
        await session.flush()
        await execute_refund(session, refund)
    audit.record(session, action=f"booking.cancelled_by_{by}", entity_type="booking", entity_id=b.id, actor_id=actor.id, ip=ip, reason=reason,
                 after={"refund": quote.total, "refund_pct": quote.refund_pct, "refund_status": refund.status if refund else "none", "host_payable": quote.host_payable})
    extra = {"cancelled_by": by, "refund": bookings.money(quote.total, b.currency), "refund_amount": quote.total, "refund_pct": quote.refund_pct,
             "policy_name": quote.policy_name, "reason": reason or "", "host_payout": bookings.money(quote.host_payable, b.currency)}
    await bookings._notify(session, b, "guest", "booking_cancelled_guest", **extra)
    await bookings._notify(session, b, "host", "booking_cancelled_host", **extra)
    return b, quote


async def guest_cancel(session: AsyncSession, guest: User, booking_id: str, expected_refund: int | None, ip: str | None) -> Booking:
    b = await bookings._locked(session, booking_id)
    if b.guest_id != guest.id:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    if b.status != B_CONFIRMED:
        raise AppError(409, "INVALID_STATE", "Only a confirmed booking can be cancelled." if b.status != B_CANCELLED else "This booking is already cancelled.")
    if b.check_in <= _now():
        raise AppError(409, "TOO_LATE", "The stay has already started.")
    current = (await quote_for(session, b, "policy")).total
    if expected_refund is not None and expected_refund != current:
        # The guest agreed to one amount but time moved them into another step: ask again rather than surprise them.
        raise AppError(409, "REFUND_CHANGED", f"The refund changed to {bookings.money(current, b.currency)} while you were deciding. Please check it and confirm again.")
    b, _ = await _cancel(session, b, by="guest", actor=guest, reason=None, mode="policy", ip=ip)
    return b


async def host_cancel(session: AsyncSession, host: HostProfile, actor: User, booking_id: str, reason: str, ip: str | None) -> Booking:
    reason = (reason or "").strip()
    if not reason:
        raise AppError(422, "REASON_REQUIRED", "Tell us why you are cancelling. The guest will see this reason.")
    b = await bookings._locked(session, booking_id)
    if b.host_id != host.id:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    if b.status != B_CONFIRMED:
        raise AppError(409, "INVALID_STATE", "Only a confirmed booking can be cancelled." if b.status != B_CANCELLED else "This booking is already cancelled.")
    if b.check_in <= _now():
        raise AppError(409, "TOO_LATE", "The stay has already started. Contact support to cancel it.")
    b, _ = await _cancel(session, b, by="host", actor=actor, reason=reason, mode="full", ip=ip)
    await _check_cancellation_alert(session, host)
    return b


async def admin_cancel(session: AsyncSession, admin: User, booking_id: str, reason: str, refund: str, ip: str | None) -> Booking:
    reason = (reason or "").strip()
    if not reason:
        raise AppError(422, "REASON_REQUIRED", "A reason is required.")
    if refund not in ("policy", "full"):
        raise AppError(422, "INVALID_REFUND", "Choose a refund by policy or in full.")
    b = await bookings._locked(session, booking_id)
    if b.status not in (B_CONFIRMED, B_CHECKED_IN):
        raise AppError(409, "INVALID_STATE", "Only a confirmed or in-progress booking can be cancelled.")
    b, _ = await _cancel(session, b, by="admin", actor=admin, reason=reason, mode="full" if refund == "full" else "policy", ip=ip)
    return b


# ---------- host cancellation tracking ----------

async def host_cancellation_stats(session: AsyncSession, host_id: uuid.UUID) -> dict:
    now = _now()

    async def count(days: int | None) -> int:
        q = select(func.count()).select_from(Booking).where(Booking.host_id == host_id, Booking.cancelled_by_role == "host")
        if days:
            q = q.where(Booking.cancelled_at >= now - timedelta(days=days))
        return (await session.execute(q)).scalar_one()

    threshold = await settings_service.get_value(session, "alerts.host_cancellation_count")
    window = await settings_service.get_value(session, "alerts.host_cancellation_days")
    in_window = await count(window)
    recent = (await session.execute(
        select(Booking).where(Booking.host_id == host_id, Booking.cancelled_by_role == "host").order_by(Booking.cancelled_at.desc()).limit(20))).scalars().all()
    return {
        "total": await count(None), "last_30_days": await count(30), "last_90_days": await count(90), "threshold": threshold, "window_days": window,
        "in_window": in_window, "alert": in_window >= threshold,
        "items": [{"reference": b.reference, "at": b.cancelled_at.isoformat() if b.cancelled_at else None, "reason": b.closed_reason} for b in recent],
    }


async def _check_cancellation_alert(session: AsyncSession, host: HostProfile) -> None:
    """Tell the admins when a host reaches the configured number of cancellations in the window (once, when it is first reached)."""
    stats = await host_cancellation_stats(session, host.id)
    if stats["in_window"] == stats["threshold"]:
        owner = await session.get(User, host.user_id)
        await bookings.notify_admins(session, "host_cancellation_alert_admin", {
            "host_name": owner.full_name, "host_email": owner.email, "count": stats["in_window"], "days": stats["window_days"], "host_id": str(host.id)})


# ---------- exceptional host refunds ----------

async def _refundable_left(session: AsyncSession, payment: Payment) -> int:
    taken = (await session.execute(select(func.coalesce(func.sum(Refund.amount), 0)).where(Refund.payment_id == payment.id, Refund.status.in_(("pending", "succeeded"))))).scalar_one()
    return payment.amount - int(taken)


async def request_exceptional(session: AsyncSession, host: HostProfile, actor: User, booking_id: str, amount: int, reason: str) -> RefundRequest:
    reason = (reason or "").strip()
    if not reason:
        raise AppError(422, "REASON_REQUIRED", "Explain why the guest should be refunded.")
    b = await bookings._locked(session, booking_id)
    if b.host_id != host.id:
        raise AppError(404, "NOT_FOUND", "Booking not found.")
    if b.status not in (B_CONFIRMED, B_CHECKED_IN, B_COMPLETED):
        raise AppError(409, "INVALID_STATE", "A refund can be requested for a paid booking that has not been cancelled.")
    payment = await _succeeded_payment(session, b)
    left = await _refundable_left(session, payment)
    if amount <= 0 or amount > left:
        raise AppError(422, "INVALID_AMOUNT", f"Choose an amount between 1 cent and {bookings.money(left, b.currency)}.")
    pending = (await session.execute(select(RefundRequest.id).where(RefundRequest.booking_id == b.id, RefundRequest.status == RR_PENDING))).first()
    if pending:
        raise AppError(409, "ALREADY_REQUESTED", "A refund request for this booking is already waiting for review.")
    req = RefundRequest(booking_id=b.id, host_id=host.id, amount=amount, reason=reason)
    session.add(req)
    await session.flush()
    audit.record(session, action="refund_request.created", entity_type="booking", entity_id=b.id, actor_id=actor.id, reason=reason, after={"amount": amount})
    await bookings.notify_admins(session, "refund_requested_admin", {"reference": b.reference, "host_name": actor.full_name, "amount": bookings.money(amount, b.currency), "reason": reason})
    return req


async def decide_exceptional(session: AsyncSession, admin: User, request_id: str, decision: str, note: str | None, amount: int | None, ip: str | None) -> RefundRequest:
    if decision not in ("approve", "reject"):
        raise AppError(422, "INVALID_DECISION", "Decision must be approve or reject.")
    note = (note or "").strip()
    if decision == "reject" and not note:
        raise AppError(422, "NOTE_REQUIRED", "Tell the host why the request was rejected.")
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Request not found.") from None
    req = (await session.execute(select(RefundRequest).where(RefundRequest.id == rid).with_for_update())).scalar_one_or_none()
    if req is None:
        raise AppError(404, "NOT_FOUND", "Request not found.")
    if req.status != RR_PENDING:
        raise AppError(409, "ALREADY_DECIDED", "This request has already been decided.")
    b = await bookings._locked(session, str(req.booking_id))
    req.reviewed_by, req.reviewed_at, req.note = admin.id, _now(), note or None
    if decision == "reject":
        req.status = RR_REJECTED
        await bookings._notify(session, b, "host", "refund_request_decided_host", decision="rejected", amount=bookings.money(req.amount, b.currency), decision_note=note)
        audit.record(session, action="refund_request.rejected", entity_type="booking", entity_id=b.id, actor_id=admin.id, reason=note, ip=ip)
        return req
    payment = await _succeeded_payment(session, b)
    granted = amount if amount is not None else req.amount
    left = await _refundable_left(session, payment)
    if granted <= 0 or granted > left:
        raise AppError(422, "INVALID_AMOUNT", f"Choose an amount between 1 cent and {bookings.money(left, b.currency)}.")
    refund = Refund(booking_id=b.id, payment_id=payment.id, amount=granted, type="exceptional", reason=req.reason, initiated_by=admin.id)
    session.add(refund)
    await session.flush()
    await execute_refund(session, refund)
    req.status, req.refund_id = RR_APPROVED, refund.id
    audit.record(session, action="refund_request.approved", entity_type="booking", entity_id=b.id, actor_id=admin.id, reason=note, ip=ip, after={"amount": granted, "refund_status": refund.status})
    await bookings._notify(session, b, "host", "refund_request_decided_host", decision="approved", amount=bookings.money(granted, b.currency), decision_note=note)
    await bookings._notify(session, b, "guest", "refund_issued_guest", refund=bookings.money(granted, b.currency), reason=req.reason)
    return req


async def retry_refund(session: AsyncSession, admin: User, refund_id: str, ip: str | None) -> Refund:
    try:
        rid = uuid.UUID(refund_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Refund not found.") from None
    refund = (await session.execute(select(Refund).where(Refund.id == rid).with_for_update())).scalar_one_or_none()
    if refund is None:
        raise AppError(404, "NOT_FOUND", "Refund not found.")
    if refund.status != "failed":
        raise AppError(409, "INVALID_STATE", "Only a failed refund can be retried.")
    await execute_refund(session, refund)
    audit.record(session, action="refund.retried", entity_type="booking", entity_id=refund.booking_id, actor_id=admin.id, ip=ip, after={"status": refund.status})
    return refund
