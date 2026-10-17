"""Running a refund at Stripe, taking the host's share back first when it was already paid out.

A refund row is created in the caller's transaction; `execute_refund` then does the Stripe side. If Stripe is unreachable the
row is marked failed (not lost) so an admin, or the retry button, can run it again: every Stripe call carries an idempotency
key built from the refund id, so a retry never refunds twice."""
import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.payments.models import Payment, Refund
from app.modules.payments.stripe_gateway import RefundInfo, get_gateway
from app.modules.payouts.models import TR_REVERSED, TR_TRANSFERRED, Transfer

log = logging.getLogger(__name__)
RF_PENDING, RF_SUCCEEDED, RF_FAILED = "pending", "succeeded", "failed"


def _status_from_stripe(stripe_status: str) -> str:
    return {"succeeded": RF_SUCCEEDED, "failed": RF_FAILED, "canceled": RF_FAILED}.get(stripe_status, RF_PENDING)


def host_reversal_amount(refund: Refund, payment: Payment, transfer: Transfer) -> int:
    """How much of the host's payout to take back for this refund, never more than is left of it.
    Exceptional refunds come out of the host's earnings in full; for the others the host gives back their share of the money."""
    if refund.type == "exceptional":
        wanted = refund.amount
    else:
        wanted = int((Decimal(transfer.amount) * refund.amount / payment.amount).to_integral_value()) if payment.amount else 0
    return max(0, min(wanted, transfer.amount - transfer.reversed_amount))


async def execute_refund(session: AsyncSession, refund: Refund) -> Refund:
    """Idempotent: safe to call again on a failed refund. Never raises for a Stripe problem, it records it on the row."""
    payment = await session.get(Payment, refund.payment_id)
    refund.error = None
    try:
        transfer = (await session.execute(
            select(Transfer).where(Transfer.booking_id == refund.booking_id, Transfer.status.in_((TR_TRANSFERRED, TR_REVERSED))).with_for_update()
        )).scalar_one_or_none()
        if transfer is not None and transfer.stripe_transfer_id and refund.stripe_reversal_id is None:
            amount = host_reversal_amount(refund, payment, transfer)
            if amount > 0:
                refund.stripe_reversal_id = await asyncio.to_thread(
                    get_gateway().reverse_transfer, transfer_id=transfer.stripe_transfer_id, amount=amount, idempotency_key=f"reverse-{refund.id}")
                refund.host_reversal_amount = amount
                transfer.reversed_amount += amount
                if transfer.reversed_amount >= transfer.amount:
                    transfer.status, transfer.reversed_at = TR_REVERSED, _now()
        info = await asyncio.to_thread(
            get_gateway().refund_payment, intent_id=payment.stripe_payment_intent_id, amount=refund.amount,
            reason=refund.reason or refund.type, idempotency_key=f"refund-{refund.id}")
        apply_refund_info(refund, info)
    except Exception as exc:  # noqa: BLE001
        refund.status, refund.error = RF_FAILED, str(exc)[:500]
        log.exception("refund %s failed", refund.id)
    return refund


def apply_refund_info(refund: Refund, info: RefundInfo) -> None:
    refund.stripe_refund_id = info.id or refund.stripe_refund_id
    refund.status = _status_from_stripe(info.status)
    refund.error = info.failure_reason if refund.status == RF_FAILED else None


async def apply_refund_event(session: AsyncSession, info: RefundInfo) -> str:
    """A `refund.*` webhook (or a reconcile read): bring our row in line with Stripe."""
    refund = (await session.execute(select(Refund).where(Refund.stripe_refund_id == info.id).with_for_update())).scalar_one_or_none()
    if refund is None:
        return "unknown"
    before = refund.status
    if before == RF_SUCCEEDED and info.status != "succeeded":
        return "duplicate"  # a stale message never undoes a refund that went through
    apply_refund_info(refund, info)
    return "unchanged" if refund.status == before else refund.status


async def reconcile_pending_refunds(session: AsyncSession, older_than_seconds: int = 60, limit: int = 100) -> int:
    from datetime import UTC, datetime

    cutoff = datetime.now(UTC).timestamp() - older_than_seconds
    rows = (await session.execute(
        select(Refund).where(Refund.status == RF_PENDING, Refund.stripe_refund_id.is_not(None), Refund.stripe_refund_id.not_like("re_fake_%"))
        .order_by(Refund.created_at).limit(limit)
    )).scalars().all()
    changed = 0
    for refund in rows:
        if refund.created_at.timestamp() > cutoff:
            continue
        try:
            info = await asyncio.to_thread(get_gateway().retrieve_refund, refund.stripe_refund_id)
            if await apply_refund_event(session, info) in (RF_SUCCEEDED, RF_FAILED):
                changed += 1
        except Exception:  # noqa: BLE001
            log.exception("refund reconcile failed for %s", refund.id)
    return changed


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)
