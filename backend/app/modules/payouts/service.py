"""Paying hosts: a Stripe transfer of the host's share, released a configurable time after check-in (or check-out).

A payout is held back while the booking has an open refund or refund request, while an admin has put a hold on it, and while the
host is suspended or not set up for payouts. It is retried with a growing delay after a failure, and an admin is told when it
keeps failing."""
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.bookings import service as bookings
from app.modules.bookings.models import B_CANCELLED, B_CHECKED_IN, B_COMPLETED, B_CONFIRMED, Booking
from app.modules.hosts import service as host_service
from app.modules.hosts.models import HostProfile
from app.modules.payments.models import PAY_SUCCEEDED, RR_PENDING, Payment, Refund, RefundRequest
from app.modules.payments.stripe_gateway import get_gateway
from app.modules.payouts.models import TR_ELIGIBLE, TR_FAILED, TR_REVERSED, TR_SKIPPED, TR_TRANSFERRED, PayoutRecord, Transfer
from app.modules.users.models import User

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
PAYABLE = (B_CONFIRMED, B_CHECKED_IN, B_COMPLETED, B_CANCELLED)


def _now() -> datetime:
    return datetime.now(UTC)


def base_amount(b: Booking) -> int:
    """What the host earns from this booking before any refund taken out of their share."""
    if b.settlement is not None:
        return int(b.settlement.get("host_payable", 0))
    return int(b.pricing_snapshot["host_payable"])


def eligible_at(b: Booking) -> datetime:
    policy = b.policy_snapshot
    anchor = b.check_out if policy.get("payout_anchor") == "check_out" else b.check_in
    return anchor + timedelta(hours=int(policy.get("payout_delay_hours", 24)))


async def exceptional_deductions(session: AsyncSession, booking_id) -> int:
    rows = (await session.execute(select(Refund.amount).where(Refund.booking_id == booking_id, Refund.type == "exceptional", Refund.status.in_(("pending", "succeeded"))))).all()
    return sum(r[0] for r in rows)


def payout_view(b: Booking, tr: Transfer | None) -> dict:
    """For host and admin screens: the amount (before any exceptional refund), when it is due, and where it stands."""
    if b.status not in PAYABLE or (tr is None and b.status == B_CANCELLED and base_amount(b) <= 0):
        return {"state": "none", "amount": 0, "eligible_at": None}
    base = {"amount": tr.amount if tr else base_amount(b), "eligible_at": eligible_at(b).isoformat(), "hold": b.payout_hold, "hold_note": b.payout_hold_note}
    if tr is None:
        return {"state": "held" if b.payout_hold else "scheduled", **base}
    state = {TR_ELIGIBLE: "scheduled", TR_FAILED: "failed", TR_TRANSFERRED: "paid", TR_REVERSED: "reversed", TR_SKIPPED: "none"}[tr.status]
    return {"state": "held" if b.payout_hold and state == "scheduled" else state, **base, "transferred_at": tr.transferred_at.isoformat() if tr.transferred_at else None,
            "reversed_amount": tr.reversed_amount, "error": tr.error if tr.status == TR_FAILED else None, "attempts": tr.attempts}


async def _blocked(session: AsyncSession, b: Booking) -> bool:
    open_refund = (await session.execute(select(Refund.id).where(Refund.booking_id == b.id, Refund.status.in_(("pending", "failed"))).limit(1))).first()
    open_request = (await session.execute(select(RefundRequest.id).where(RefundRequest.booking_id == b.id, RefundRequest.status == RR_PENDING).limit(1))).first()
    return open_refund is not None or open_request is not None


