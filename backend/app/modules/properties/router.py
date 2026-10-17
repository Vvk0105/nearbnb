import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user
from app.core.errors import AppError
from app.core.ratelimit import rate_limit
from app.modules.hosts import service as host_service
from app.modules.properties import service
from app.modules.properties.models import Amenity, PropertyType
from app.modules.properties.schemas import BlockIn, OrderIn, PropertyIn, RoomIn, UnitIn
from app.modules.users.models import User

router = APIRouter(tags=["listings"])
MAX_UPLOAD = 30 * 1024 * 1024


async def _host(session: AsyncSession, user: User):
    return await host_service.require_host(session, user)


# ---------- public ----------

@router.get("/meta/listing")
async def listing_meta(session: AsyncSession = Depends(get_session)):
    types = (await session.execute(select(PropertyType).where(PropertyType.active.is_(True)).order_by(PropertyType.sort_order, PropertyType.name))).scalars()
    amenities = (await session.execute(select(Amenity).where(Amenity.active.is_(True)).order_by(Amenity.name))).scalars()
    return {
        "property_types": [{"id": str(t.id), "name": t.name} for t in types],
        "amenities": [{"id": str(a.id), "name": a.name} for a in amenities],
    }


@router.get("/properties/{property_id}")
async def public_property(property_id: str, session: AsyncSession = Depends(get_session)):
    try:
        pid = uuid.UUID(property_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Property not found") from None
    return await service.public_property(session, pid)


# ---------- host: properties ----------

@router.get("/host/properties")
async def my_properties(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    from app.modules.properties.models import Property

    props = (await session.execute(select(Property).where(Property.host_id == host.id).order_by(Property.created_at.desc()).execution_options(populate_existing=True))).scalars().all()
    return [await service.serialize_property(session, p) for p in props if p.status != "archived"]


@router.post("/host/properties", status_code=201)
async def create_property(body: PropertyIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    prop = await service.create_property(session, host, body.model_dump(exclude_unset=True, exclude_none=True), user)
    await session.commit()
    return await service.serialize_property(session, await service.load_property(session, prop.id), with_checklist=True)


@router.get("/host/properties/{property_id}")
async def get_property(property_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    prop = await service.owned_property(session, host, property_id)
    return await service.serialize_property(session, prop, with_checklist=True)


@router.patch("/host/properties/{property_id}")
async def edit_property(property_id: str, body: PropertyIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    prop = await service.owned_property(session, host, property_id)
    result = await service.edit_property(session, host, prop, body.model_dump(exclude_unset=True), user)
    await session.commit()
    data = await service.serialize_property(session, await service.load_property(session, prop.id), with_checklist=True)
    return {**data, "result": result}


@router.post("/host/properties/{property_id}/submit")
async def submit_property(property_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    prop = await service.owned_property(session, host, property_id)
    await service.submit_property(session, host, prop, user)
    await session.commit()
    return await service.serialize_property(session, await service.load_property(session, prop.id), with_checklist=True)


async def _lifecycle(property_id: str, action: str, user: User, session: AsyncSession) -> dict:
    host = await _host(session, user)
    prop = await service.owned_property(session, host, property_id)
    service.require_not_suspended(host)
    if action == "archive":
        await service.archive(session, prop, "property", user)
    else:
        await service.set_paused(session, prop, "property", pause=(action == "pause"), actor=user)
    await session.commit()
    return await service.serialize_property(session, await service.load_property(session, prop.id), with_checklist=True)


@router.post("/host/properties/{property_id}/pause")
async def pause_property(property_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await _lifecycle(property_id, "pause", user, session)


@router.post("/host/properties/{property_id}/resume")
async def resume_property(property_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await _lifecycle(property_id, "resume", user, session)


@router.post("/host/properties/{property_id}/archive")
async def archive_property(property_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await _lifecycle(property_id, "archive", user, session)


@router.post("/host/properties/{property_id}/photos", status_code=201, dependencies=[Depends(rate_limit("photos", 120, 3600))])
async def add_property_photo(property_id: str, file: UploadFile = File(...), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    prop = await service.owned_property(session, host, property_id)
    await service.add_photo(session, host, "property", prop, await file.read(MAX_UPLOAD))
    await session.commit()
    return await service.serialize_property(session, await service.load_property(session, prop.id), with_checklist=True)


@router.delete("/host/properties/{property_id}/photos/{media_id}")
async def delete_property_photo(property_id: str, media_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    prop = await service.owned_property(session, host, property_id)
    await service.delete_photo(session, host, "property", prop, media_id)
    await session.commit()
    return await service.serialize_property(session, await service.load_property(session, prop.id), with_checklist=True)


@router.put("/host/properties/{property_id}/photos/order")
async def reorder_property_photos(property_id: str, body: OrderIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    prop = await service.owned_property(session, host, property_id)
    await service.reorder_photos(session, "property", prop, body.ids)
    await session.commit()
    return await service.serialize_property(session, await service.load_property(session, prop.id), with_checklist=True)


# ---------- host: units ----------

async def _unit_response(session: AsyncSession, prop_id: uuid.UUID) -> dict:
    return await service.serialize_property(session, await service.load_property(session, prop_id), with_checklist=True)


@router.post("/host/properties/{property_id}/units", status_code=201)
async def create_unit(property_id: str, body: UnitIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    prop = await service.owned_property(session, host, property_id)
    await service.create_unit(session, host, prop, body.model_dump(exclude_unset=True, exclude_none=True), user)
    await session.commit()
    return await _unit_response(session, prop.id)


@router.patch("/host/units/{unit_id}")
async def edit_unit(unit_id: str, body: UnitIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    unit, prop = await service.owned_unit(session, host, unit_id)
    result = await service.edit_unit(session, host, unit, body.model_dump(exclude_unset=True), user)
    await session.commit()
    return {**await _unit_response(session, prop.id), "result": result}


@router.post("/host/units/{unit_id}/duplicate", status_code=201)
async def duplicate_unit(unit_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    unit, prop = await service.owned_unit(session, host, unit_id)
    await service.duplicate_unit(session, host, unit, prop, user)
    await session.commit()
    return await _unit_response(session, prop.id)


@router.post("/host/units/{unit_id}/submit")
async def submit_unit(unit_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    unit, prop = await service.owned_unit(session, host, unit_id)
    await service.submit_unit(session, host, unit, prop, user)
    await session.commit()
    return await _unit_response(session, prop.id)


async def _unit_lifecycle(unit_id: str, action: str, user: User, session: AsyncSession) -> dict:
    host = await _host(session, user)
    unit, prop = await service.owned_unit(session, host, unit_id)
    service.require_not_suspended(host)
    if action == "archive":
        await service.archive(session, unit, "unit", user)
    else:
        await service.set_paused(session, unit, "unit", pause=(action == "pause"), actor=user)
    await session.commit()
    return await _unit_response(session, prop.id)


@router.post("/host/units/{unit_id}/pause")
async def pause_unit(unit_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await _unit_lifecycle(unit_id, "pause", user, session)


@router.post("/host/units/{unit_id}/resume")
async def resume_unit(unit_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await _unit_lifecycle(unit_id, "resume", user, session)


@router.post("/host/units/{unit_id}/archive")
async def archive_unit(unit_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await _unit_lifecycle(unit_id, "archive", user, session)


@router.patch("/host/units/{unit_id}/rooms/{room_id}")
async def rename_room(unit_id: str, room_id: str, body: RoomIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    unit, prop = await service.owned_unit(session, host, unit_id)
    await service.rename_room(session, unit, room_id, body.label)
    await session.commit()
    return await _unit_response(session, prop.id)


@router.post("/host/units/{unit_id}/photos", status_code=201, dependencies=[Depends(rate_limit("photos", 120, 3600))])
async def add_unit_photo(unit_id: str, file: UploadFile = File(...), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    unit, prop = await service.owned_unit(session, host, unit_id)
    await service.add_photo(session, host, "unit", unit, await file.read(MAX_UPLOAD))
    await session.commit()
    return await _unit_response(session, prop.id)


@router.delete("/host/units/{unit_id}/photos/{media_id}")
async def delete_unit_photo(unit_id: str, media_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    unit, prop = await service.owned_unit(session, host, unit_id)
    await service.delete_photo(session, host, "unit", unit, media_id)
    await session.commit()
    return await _unit_response(session, prop.id)


@router.put("/host/units/{unit_id}/photos/order")
async def reorder_unit_photos(unit_id: str, body: OrderIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    unit, prop = await service.owned_unit(session, host, unit_id)
    await service.reorder_photos(session, "unit", unit, body.ids)
    await session.commit()
    return await _unit_response(session, prop.id)


# ---------- host: availability blocks ----------

@router.get("/host/units/{unit_id}/calendar")
async def unit_calendar(
    unit_id: str, date_from: str = Query(..., alias="from"), date_to: str = Query(..., alias="to"),
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
):
    host = await _host(session, user)
    unit, prop = await service.owned_unit(session, host, unit_id)
    return await service.calendar(session, unit, prop, date_from, date_to)


@router.post("/host/units/{unit_id}/blocks", status_code=201)
async def create_block(unit_id: str, body: BlockIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    unit, prop = await service.owned_unit(session, host, unit_id)
    created = await service.create_block(session, host, unit, prop, body, user)
    await session.commit()
    return {"blocks": created}


@router.delete("/host/units/{unit_id}/blocks/{occupancy_id}")
async def delete_block(unit_id: str, occupancy_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await _host(session, user)
    unit, prop = await service.owned_unit(session, host, unit_id)
    await service.delete_block(session, unit, prop, occupancy_id, user)
    await session.commit()
    return {"ok": True}
