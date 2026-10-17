import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user, require_admin
from app.core.errors import AppError
from app.core.ratelimit import client_ip, rate_limit
from app.modules.bookings import service as bookings
from app.modules.reviews import service
from app.modules.reviews.models import DIR_GUEST_TO_HOST, DIR_HOST_TO_GUEST
from app.modules.users.models import User

router = APIRouter(tags=["reviews"])


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=service.MAX_COMMENT)


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=500)


@router.post("/me/bookings/{booking_id}/review", status_code=201, dependencies=[Depends(rate_limit("review", 30, 3600))])
async def guest_review(booking_id: str, body: ReviewIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """The guest reviews the property and host. Public once published (see the rules in the reviews service)."""
    await service.submit(session, user, booking_id, DIR_GUEST_TO_HOST, body.rating, body.comment)
    await session.commit()
    return await bookings.get_for(session, booking_id, guest_id=user.id, viewer="guest")


@router.post("/host/bookings/{booking_id}/review", status_code=201, dependencies=[Depends(rate_limit("review", 30, 3600))])
async def host_review(booking_id: str, body: ReviewIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """The host reviews the guest. Private: only other hosts who get a request from this guest, and admins, can ever read it."""
    from app.modules.hosts import service as host_service

    host = await host_service.require_host(session, user)
    await service.submit(session, user, booking_id, DIR_HOST_TO_GUEST, body.rating, body.comment)
    await session.commit()
    return await bookings.get_for(session, booking_id, host_id=host.id, viewer="host")


@router.get("/properties/{property_id}/reviews")
async def property_reviews(property_id: str, page: int = Query(1, ge=1, le=500), session: AsyncSession = Depends(get_session)):
    """Public. Published guest reviews of a property, newest first. Hidden reviews never appear."""
    try:
        pid = uuid.UUID(property_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "This place is not available.") from None
    return await service.public_reviews(session, pid, page)


@router.get("/admin/reviews")
async def admin_reviews(status: str | None = Query(None, pattern="^(submitted|published|hidden)$"), direction: str | None = Query(None, pattern="^(guest_to_host|host_to_guest)$"),
                        q: str | None = Query(None, max_length=100), page: int = Query(1, ge=1, le=1000), _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await service.admin_list(session, status=status, direction=direction, q=q, page=page)


@router.post("/admin/reviews/{review_id}/hide")
async def hide_review(review_id: str, body: ReasonIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    r = await service.hide(session, admin, review_id, body.reason, client_ip(request))
    await session.commit()
    return {"id": str(r.id), "status": r.status}


@router.post("/admin/reviews/{review_id}/restore")
async def restore_review(review_id: str, body: ReasonIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    r = await service.restore(session, admin, review_id, body.reason, client_ip(request))
    await session.commit()
    return {"id": str(r.id), "status": r.status}
