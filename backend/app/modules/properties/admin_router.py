import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_admin
from app.core.errors import AppError
from app.core.ratelimit import client_ip
from app.modules.audit import service as audit
from app.modules.hosts.models import HostProfile
from app.modules.notifications.service import kick_outbox
from app.modules.properties import service
from app.modules.properties.models import Amenity, ListingRevision, Property, PropertyType, Unit
from app.modules.properties.schemas import DecisionIn, NameIn, ReasonIn
from app.modules.users.models import User

router = APIRouter(prefix="/admin", tags=["admin-listings"], dependencies=[Depends(require_admin)])

DEFAULT_PROPERTY_TYPES = ["House", "Apartment / Flat", "Hotel", "Villa", "Resort", "Homestay", "Cabin"]
DEFAULT_AMENITIES = [
    "Wi-Fi", "Air conditioning", "Heating", "Kitchen", "Free parking", "Pool", "Hot tub", "TV", "Washing machine",
    "Breakfast included", "Pet friendly", "Balcony", "Workspace", "Smoke alarm", "First aid kit", "BBQ",
]


async def seed_catalogues(session: AsyncSession) -> int:
    created = 0
    for i, name in enumerate(DEFAULT_PROPERTY_TYPES):
        if (await session.execute(select(PropertyType).where(PropertyType.name == name))).scalar_one_or_none() is None:
            session.add(PropertyType(name=name, sort_order=i))
            created += 1
    for name in DEFAULT_AMENITIES:
        if (await session.execute(select(Amenity).where(Amenity.name == name))).scalar_one_or_none() is None:
            session.add(Amenity(name=name))
            created += 1
    await session.commit()
    return created


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise AppError(404, "NOT_FOUND") from None


# ---------- listing queue ----------

@router.get("/properties")
async def list_properties(
    status: str | None = None, q: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Property, HostProfile, User).join(HostProfile, HostProfile.id == Property.host_id).join(User, User.id == HostProfile.user_id)
    if status:
        stmt = stmt.where(Property.status == status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Property.name.ilike(like), User.email.ilike(like), Property.suburb.ilike(like)))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(
        stmt.order_by(Property.submitted_at.asc().nullslast(), Property.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        .execution_options(populate_existing=True)
    )).all()
    pending_units = dict((await session.execute(
        select(Unit.property_id, func.count()).where(Unit.status == "submitted").group_by(Unit.property_id)
    )).all())
    pending_revisions = dict((await session.execute(
        select(ListingRevision.entity_id, func.count()).where(ListingRevision.status == "pending", ListingRevision.entity_type == "property").group_by(ListingRevision.entity_id)
    )).all())
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [{
            "id": str(p.id), "name": p.name, "status": p.status, "suburb": p.suburb, "state": p.state,
            "host_email": u.email, "host_name": u.full_name, "units": len([x for x in p.units if x.status != "archived"]),
            "pending_units": pending_units.get(p.id, 0), "has_pending_revision": p.id in pending_revisions,
            "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
        } for p, _, u in rows],
    }


