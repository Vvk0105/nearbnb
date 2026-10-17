import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import AppError
from app.core.ratelimit import rate_limit
from app.modules.places import service
from app.modules.settings import service as settings_service

router = APIRouter(prefix="/places", tags=["places"])
PLACE_ID = re.compile(r"^[A-Za-z0-9_-]{10,300}$")


@router.get("/status")
async def status():
    """Lets the frontend fall back to plain text search when no Google key is configured."""
    return {"enabled": service.enabled()}


@router.get("/autocomplete", dependencies=[Depends(rate_limit("places", 120, 60))])
async def autocomplete(
    q: str = Query(..., min_length=2, max_length=120),
    kind: str = Query("region", pattern="^(region|address)$"),
    token: str | None = Query(None, max_length=64),
    session: AsyncSession = Depends(get_session),
):
    countries = await settings_service.get_value(session, "host.supported_countries")
    return {"enabled": service.enabled(), "suggestions": await service.autocomplete(q.strip(), kind=kind, session_token=token, countries=countries)}


@router.get("/details/{place_id}", dependencies=[Depends(rate_limit("places_details", 60, 60))])
async def details(place_id: str, token: str | None = Query(None, max_length=64)):
    if not PLACE_ID.match(place_id):
        raise AppError(422, "INVALID_PLACE", "Invalid place.")
    return await service.details(place_id, session_token=token)