async def release_due(session: AsyncSession, now: datetime | None = None, limit: int = 200) -> dict:
    """Pays every host whose payout is due. Returns counts. Safe to run often and from several workers at once."""
    now = now or _now()
    counts = {"paid": 0, "failed": 0, "skipped": 0}
    candidates = (await session.execute(
        select(Booking).outerjoin(Transfer, Transfer.booking_id == Booking.id)
        .where(Booking.status.in_(PAYABLE), Booking.payout_hold.is_(False), Booking.check_in <= now,
               or_(Transfer.id.is_(None), and_(Transfer.status == TR_FAILED, Transfer.attempts < MAX_ATTEMPTS, Transfer.next_attempt_at <= now)))
        .order_by(Booking.check_in).limit(limit).with_for_update(of=Booking, skip_locked=True)
    )).scalars().all()
    for b in candidates:
        due = eligible_at(b)
        if due > now or (b.status == B_CANCELLED and base_amount(b) <= 0):
            continue
        host = await session.get(HostProfile, b.host_id)
        if not host_service.is_active(host) or not host.stripe_account_id or await _blocked(session, b):
            continue
        payment = (await session.execute(select(Payment).where(Payment.booking_id == b.id, Payment.status == PAY_SUCCEEDED))).scalars().first()
        if payment is None:
            continue
        amount = base_amount(b) - await exceptional_deductions(session, b.id)
        tr = (await session.execute(select(Transfer).where(Transfer.booking_id == b.id).with_for_update())).scalar_one_or_none()
        if tr is None:
            tr = Transfer(booking_id=b.id, host_id=b.host_id, amount=max(0, amount), currency=b.currency, eligible_at=due, status=TR_ELIGIBLE)
            session.add(tr)
            await session.flush()
        tr.amount = max(0, amount)
        if amount <= 0:
            tr.status = TR_SKIPPED
            counts["skipped"] += 1
            continue
        try:
            tr.stripe_transfer_id = await asyncio.to_thread(
                get_gateway().create_transfer, amount=tr.amount, currency=b.currency, destination=host.stripe_account_id,
                source_transaction=payment.stripe_charge_id, transfer_group=b.reference, idempotency_key=f"transfer-{b.id}")
            tr.status, tr.transferred_at, tr.error = TR_TRANSFERRED, now, None
            counts["paid"] += 1
            audit.record(session, action="payout.transferred", entity_type="booking", entity_id=b.id, after={"amount": tr.amount})
            owner = await session.get(User, host.user_id)
            await bookings._notify(session, b, "host", "payout_sent_host", payout=bookings.money(tr.amount, b.currency), host_name=owner.full_name)
        except Exception as exc:  # noqa: BLE001
            tr.attempts += 1
            tr.status, tr.error = TR_FAILED, str(exc)[:500]
            tr.next_attempt_at = now + timedelta(minutes=min(5 * 2 ** tr.attempts, 360))
            counts["failed"] += 1
            log.exception("payout failed for booking %s", b.id)
            if tr.attempts >= MAX_ATTEMPTS:
                await bookings.notify_admins(session, "payout_failed_admin", {"reference": b.reference, "amount": bookings.money(tr.amount, b.currency), "error": tr.error})
    return counts


async def set_hold(session: AsyncSession, admin: User, booking_id: str, hold: bool, note: str | None, ip: str | None) -> Booking:
    b = await bookings._locked(session, booking_id)
    b.payout_hold, b.payout_hold_note = hold, (note or "").strip() or None
    audit.record(session, action="payout.hold" if hold else "payout.release_hold", entity_type="booking", entity_id=b.id, actor_id=admin.id, reason=b.payout_hold_note, ip=ip)
    return b


async def retry_failed(session: AsyncSession, admin: User, booking_id: str, ip: str | None) -> Booking:
    b = await bookings._locked(session, booking_id)
    tr = (await session.execute(select(Transfer).where(Transfer.booking_id == b.id).with_for_update())).scalar_one_or_none()
    if tr is not None and tr.status == TR_FAILED:
        tr.attempts, tr.next_attempt_at = 0, _now()
        audit.record(session, action="payout.retry", entity_type="booking", entity_id=b.id, actor_id=admin.id, ip=ip)
    return b


# ---------- Stripe's own bank payouts to the host (from payout.* webhooks) ----------

async def record_payout(session: AsyncSession, account_id: str | None, payout: dict) -> str:
    if not account_id or not payout.get("id"):
        return "ignored"
    host = (await session.execute(select(HostProfile).where(HostProfile.stripe_account_id == account_id))).scalar_one_or_none()
    if host is None:
        return "unknown"
    row = (await session.execute(select(PayoutRecord).where(PayoutRecord.stripe_payout_id == payout["id"]).with_for_update())).scalar_one_or_none()
    arrival = datetime.fromtimestamp(payout["arrival_date"], UTC) if payout.get("arrival_date") else None
    if row is None:
        row = PayoutRecord(host_id=host.id, stripe_account_id=account_id, stripe_payout_id=payout["id"], amount=int(payout.get("amount") or 0),
                           currency=(payout.get("currency") or "").upper(), status=payout.get("status", "pending"))
        session.add(row)
    row.status, row.arrival_date, row.failure_code = payout.get("status", row.status), arrival, payout.get("failure_code")
    return "recorded"


async def list_records(session: AsyncSession, host_id) -> list[PayoutRecord]:
    return list((await session.execute(select(PayoutRecord).where(PayoutRecord.host_id == host_id).order_by(PayoutRecord.created_at.desc()).limit(50))).scalars())