@router.get("/properties/{property_id}")
async def property_detail(property_id: str, session: AsyncSession = Depends(get_session)):
    prop = await service.load_property(session, _uuid(property_id))
    host = await session.get(HostProfile, prop.host_id)
    owner = await session.get(User, host.user_id)
    data = await service.serialize_property(session, prop, with_checklist=False)
    revisions = (await session.execute(
        select(ListingRevision).where(ListingRevision.entity_id.in_([prop.id, *[u.id for u in prop.units]]))
        .order_by(ListingRevision.submitted_at.desc()).limit(30)
    )).scalars().all()
    from app.modules.hosts import service as host_service

    return {**data, "host": {"id": str(host.id), "name": owner.full_name, "email": owner.email, "state": host_service.host_state(host)},
            "revisions": [{
                "id": str(r.id), "entity_type": r.entity_type, "entity_id": str(r.entity_id), "data": r.data, "photos_changed": r.photos_changed,
                "status": r.status, "note": r.note, "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            } for r in revisions]}


@router.post("/properties/{property_id}/decision")
async def decide_property(property_id: str, body: DecisionIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    prop = await service.load_property(session, _uuid(property_id))
    await service.decide_property(session, prop, decision=body.decision, note=body.note, admin=admin, ip=client_ip(request))
    await session.commit()
    kick_outbox()
    return await service.serialize_property(session, await service.load_property(session, prop.id))


@router.post("/units/{unit_id}/decision")
async def decide_unit(unit_id: str, body: DecisionIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    unit = (await session.execute(select(Unit).where(Unit.id == _uuid(unit_id)).execution_options(populate_existing=True))).scalar_one_or_none()
    if unit is None:
        raise AppError(404, "NOT_FOUND", "Unit not found")
    prop = await service.load_property(session, unit.property_id)
    await service.decide_unit(session, unit, prop, decision=body.decision, note=body.note, admin=admin, ip=client_ip(request))
    await session.commit()
    kick_outbox()
    return await service.serialize_property(session, await service.load_property(session, prop.id))


@router.get("/revisions")
async def list_revisions(status: str = "pending", session: AsyncSession = Depends(get_session)):
    revs = (await session.execute(
        select(ListingRevision).where(ListingRevision.status == status).order_by(ListingRevision.submitted_at.asc()).limit(200)
    )).scalars().all()
    items = []
    for r in revs:
        if r.entity_type == "property":
            prop = await service.load_property(session, r.entity_id)
            label = prop.name
        else:
            unit = await session.get(Unit, r.entity_id)
            prop = await service.load_property(session, unit.property_id)
            label = f"{prop.name}: {unit.name}"
        items.append({"id": str(r.id), "entity_type": r.entity_type, "label": label, "property_id": str(prop.id),
                      "fields": sorted(r.data or {}), "photos_changed": r.photos_changed,
                      "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None})
    return items


@router.post("/revisions/{revision_id}/decision")
async def decide_revision(revision_id: str, body: DecisionIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rev = await session.get(ListingRevision, _uuid(revision_id))
    if rev is None:
        raise AppError(404, "NOT_FOUND", "Change not found")
    await service.decide_revision(session, rev, decision=body.decision, note=body.note, admin=admin, ip=client_ip(request))
    await session.commit()
    kick_outbox()
    return {"ok": True, "status": rev.status}


@router.post("/properties/{property_id}/unpublish")
async def unpublish(property_id: str, body: ReasonIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    prop = await service.load_property(session, _uuid(property_id))
    await service.set_unpublished(session, prop, unpublish=True, reason=body.reason, admin=admin, ip=client_ip(request))
    await session.commit()
    return await service.serialize_property(session, await service.load_property(session, prop.id))


@router.post("/properties/{property_id}/republish")
async def republish(property_id: str, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    prop = await service.load_property(session, _uuid(property_id))
    await service.set_unpublished(session, prop, unpublish=False, reason=None, admin=admin, ip=client_ip(request))
    await session.commit()
    return await service.serialize_property(session, await service.load_property(session, prop.id))


# ---------- catalogues ----------

@router.get("/catalogues")
async def catalogues(session: AsyncSession = Depends(get_session)):
    types = (await session.execute(select(PropertyType).order_by(PropertyType.sort_order, PropertyType.name))).scalars()
    amenities = (await session.execute(select(Amenity).order_by(Amenity.name))).scalars()
    return {
        "property_types": [{"id": str(t.id), "name": t.name, "active": t.active} for t in types],
        "amenities": [{"id": str(a.id), "name": a.name, "active": a.active} for a in amenities],
    }


async def _upsert(session: AsyncSession, model, kind: str, item_id: str | None, body: NameIn, admin: User, ip: str | None) -> dict:
    name = body.name.strip()
    if item_id is None:
        item = model(name=name, active=body.active)
        session.add(item)
        action, before = f"{kind}.created", None
    else:
        item = await session.get(model, _uuid(item_id))
        if item is None:
            raise AppError(404, "NOT_FOUND")
        before = {"name": item.name, "active": item.active}
        item.name, item.active = name, body.active
        action = f"{kind}.updated"
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise AppError(409, "DUPLICATE_NAME", "That name already exists.") from None
    audit.record(session, action=action, entity_type=kind, entity_id=item.id, actor_id=admin.id, before=before,
                 after={"name": name, "active": body.active}, ip=ip)
    await session.commit()
    return {"id": str(item.id), "name": item.name, "active": item.active}


@router.post("/property-types", status_code=201)
async def create_type(body: NameIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await _upsert(session, PropertyType, "property_type", None, body, admin, client_ip(request))


@router.put("/property-types/{item_id}")
async def update_type(item_id: str, body: NameIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await _upsert(session, PropertyType, "property_type", item_id, body, admin, client_ip(request))


@router.post("/amenities", status_code=201)
async def create_amenity(body: NameIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await _upsert(session, Amenity, "amenity", None, body, admin, client_ip(request))


@router.put("/amenities/{item_id}")
async def update_amenity(item_id: str, body: NameIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await _upsert(session, Amenity, "amenity", item_id, body, admin, client_ip(request))
