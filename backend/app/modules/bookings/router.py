import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user, require_admin
from app.core.errors import AppError
from app.core.ratelimit import client_ip, rate_limit
from app.modules.bookings import service
from app.modules.hosts import service as host_service
from app.modules.notifications.service import kick_outbox
from app.modules.payments import service as payments
from app.modules.users.models import User

router = APIRouter(tags=["bookings"])


class BookIn(BaseModel):
    checkin_local: str = Field(max_length=25)
    hours: int = Field(ge=1, le=2160)
    guests: int = Field(ge=1, le=100)


class RejectIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


async def _tidy(session: AsyncSession) -> None:
    """Close anything that has run out of time before showing or changing bookings."""
    if await service.expire_due(session):
        await session.commit()
        kick_outbox()


async def _host(session: AsyncSession, user: User):
    return await host_service.require_host(session, user)


# ---------- guest ----------

@router.get("/me/booking-readiness")
async def booking_readiness(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await service.readiness(session, user)


@router.post("/units/{unit_id}/book", status_code=201, dependencies=[Depends(rate_limit("book", 20, 3600))])
async def create_request(unit_id: str, body: BookIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    try:
        uid = uuid.UUID(unit_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "This place is not available.") from None
    booking = await service.create_request(session, user, uid, body.checkin_local, body.hours, body.guests, client_ip(request))
    await session.commit()
    kick_outbox()
    return (await service.serialize(session, [booking], "guest"))[0]


@router.get("/me/bookings")
async def my_bookings(group: str | None = None, status: str | None = Query(None, max_length=20), page: int = Query(1, ge=1, le=500), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await _tidy(session)
    return await service.list_for(session, guest_id=user.id, group=group, status=status, viewer="guest", page=page)


@router.get("/me/bookings/{booking_id}")
async def my_booking(booking_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await _tidy(session)
    return await service.get_for(session, booking_id, guest_id=user.id, viewer="guest")


@router.post("/me/bookings/{booking_id}/withdraw")
async def withdraw(booking_id: str, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    booking = await service.withdraw(session, user, booking_id, client_ip(request))
    await session.commit()
    kick_outbox()
    return (await service.serialize(session, [booking], "guest"))[0]


# ---------- paying ----------

@router.post("/me/bookings/{booking_id}/pay", dependencies=[Depends(rate_limit("pay", 30, 3600))])
async def start_payment(booking_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """Creates or reuses the payment for an accepted booking and returns what the card form needs."""
    result = await payments.start_payment(session, user, booking_id)
    await session.commit()
    return result


@router.post("/me/bookings/{booking_id}/pay/sync", dependencies=[Depends(rate_limit("pay_sync", 60, 3600))])
async def sync_payment(booking_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """Reads the payment's state from Stripe (not from the browser) and applies it. Safe to call as often as needed."""
    outcome = await payments.sync_payment(session, user, booking_id)
    await session.commit()
    kick_outbox()
    return {"outcome": outcome, "booking": await service.get_for(session, booking_id, guest_id=user.id, viewer="guest")}


@router.post("/me/bookings/{booking_id}/pay/dev-complete")
async def dev_complete_payment(booking_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """Development only (fake Stripe gateway): behaves as if Stripe reported the payment succeeded."""
    outcome = await payments.dev_complete(session, user, booking_id)
    await session.commit()
    kick_outbox()
    return {"outcome": outcome, "booking": await service.get_for(session, booking_id, guest_id=user.id, viewer="guest")}


@router.get("/me/bookings/{booking_id}/invoice")
async def my_invoice(booking_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await payments.get_invoice(session, user, booking_id)


# ---------- host ----------

@router.get("/host/bookings")
async def host_bookings(group: str | None = None, status: str | None = Query(None, max_length=20), page: int = Query(1, ge=1, le=500), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    await _tidy(session)
    return await service.list_for(session, host_id=host.id, group=group, status=status, viewer="host", page=page)


@router.get("/host/bookings/{booking_id}")
async def host_booking(booking_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    await _tidy(session)
    return await service.get_for(session, booking_id, host_id=host.id, viewer="host")


@router.post("/host/bookings/{booking_id}/accept")
async def accept(booking_id: str, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    booking = await service.accept(session, host, user, booking_id, client_ip(request))
    await session.commit()
    kick_outbox()
    return (await service.serialize(session, [booking], "host"))[0]


@router.post("/host/bookings/{booking_id}/reject")
async def reject(booking_id: str, body: RejectIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    booking = await service.reject(session, host, user, booking_id, body.note, client_ip(request))
    await session.commit()
    kick_outbox()
    return (await service.serialize(session, [booking], "host"))[0]


# ---------- admin (read only for now) ----------

@router.get("/admin/bookings")
async def admin_bookings(group: str | None = None, status: str | None = None, q: str | None = Query(None, max_length=100), page: int = Query(1, ge=1, le=500),
                         _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    await _tidy(session)
    return await service.list_for(session, group=group, status=status, viewer="admin", page=page, search=q)
