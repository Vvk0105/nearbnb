import re
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import AppError
from app.core.ratelimit import rate_limit
from app.modules.availability import service as availability
from app.modules.search import service

router = APIRouter(tags=["search"])
MONTH = re.compile(r"^(\d{4})-(\d{2})$")


class QuoteIn(BaseModel):
    checkin_local: str = Field(max_length=25)
    hours: int = Field(default=24, ge=1, le=2160)
    guests: int = Field(default=1, ge=1, le=100)


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "This place is not available.") from None


@router.get("/search", dependencies=[Depends(rate_limit("search", 120, 60))])
async def search(
    q: str | None = Query(None, max_length=120),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_km: float = Query(25, ge=1, le=300),
    checkin: str | None = Query(None, max_length=25, description="Local time at the property, YYYY-MM-DDTHH:MM"),
    hours: int = Query(24, ge=1, le=2160),
    guests: int = Query(1, ge=1, le=100),
    min_price: int | None = Query(None, ge=0, description="cents per 24h"),
    max_price: int | None = Query(None, ge=0),
    type_ids: list[uuid.UUID] = Query(default=[]),
    amenity_ids: list[uuid.UUID] = Query(default=[]),
    sort: str = Query("relevance", pattern="^(relevance|price_asc|price_desc|newest)$"),
    page: int = Query(1, ge=1, le=200),
    page_size: int = Query(12, ge=1, le=48),
    session: AsyncSession = Depends(get_session),
):
    if (lat is None) != (lng is None):
        raise AppError(422, "INVALID_LOCATION", "Send both lat and lng, or neither.")
    return await service.search(
        session, q=q, lat=lat, lng=lng, radius_km=radius_km, checkin=checkin, hours=hours, guests=guests, min_price=min_price,
        max_price=max_price, type_ids=type_ids, amenity_ids=amenity_ids, sort=sort, page=page, page_size=page_size,
    )


@router.get("/search/suggest", dependencies=[Depends(rate_limit("suggest", 240, 60))])
async def suggest(q: str = Query(..., min_length=2, max_length=80), session: AsyncSession = Depends(get_session)):
    """What to offer under the search box as someone types: matching property names and the areas those properties are in."""
    return await service.suggest(session, q)


@router.get("/meta/booking")
async def booking_meta(session: AsyncSession = Depends(get_session)):
    g = await availability.load_global_rules(session)
    return {
        "interval_minutes": g["checkin.interval_minutes"], "step_hours": g["booking.duration_step_hours"],
        "max_stay_hours": g["booking.max_stay_hours"], "min_lead_hours": g["booking.min_lead_time_hours"],
        "max_advance_days": g["booking.max_advance_days"],
    }


@router.get("/units/{unit_id}/availability/calendar", dependencies=[Depends(rate_limit("calendar", 120, 60))])
async def month_calendar(unit_id: str, month: str = Query(..., description="YYYY-MM"), session: AsyncSession = Depends(get_session)):
    m = MONTH.match(month)
    if not m:
        raise AppError(422, "INVALID_MONTH", "Use the format YYYY-MM.")
    year, mon = int(m.group(1)), int(m.group(2))
    this_year = datetime.now(UTC).year
    if not (1 <= mon <= 12) or abs(year - this_year) > 3:
        raise AppError(422, "INVALID_MONTH", "Choose a month within the next few years.")
    unit, prop, host = await availability.public_unit(session, _uuid(unit_id))
    return await availability.month_calendar(session, unit, prop, host, year, mon)


@router.get("/units/{unit_id}/availability/day", dependencies=[Depends(rate_limit("timeline", 120, 60))])
async def day_timeline(unit_id: str, day: str = Query(..., alias="date", description="YYYY-MM-DD"), session: AsyncSession = Depends(get_session)):
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise AppError(422, "INVALID_DATE", "Use the format YYYY-MM-DD.") from None
    unit, prop, _ = await availability.public_unit(session, _uuid(unit_id))
    return await availability.day_timeline(session, unit, prop, d)


@router.post("/units/{unit_id}/quote", dependencies=[Depends(rate_limit("quote", 60, 60))])
async def quote(unit_id: str, body: QuoteIn, session: AsyncSession = Depends(get_session)):
    unit, prop, host = await availability.public_unit(session, _uuid(unit_id))
    return await availability.build_quote(session, unit, prop, host, body.checkin_local, body.hours, body.guests)
