"""Cancelling, refunds and payouts: the guest, host and admin endpoints."""
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user, require_admin
from app.core.errors import AppError
from app.core.ratelimit import client_ip, rate_limit
from app.modules.bookings import service as bookings
from app.modules.cancellations import flow
from app.modules.hosts import service as host_service
from app.modules.notifications.service import kick_outbox
from app.modules.payments.models import Refund, RefundRequest
from app.modules.payouts import service as payouts
from app.modules.properties.models import Property
from app.modules.users.models import User

router = APIRouter(tags=["cancellations and payouts"])


class GuestCancelIn(BaseModel):
    expected_refund: int | None = Field(default=None, ge=0)  # the amount the guest was shown; if it changed meanwhile we ask again


class HostCancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class AdminCancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    refund: str = Field(pattern="^(policy|full)$")


class RefundRequestIn(BaseModel):
    amount: int = Field(gt=0, le=100_000_000)
    reason: str = Field(min_length=1, max_length=1000)


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str | None = Field(default=None, max_length=1000)
    amount: int | None = Field(default=None, gt=0)


class HoldIn(BaseModel):
    hold: bool
    note: str | None = Field(default=None, max_length=500)


# ---------- guest ----------

@router.get("/me/bookings/{booking_id}/cancel-preview")
async def cancel_preview(booking_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """What cancelling right now would refund. The same calculation runs when the guest confirms."""
    return await flow.preview(session, user, booking_id)


@router.post("/me/bookings/{booking_id}/cancel", dependencies=[Depends(rate_limit("cancel", 20, 3600))])
async def guest_cancel(booking_id: str, body: GuestCancelIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    booking = await flow.guest_cancel(session, user, booking_id, body.expected_refund, client_ip(request))
    await session.commit()
    kick_outbox()
    return (await bookings.serialize(session, [booking], "guest"))[0]


# ---------- host ----------

@router.post("/host/bookings/{booking_id}/cancel", dependencies=[Depends(rate_limit("host_cancel", 20, 3600))])
async def host_cancel(booking_id: str, body: HostCancelIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    booking = await flow.host_cancel(session, host, user, booking_id, body.reason, client_ip(request))
    await session.commit()
    kick_outbox()
    return (await bookings.serialize(session, [booking], "host"))[0]


@router.post("/host/bookings/{booking_id}/refund-request", status_code=201, dependencies=[Depends(rate_limit("refund_request", 20, 3600))])
async def host_refund_request(booking_id: str, body: RefundRequestIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    req = await flow.request_exceptional(session, host, user, booking_id, body.amount, body.reason)
    await session.commit()
    kick_outbox()
    return _request_json(req, None)


@router.get("/host/bookings/{booking_id}/refund-requests")
async def host_refund_requests(booking_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    rows = (await session.execute(select(RefundRequest).where(RefundRequest.booking_id == _uuid(booking_id), RefundRequest.host_id == host.id)
                                  .order_by(RefundRequest.created_at.desc()))).scalars().all()
    return [_request_json(r, None) for r in rows]


@router.get("/host/payout-records")
async def host_payout_records(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """Bank payouts Stripe has made (or tried to make) to this host."""
    host = await host_service.require_host(session, user)
    return [{"id": r.stripe_payout_id, "amount": r.amount, "currency": r.currency, "status": r.status,
             "arrival_date": r.arrival_date.isoformat() if r.arrival_date else None, "failure_code": r.failure_code} for r in await payouts.list_records(session, host.id)]


# ---------- admin ----------

@router.post("/admin/bookings/{booking_id}/cancel")
async def admin_cancel(booking_id: str, body: AdminCancelIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    booking = await flow.admin_cancel(session, admin, booking_id, body.reason, body.refund, client_ip(request))
    await session.commit()
    kick_outbox()
    return (await bookings.serialize(session, [booking], "admin"))[0]


@router.post("/admin/bookings/{booking_id}/payout-hold")
async def payout_hold(booking_id: str, body: HoldIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    booking = await payouts.set_hold(session, admin, booking_id, body.hold, body.note, client_ip(request))
    await session.commit()
    return (await bookings.serialize(session, [booking], "admin"))[0]


@router.post("/admin/bookings/{booking_id}/payout-retry")
async def payout_retry(booking_id: str, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    booking = await payouts.retry_failed(session, admin, booking_id, client_ip(request))
    await session.commit()
    return (await bookings.serialize(session, [booking], "admin"))[0]


@router.get("/admin/refunds")
async def admin_refunds(status: str | None = Query(None, pattern="^(pending|succeeded|failed)$"), session: AsyncSession = Depends(get_session), _: User = Depends(require_admin)):
    q = select(Refund).order_by(Refund.created_at.desc()).limit(200)
    if status:
        q = q.where(Refund.status == status)
    out = []
    for r in (await session.execute(q)).scalars():
        booking = await session.get(bookings.Booking, r.booking_id)
        out.append({"id": str(r.id), "reference": booking.reference, "booking_id": str(booking.id), "amount": r.amount, "currency": booking.currency, "type": r.type,
                    "status": r.status, "reason": r.reason, "error": r.error, "stripe_refund_id": r.stripe_refund_id, "host_reversal_amount": r.host_reversal_amount,
                    "created_at": r.created_at.isoformat()})
    return out


@router.post("/admin/refunds/{refund_id}/retry")
async def admin_retry_refund(refund_id: str, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    refund = await flow.retry_refund(session, admin, refund_id, client_ip(request))
    await session.commit()
    return {"id": str(refund.id), "status": refund.status, "error": refund.error}


@router.get("/admin/refund-requests")
async def admin_refund_requests(status: str | None = Query(None, pattern="^(pending|approved|rejected)$"), session: AsyncSession = Depends(get_session), _: User = Depends(require_admin)):
    q = select(RefundRequest).order_by(RefundRequest.created_at.desc()).limit(200)
    if status:
        q = q.where(RefundRequest.status == status)
    out = []
    for r in (await session.execute(q)).scalars():
        booking = await session.get(bookings.Booking, r.booking_id)
        prop = await session.get(Property, booking.property_id)
        host_user = await session.get(User, (await session.get(host_service.HostProfile, r.host_id)).user_id)
        out.append(_request_json(r, {"reference": booking.reference, "property": prop.name, "host": host_user.full_name, "currency": booking.currency, "paid": booking.pricing_snapshot["total"]}))
    return out


@router.post("/admin/refund-requests/{request_id}/decision")
async def decide_refund_request(request_id: str, body: DecisionIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    req = await flow.decide_exceptional(session, admin, request_id, body.decision, body.note, body.amount, client_ip(request))
    await session.commit()
    kick_outbox()
    return _request_json(req, None)


def _uuid(value: str):
    import uuid

    try:
        return uuid.UUID(value)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Booking not found.") from None


def _request_json(r: RefundRequest, extra: dict | None) -> dict:
    return {"id": str(r.id), "booking_id": str(r.booking_id), "amount": r.amount, "reason": r.reason, "status": r.status, "note": r.note,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None, "created_at": r.created_at.isoformat(), **(extra or {})}
