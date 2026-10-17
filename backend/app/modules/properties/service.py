import asyncio
import uuid
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.locks import lock_unit
from app.core.images import process_photo
from app.core.storage import get_storage
from app.modules.audit import service as audit
from app.modules.hosts import service as host_service
from app.modules.hosts.models import HostProfile
from app.modules.notifications.service import enqueue_email
from app.modules.properties.models import (
    EDITABLE_STATUSES, LIVE_LIFECYCLE, ST_APPROVED, ST_ARCHIVED, ST_CHANGES, ST_DRAFT, ST_PAUSED, ST_REJECTED,
    ST_SUBMITTED, ST_UNPUBLISHED, Amenity, ListingRevision, Media, Property, PropertyType, Room, RoomOccupancy, Unit,
)
from app.modules.settings import service as settings_service
from app.modules.users.models import User

# Which edited columns belong to which admin-configurable "re-approval" group.
PROPERTY_GROUPS = {
    "address": ["address_line1", "address_line2", "suburb", "state", "postcode", "country", "latitude", "longitude", "timezone"],
    "property_type": ["type_id"],
    "description": ["description"],
    "name": ["name"],
    "rules": ["rules"],
    "cancellation_policy": ["cancellation_template_key"],
}
UNIT_GROUPS = {
    "name": ["name"],
    "description": ["description"],
    "price": ["price_per_24h", "cleaning_fee"],
    "capacity": ["capacity", "quantity", "bedrooms", "bathrooms", "beds"],
    "amenities": ["amenity_ids"],
}
BLOCKING_KINDS = ("booking", "hold", "external_block")  # a room with these in the future cannot be removed

AU_TIMEZONES = {
    "NSW": "Australia/Sydney", "ACT": "Australia/Sydney", "VIC": "Australia/Melbourne", "TAS": "Australia/Hobart",
    "QLD": "Australia/Brisbane", "SA": "Australia/Adelaide", "WA": "Australia/Perth", "NT": "Australia/Darwin",
}


def media_url(path: str) -> str:
    return f"/api/v1/media/{path}"


# ---------- small helpers ----------

def default_timezone(country: str | None, state: str | None) -> str:
    if (country or "AU").upper() == "AU":
        return AU_TIMEZONES.get((state or "").strip().upper(), "Australia/Sydney")
    return "UTC"


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise AppError(422, "INVALID_TIMEZONE", f"Unknown timezone '{name}'.") from None
    return name


def _jsonable(changes: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in changes.items():
        if isinstance(value, uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, time):
            out[key] = value.isoformat()
        elif isinstance(value, list):
            out[key] = [str(v) if isinstance(v, uuid.UUID) else v for v in value]
        else:
            out[key] = value
    return out


def _split(changes: dict[str, Any], groups: dict[str, list[str]], reapproval: list[str]) -> tuple[dict, dict]:
    """Returns (apply_now, needs_review)."""
    lookup = {col: group for group, cols in groups.items() for col in cols}
    now: dict[str, Any] = {}
    review: dict[str, Any] = {}
    for key, value in changes.items():
        (review if lookup.get(key) in reapproval else now)[key] = value
    return now, review


def _clean_text_fields(changes: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in changes.items()}


# ---------- loading ----------

async def load_property(session: AsyncSession, property_id: uuid.UUID) -> Property:
    prop = (await session.execute(select(Property).where(Property.id == property_id).execution_options(populate_existing=True))).scalar_one_or_none()
    if prop is None:
        raise AppError(404, "NOT_FOUND", "Property not found")
    return prop


async def owned_property(session: AsyncSession, host: HostProfile, property_id: str) -> Property:
    try:
        pid = uuid.UUID(property_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Property not found") from None
    prop = await load_property(session, pid)
    if prop.host_id != host.id:
        raise AppError(404, "NOT_FOUND", "Property not found")
    return prop


async def owned_unit(session: AsyncSession, host: HostProfile, unit_id: str) -> tuple[Unit, Property]:
    try:
        uid = uuid.UUID(unit_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Unit not found") from None
    unit = (await session.execute(select(Unit).where(Unit.id == uid).execution_options(populate_existing=True))).scalar_one_or_none()
    if unit is None:
        raise AppError(404, "NOT_FOUND", "Unit not found")
    prop = await load_property(session, unit.property_id)
    if prop.host_id != host.id:
        raise AppError(404, "NOT_FOUND", "Unit not found")
    return unit, prop


def require_can_create(host: HostProfile) -> None:
    if not host_service.host_state(host) in ("approved", "active", "restricted"):
        raise AppError(403, "HOST_NOT_APPROVED", "Your host application must be approved before you can add properties.")


def require_not_suspended(host: HostProfile) -> None:
    if host.suspended_at:
        raise AppError(403, "HOST_SUSPENDED", "Your host account is suspended.")


async def media_for(session: AsyncSession, owner_type: str, owner_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Media]]:
    if not owner_ids:
        return {}
    rows = (await session.execute(
        select(Media).where(Media.owner_type == owner_type, Media.owner_id.in_(owner_ids)).order_by(Media.position, Media.created_at)
    )).scalars().all()
    grouped: dict[uuid.UUID, list[Media]] = {}
    for m in rows:
        grouped.setdefault(m.owner_id, []).append(m)
    return grouped


PUBLIC_MEDIA = ("approved", "pending_delete")  # a photo awaiting deletion stays visible until an admin approves


def _media_json(m: Media) -> dict:
    return {"id": str(m.id), "url": media_url(m.path), "thumb_url": media_url(m.thumb_path), "position": m.position, "status": m.status}


# ---------- revisions ----------

async def pending_revision(session: AsyncSession, entity_type: str, entity_id: uuid.UUID, *, create: bool = False) -> ListingRevision | None:
    rev = (await session.execute(
        select(ListingRevision).where(ListingRevision.entity_type == entity_type, ListingRevision.entity_id == entity_id,
                                      ListingRevision.status == "pending")
    )).scalar_one_or_none()
    if rev is None and create:
        rev = ListingRevision(entity_type=entity_type, entity_id=entity_id, data={}, photos_changed=False, status="pending")
        session.add(rev)
        await session.flush()
    return rev


async def _queue_revision(session: AsyncSession, entity_type: str, entity_id: uuid.UUID, changes: dict[str, Any]) -> None:
    rev = await pending_revision(session, entity_type, entity_id, create=True)
    rev.data = {**(rev.data or {}), **_jsonable(changes)}
    rev.submitted_at = datetime.now(UTC)


# ---------- property editing ----------

async def _validate_property_values(session: AsyncSession, changes: dict[str, Any]) -> dict[str, Any]:
    changes = _clean_text_fields(changes)
    if "type_id" in changes and changes["type_id"] is not None:
        ptype = await session.get(PropertyType, uuid.UUID(str(changes["type_id"])))
        if ptype is None or not ptype.active:
            raise AppError(422, "INVALID_PROPERTY_TYPE", "Choose a valid property type.")
        changes["type_id"] = ptype.id
    if "country" in changes and changes["country"]:
        changes["country"] = changes["country"].upper()
    if "timezone" in changes and changes["timezone"]:
        validate_timezone(changes["timezone"])
    return changes


def _apply_property_values(prop: Property, changes: dict[str, Any]) -> None:
    changes = dict(changes)
    if "type_id" in changes and isinstance(changes["type_id"], str):
        changes["type_id"] = uuid.UUID(changes["type_id"])
    for key, value in changes.items():
        setattr(prop, key, value)


def _status_gate(entity_status: str) -> str:
    """Returns 'direct' (edit freely) or 'live' (route sensitive edits through review); raises if locked."""
    if entity_status in EDITABLE_STATUSES:
        return "direct"
    if entity_status == ST_SUBMITTED:
        raise AppError(409, "UNDER_REVIEW", "This is being reviewed. You can edit it again after the decision.")
    if entity_status in LIVE_LIFECYCLE:
        return "live"
    raise AppError(409, "ARCHIVED", "Archived items can't be edited.")


async def create_property(session: AsyncSession, host: HostProfile, changes: dict[str, Any], actor: User) -> Property:
    require_can_create(host)
    changes = await _validate_property_values(session, changes)
    if not changes.get("name"):
        raise AppError(422, "NAME_REQUIRED", "Give the property a name.")
    prop = Property(host_id=host.id, units=[])
    _apply_property_values(prop, changes)
    if "timezone" not in changes:
        prop.timezone = default_timezone(prop.country, prop.state)
    session.add(prop)
    await session.flush()
    audit.record(session, action="property.created", entity_type="property", entity_id=prop.id, actor_id=actor.id,
                 after={"name": prop.name})
    return prop


async def edit_property(session: AsyncSession, host: HostProfile, prop: Property, changes: dict[str, Any], actor: User) -> dict[str, list[str]]:
    require_not_suspended(host)
    mode = _status_gate(prop.status)
    changes = await _validate_property_values(session, changes)
    # Forms send every field on save. Only fields whose value really changed count as an edit,
    # otherwise an untouched description would be sent to admin review.
    changes = {k: v for k, v in changes.items() if getattr(prop, k) != v}
    if ("state" in changes or "country" in changes) and "timezone" not in changes:
        derived = default_timezone(changes.get("country", prop.country), changes.get("state", prop.state))
        if derived != prop.timezone:
            changes["timezone"] = derived
    if not changes:
        return {"applied": [], "pending_review": []}
    if mode == "direct":
        _apply_property_values(prop, changes)
        return {"applied": sorted(changes), "pending_review": []}
    reapproval = await settings_service.get_value(session, "listing.reapproval_fields")
    now, review = _split(changes, PROPERTY_GROUPS, reapproval)
    _apply_property_values(prop, now)
    if review:
        await _queue_revision(session, "property", prop.id, review)
        audit.record(session, action="property.revision_queued", entity_type="property", entity_id=prop.id, actor_id=actor.id,
                     after=_jsonable(review))
    return {"applied": sorted(now), "pending_review": sorted(review)}


# ---------- units and rooms ----------

async def _validate_unit_values(session: AsyncSession, changes: dict[str, Any]) -> dict[str, Any]:
    changes = _clean_text_fields(changes)
    if changes.get("max_stay_hours") is not None and changes["max_stay_hours"] % 24 != 0:
        raise AppError(422, "INVALID_MAX_STAY", "Maximum stay must be a multiple of 24 hours.")
    if "amenity_ids" in changes and changes["amenity_ids"] is not None:
        ids = [uuid.UUID(str(i)) for i in changes["amenity_ids"]]
        found = (await session.execute(select(Amenity).where(Amenity.id.in_(ids), Amenity.active.is_(True)))).scalars().all()
        if len(found) != len(set(ids)):
            raise AppError(422, "INVALID_AMENITY", "One or more amenities are not available.")
        changes["amenity_ids"] = [a.id for a in found]
        changes["_amenities"] = found
    return changes


def _check_window(unit: Unit, changes: dict[str, Any]) -> None:
    start = changes.get("checkin_from", unit.checkin_from)
    end = changes.get("checkin_to", unit.checkin_to)
    start = time.fromisoformat(start) if isinstance(start, str) else start
    end = time.fromisoformat(end) if isinstance(end, str) else end
    if start >= end:
        raise AppError(422, "INVALID_CHECKIN_WINDOW", "Check-in 'from' time must be earlier than 'to'.")


async def _sync_rooms(session: AsyncSession, unit: Unit, quantity: int) -> None:
    active = [r for r in unit.rooms if r.active]
    if quantity > len(active):
        next_n = len(unit.rooms) + 1
        for i in range(quantity - len(active)):
            unit.rooms.append(Room(label=str(next_n + i), active=True))
    elif quantity < len(active):
        need = len(active) - quantity
        busy_ids = set()
        if active and unit.id is not None:
            busy_ids = set((await session.execute(
                select(RoomOccupancy.room_id).where(
                    RoomOccupancy.room_id.in_([r.id for r in active]),
                    RoomOccupancy.kind.in_(BLOCKING_KINDS),
                    func.upper(RoomOccupancy.period) > datetime.now(UTC),
                )
            )).scalars())
        removable = [r for r in reversed(active) if r.id not in busy_ids][:need]
        if len(removable) < need:
            raise AppError(409, "ROOMS_IN_USE", "Some rooms have upcoming bookings, so the quantity can't be reduced that far.")
        for room in removable:
            await session.execute(delete(RoomOccupancy).where(RoomOccupancy.room_id == room.id, RoomOccupancy.kind == "host_block"))
            room.active = False


async def _apply_unit_values(session: AsyncSession, unit: Unit, changes: dict[str, Any]) -> None:
    changes = dict(changes)
    amenities = changes.pop("_amenities", None)
    ids = changes.pop("amenity_ids", None)
    if ids is not None and amenities is None:  # applying a stored revision: ids are strings
        parsed = [uuid.UUID(str(i)) for i in ids]
        amenities = list((await session.execute(select(Amenity).where(Amenity.id.in_(parsed)))).scalars())
    quantity = changes.pop("quantity", None)
    for key in ("checkin_from", "checkin_to"):
        if isinstance(changes.get(key), str):
            changes[key] = time.fromisoformat(changes[key])
    for key, value in changes.items():
        setattr(unit, key, value)
    if amenities is not None:
        unit.amenities = list(amenities)
    if quantity is not None:
        unit.quantity = quantity
        await _sync_rooms(session, unit, quantity)


async def create_unit(session: AsyncSession, host: HostProfile, prop: Property, changes: dict[str, Any], actor: User) -> Unit:
    require_can_create(host)
    if prop.status == ST_SUBMITTED:
        raise AppError(409, "UNDER_REVIEW", "You can add units after the review is finished.")
    if prop.status == ST_ARCHIVED:
        raise AppError(409, "ARCHIVED", "Archived properties can't be edited.")
    changes = await _validate_unit_values(session, changes)
    if not changes.get("name"):
        raise AppError(422, "NAME_REQUIRED", "Give the unit a name.")
    if "checkin_from" in changes or "checkin_to" in changes:
        _check_window(Unit(checkin_from=time(8, 0), checkin_to=time(22, 0)), changes)
    unit = Unit(property_id=prop.id, rooms=[], amenities=[])
    quantity = changes.get("quantity", 1)
    unit.quantity = 1
    await _apply_unit_values(session, unit, {k: v for k, v in changes.items() if k != "quantity"})
    unit.quantity = quantity
    for i in range(quantity):
        unit.rooms.append(Room(label=str(i + 1), active=True))
    session.add(unit)
    await session.flush()
    audit.record(session, action="unit.created", entity_type="unit", entity_id=unit.id, actor_id=actor.id,
                 after={"name": unit.name, "property_id": str(prop.id)})
    return unit


async def duplicate_unit(session: AsyncSession, host: HostProfile, unit: Unit, prop: Property, actor: User) -> Unit:
    changes = {
        "name": f"{unit.name} (copy)"[:200], "description": unit.description, "price_per_24h": unit.price_per_24h,
        "cleaning_fee": unit.cleaning_fee, "capacity": unit.capacity, "quantity": unit.quantity, "bedrooms": unit.bedrooms,
        "bathrooms": unit.bathrooms, "beds": unit.beds, "max_stay_hours": unit.max_stay_hours,
        "checkin_from": unit.checkin_from, "checkin_to": unit.checkin_to, "amenity_ids": [a.id for a in unit.amenities],
    }
    return await create_unit(session, host, prop, {k: v for k, v in changes.items() if v is not None}, actor)


async def edit_unit(session: AsyncSession, host: HostProfile, unit: Unit, changes: dict[str, Any], actor: User) -> dict[str, list[str]]:
    require_not_suspended(host)
    mode = _status_gate(unit.status)
    changes = await _validate_unit_values(session, changes)
    if "checkin_from" in changes or "checkin_to" in changes:
        _check_window(unit, changes)
    # Only fields whose value really changed count as an edit (see edit_property).
    unchanged = set()
    for key, value in changes.items():
        if key == "amenity_ids":
            if {a.id for a in unit.amenities} == set(value):
                unchanged.update({"amenity_ids", "_amenities"})
        elif key != "_amenities" and getattr(unit, key) == value:
            unchanged.add(key)
    changes = {k: v for k, v in changes.items() if k not in unchanged}
    if not changes:
        return {"applied": [], "pending_review": []}
    if mode == "direct":
        await _apply_unit_values(session, unit, changes)
        return {"applied": sorted(k for k in changes if not k.startswith("_")), "pending_review": []}
    reapproval = await settings_service.get_value(session, "listing.reapproval_fields")
    now, review = _split({k: v for k, v in changes.items() if k != "_amenities"}, UNIT_GROUPS, reapproval)
    if "amenity_ids" in now:
        now["_amenities"] = changes["_amenities"]
    await _apply_unit_values(session, unit, now)
    if review:
        await _queue_revision(session, "unit", unit.id, review)
        audit.record(session, action="unit.revision_queued", entity_type="unit", entity_id=unit.id, actor_id=actor.id,
                     after=_jsonable(review))
    return {"applied": sorted(k for k in now if not k.startswith("_")), "pending_review": sorted(review)}


async def rename_room(session: AsyncSession, unit: Unit, room_id: str, label: str) -> None:
    room = next((r for r in unit.rooms if str(r.id) == room_id), None)
    if room is None:
        raise AppError(404, "NOT_FOUND", "Room not found")
    if any(r.label == label.strip() and r.id != room.id and r.active for r in unit.rooms):
        raise AppError(409, "DUPLICATE_LABEL", "Another room already has that name.")
    room.label = label.strip()


# ---------- photos ----------

async def _photo_needs_review(session: AsyncSession, status: str) -> bool:
    if status not in LIVE_LIFECYCLE:
        return False
    return "photos" in await settings_service.get_value(session, "listing.reapproval_fields")


async def add_photo(session: AsyncSession, host: HostProfile, owner_type: str, owner: Property | Unit, data: bytes) -> Media:
    require_not_suspended(host)
    _status_gate(owner.status)
    max_mb = await settings_service.get_value(session, "listing.max_photo_mb")
    max_count = await settings_service.get_value(session, "listing.max_photos")
    existing = (await media_for(session, owner_type, [owner.id])).get(owner.id, [])
    if len([m for m in existing if m.status != "pending_delete"]) >= max_count:
        raise AppError(422, "TOO_MANY_PHOTOS", f"A listing can have at most {max_count} photos.")
    full, thumb = await asyncio.to_thread(process_photo, data, max_bytes=max_mb * 1024 * 1024)
    name = uuid.uuid4().hex
    base = f"public/listings/{owner_type}/{owner.id}"
    storage = get_storage()
    storage.save(f"{base}/{name}.webp", full)
    storage.save(f"{base}/{name}_thumb.webp", thumb)
    review = await _photo_needs_review(session, owner.status)
    media = Media(owner_type=owner_type, owner_id=owner.id, path=f"{base}/{name}.webp", thumb_path=f"{base}/{name}_thumb.webp",
                  position=(max((m.position for m in existing), default=-1) + 1), status="pending_add" if review else "approved")
    session.add(media)
    if review:
        (await pending_revision(session, owner_type, owner.id, create=True)).photos_changed = True
    await session.flush()
    return media


def _delete_files(media: Media) -> None:
    storage = get_storage()
    storage.delete(media.path)
    storage.delete(media.thumb_path)


async def delete_photo(session: AsyncSession, host: HostProfile, owner_type: str, owner: Property | Unit, media_id: str) -> None:
    require_not_suspended(host)
    _status_gate(owner.status)
    media = await session.get(Media, uuid.UUID(media_id)) if _is_uuid(media_id) else None
    if media is None or media.owner_id != owner.id or media.owner_type != owner_type:
        raise AppError(404, "NOT_FOUND", "Photo not found")
    if media.status == "approved" and await _photo_needs_review(session, owner.status):
        media.status = "pending_delete"
        (await pending_revision(session, owner_type, owner.id, create=True)).photos_changed = True
        return
    _delete_files(media)
    await session.delete(media)


async def reorder_photos(session: AsyncSession, owner_type: str, owner: Property | Unit, ids: list[uuid.UUID]) -> None:
    existing = (await media_for(session, owner_type, [owner.id])).get(owner.id, [])
    by_id = {m.id: m for m in existing}
    if set(ids) != set(by_id):
        raise AppError(422, "INVALID_ORDER", "Send every photo id exactly once.")
    for position, media_id in enumerate(ids):
        by_id[media_id].position = position


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


# ---------- checklist and submission ----------

async def checklist(session: AsyncSession, prop: Property) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    min_photos = await settings_service.get_value(session, "listing.min_photos")
    min_unit_photos = await settings_service.get_value(session, "listing.min_unit_photos")

    def need(scope: str, item: str, message: str) -> None:
        missing.append({"scope": scope, "item": item, "message": message})

    for field, label in (("name", "a name"), ("description", "a description"), ("address_line1", "a street address"),
                         ("suburb", "a suburb/city"), ("state", "a state"), ("postcode", "a postcode")):
        if not (getattr(prop, field) or "").strip():
            need("property", field, f"Add {label}.")
    if prop.type_id is None:
        need("property", "type_id", "Choose a property type.")
    prop_media = (await media_for(session, "property", [prop.id])).get(prop.id, [])
    if len([m for m in prop_media if m.status != "pending_delete"]) < min_photos:
        need("property", "photos", f"Add at least {min_photos} property photos.")
    units = [u for u in prop.units if u.status not in (ST_ARCHIVED,)]
    if not units:
        need("property", "units", "Add at least one bookable unit (e.g. 'Entire house' or 'Standard room').")
    unit_media = await media_for(session, "unit", [u.id for u in units])
    for u in units:
        scope = f"unit:{u.id}"
        if u.price_per_24h <= 0:
            need(scope, "price", f"{u.name}: set a price per 24 hours.")
        if u.capacity < 1:
            need(scope, "capacity", f"{u.name}: set the guest capacity.")
        if len([m for m in unit_media.get(u.id, []) if m.status != "pending_delete"]) < min_unit_photos:
            need(scope, "photos", f"{u.name}: add at least {min_unit_photos} photo(s).")
    return missing


async def submit_property(session: AsyncSession, host: HostProfile, prop: Property, actor: User) -> None:
    if not host_service.is_active(host):
        raise AppError(403, "HOST_NOT_ACTIVE", "Finish payout setup to become an active host before submitting.")
    if prop.status not in EDITABLE_STATUSES:
        raise AppError(409, "NOT_SUBMITTABLE", "This property can't be submitted right now.")
    problems = await checklist(session, prop)
    if problems:
        raise AppError(422, "LISTING_INCOMPLETE", "Complete the checklist first: " + " ".join(p["message"] for p in problems))
    now = datetime.now(UTC)
    prop.status = ST_SUBMITTED
    prop.submitted_at = now
    prop.review_note = None
    for unit in prop.units:
        if unit.status in EDITABLE_STATUSES:
            unit.status = ST_SUBMITTED
            unit.submitted_at = now
            unit.review_note = None
    audit.record(session, action="property.submitted", entity_type="property", entity_id=prop.id, actor_id=actor.id)


async def submit_unit(session: AsyncSession, host: HostProfile, unit: Unit, prop: Property, actor: User) -> None:
    if not host_service.is_active(host):
        raise AppError(403, "HOST_NOT_ACTIVE", "Finish payout setup to become an active host before submitting.")
    if prop.status not in LIVE_LIFECYCLE:
        raise AppError(409, "PROPERTY_NOT_APPROVED", "Submit the whole property first; this unit goes with it.")
    if unit.status not in EDITABLE_STATUSES:
        raise AppError(409, "NOT_SUBMITTABLE", "This unit can't be submitted right now.")
    problems = [p for p in await checklist(session, prop) if p["scope"] == f"unit:{unit.id}"]
    if problems:
        raise AppError(422, "LISTING_INCOMPLETE", "Complete the checklist first: " + " ".join(p["message"] for p in problems))
    unit.status = ST_SUBMITTED
    unit.submitted_at = datetime.now(UTC)
    unit.review_note = None
    audit.record(session, action="unit.submitted", entity_type="unit", entity_id=unit.id, actor_id=actor.id)


# ---------- host lifecycle: pause / resume / archive ----------

async def set_paused(session: AsyncSession, entity: Property | Unit, entity_type: str, *, pause: bool, actor: User) -> None:
    if pause:
        if entity.status != ST_APPROVED:
            raise AppError(409, "NOT_LIVE", "Only live listings can be paused.")
        entity.status = ST_PAUSED
    else:
        if entity.status != ST_PAUSED:
            raise AppError(409, "NOT_PAUSED", "This listing is not paused.")
        entity.status = ST_APPROVED
    audit.record(session, action=f"{entity_type}.{'paused' if pause else 'resumed'}", entity_type=entity_type, entity_id=entity.id, actor_id=actor.id)


async def _has_blocking_future_occupancy(session: AsyncSession, unit_ids: list[uuid.UUID]) -> bool:
    count = (await session.execute(
        select(func.count()).select_from(RoomOccupancy).join(Room, Room.id == RoomOccupancy.room_id).where(
            Room.unit_id.in_(unit_ids), RoomOccupancy.kind.in_(BLOCKING_KINDS), func.upper(RoomOccupancy.period) > datetime.now(UTC))
    )).scalar_one()
    return count > 0


async def archive(session: AsyncSession, entity: Property | Unit, entity_type: str, actor: User) -> None:
    unit_ids = [u.id for u in entity.units] if entity_type == "property" else [entity.id]
    if await _has_blocking_future_occupancy(session, unit_ids):
        raise AppError(409, "HAS_UPCOMING_BOOKINGS", "This has upcoming bookings or holds, so it can't be archived yet.")
    if entity.status == ST_SUBMITTED:
        raise AppError(409, "UNDER_REVIEW", "This is being reviewed.")
    entity.status = ST_ARCHIVED
    if entity_type == "property":
        for unit in entity.units:
            unit.status = ST_ARCHIVED
    for unit_id in unit_ids:
        room_ids = select(Room.id).where(Room.unit_id == unit_id)
        await session.execute(delete(RoomOccupancy).where(RoomOccupancy.room_id.in_(room_ids), RoomOccupancy.kind == "host_block"))
    audit.record(session, action=f"{entity_type}.archived", entity_type=entity_type, entity_id=entity.id, actor_id=actor.id)


# ---------- availability blocks ----------

def _parse_local(value: str, tz: str) -> datetime:
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        raise AppError(422, "INVALID_DATETIME", "Use the format YYYY-MM-DDTHH:MM.") from None
    if naive.tzinfo is not None:
        raise AppError(422, "INVALID_DATETIME", "Send local time without a timezone offset.")
    return naive.replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _occ_json(occ: RoomOccupancy, room: Room, tz: str) -> dict:
    zone = ZoneInfo(tz)
    lower, upper = occ.period.lower, occ.period.upper
    return {
        "id": str(occ.id), "room_id": str(room.id), "room_label": room.label, "kind": occ.kind, "note": occ.note,
        "start": lower.isoformat(), "end": upper.isoformat(),
        "start_local": lower.astimezone(zone).strftime("%Y-%m-%dT%H:%M"), "end_local": upper.astimezone(zone).strftime("%Y-%m-%dT%H:%M"),
    }


async def create_block(session: AsyncSession, host: HostProfile, unit: Unit, prop: Property, body: Any, actor: User) -> list[dict]:
    require_not_suspended(host)
    if unit.status == ST_ARCHIVED:
        raise AppError(409, "ARCHIVED", "Archived units can't be edited.")
    interval = await settings_service.get_value(session, "checkin.interval_minutes")
    start = _parse_local(body.start_local, prop.timezone)
    end = _parse_local(body.end_local, prop.timezone)
    for label, local in (("start", body.start_local), ("end", body.end_local)):
        parsed = datetime.fromisoformat(local)
        if parsed.second or parsed.minute % interval:
            raise AppError(422, "INVALID_TIME_STEP", f"Block {label} time must fall on a {interval}-minute step.")
    if end <= start:
        raise AppError(422, "INVALID_RANGE", "The end must be after the start.")
    if end <= datetime.now(UTC):
        raise AppError(422, "IN_THE_PAST", "That period is already over.")
    if (end - start).days > 366:
        raise AppError(422, "RANGE_TOO_LONG", "A block can be at most one year.")

    active = [r for r in unit.rooms if r.active]
    if body.all_rooms:
        targets = active
    elif body.room_id is not None:
        targets = [r for r in active if r.id == body.room_id]
        if not targets:
            raise AppError(404, "NOT_FOUND", "Room not found")
    elif len(active) == 1:
        targets = active
    else:
        raise AppError(422, "ROOM_REQUIRED", "This unit has several rooms: choose a room or block all rooms.")

    created: list[tuple[RoomOccupancy, Room]] = []
    await lock_unit(session, unit.id)
    # An expired hold still sits in the table until the expiry job removes it, and the overlap constraint cannot tell.
    await session.execute(delete(RoomOccupancy).where(
        RoomOccupancy.room_id.in_([r.id for r in targets]), RoomOccupancy.kind == "hold", RoomOccupancy.expires_at <= datetime.now(UTC)))
    try:
        async with session.begin_nested():
            for room in targets:
                conflict = (await session.execute(
                    select(RoomOccupancy).where(RoomOccupancy.room_id == room.id, RoomOccupancy.period.overlaps(Range(start, end, bounds="[)"))).limit(1)
                )).scalar_one_or_none()
                if conflict is not None:
                    zone = ZoneInfo(prop.timezone)
                    what = {"booking": "a booking", "hold": "a pending booking request", "host_block": "an existing block",
                            "external_block": "an imported calendar block"}.get(conflict.kind, "another entry")
                    raise AppError(409, "SLOT_UNAVAILABLE",
                                   f"Room {room.label} already has {what} from "
                                   f"{conflict.period.lower.astimezone(zone):%d %b %H:%M} to {conflict.period.upper.astimezone(zone):%d %b %H:%M}.")
                occ = RoomOccupancy(room_id=room.id, period=Range(start, end, bounds="[)"), kind="host_block", note=body.note, created_by=actor.id)
                session.add(occ)
                created.append((occ, room))
            await session.flush()
    except IntegrityError:
        raise AppError(409, "SLOT_UNAVAILABLE", "That period was just taken. Please check the calendar and try again.") from None
    audit.record(session, action="unit.block_created", entity_type="unit", entity_id=unit.id, actor_id=actor.id,
                 after={"start": start.isoformat(), "end": end.isoformat(), "rooms": [r.label for _, r in created]})
    return [_occ_json(o, r, prop.timezone) for o, r in created]


async def delete_block(session: AsyncSession, unit: Unit, prop: Property, occupancy_id: str, actor: User) -> None:
    if not _is_uuid(occupancy_id):
        raise AppError(404, "NOT_FOUND")
    occ = await session.get(RoomOccupancy, uuid.UUID(occupancy_id))
    room_ids = {r.id for r in unit.rooms}
    if occ is None or occ.room_id not in room_ids:
        raise AppError(404, "NOT_FOUND", "Block not found")
    if occ.kind != "host_block":
        raise AppError(409, "NOT_A_BLOCK", "Only your own blocks can be removed here.")
    audit.record(session, action="unit.block_deleted", entity_type="unit", entity_id=unit.id, actor_id=actor.id,
                 before={"start": occ.period.lower.isoformat(), "end": occ.period.upper.isoformat()})
    await session.delete(occ)


async def calendar(session: AsyncSession, unit: Unit, prop: Property, date_from: str, date_to: str) -> dict:
    start = _parse_local(date_from + "T00:00", prop.timezone)
    end = _parse_local(date_to + "T00:00", prop.timezone)
    if end <= start or (end - start).days > 370:
        raise AppError(422, "INVALID_RANGE", "Choose a range of up to one year.")
    rooms = {r.id: r for r in unit.rooms if r.active}
    rows = (await session.execute(
        select(RoomOccupancy).where(RoomOccupancy.room_id.in_(list(rooms)), RoomOccupancy.period.overlaps(Range(start, end, bounds="[)")))
        .order_by(func.lower(RoomOccupancy.period))
    )).scalars().all() if rooms else []
    return {
        "timezone": prop.timezone,
        "rooms": [{"id": str(r.id), "label": r.label} for r in rooms.values()],
        "entries": [_occ_json(o, rooms[o.room_id], prop.timezone) for o in rows],
    }


# ---------- serialization ----------

def _unit_json(unit: Unit, media: list[Media], *, host_view: bool) -> dict:
    visible = [m for m in media if host_view or m.status in PUBLIC_MEDIA]
    return {
        "id": str(unit.id), "property_id": str(unit.property_id), "name": unit.name, "description": unit.description,
        "price_per_24h": unit.price_per_24h, "currency": unit.currency, "cleaning_fee": unit.cleaning_fee,
        "capacity": unit.capacity, "quantity": unit.quantity, "bedrooms": unit.bedrooms, "bathrooms": unit.bathrooms,
        "beds": unit.beds, "max_stay_hours": unit.max_stay_hours,
        "checkin_from": unit.checkin_from.strftime("%H:%M"), "checkin_to": unit.checkin_to.strftime("%H:%M"),
        "status": unit.status, "review_note": unit.review_note if host_view else None,
        "amenities": [{"id": str(a.id), "name": a.name} for a in unit.amenities],
        "rooms": [{"id": str(r.id), "label": r.label} for r in unit.rooms if r.active],
        "photos": [_media_json(m) for m in visible],
    }


async def serialize_property(session: AsyncSession, prop: Property, *, host_view: bool = True, with_checklist: bool = False) -> dict:
    units = [u for u in prop.units if host_view or u.status == ST_APPROVED]
    if host_view:
        units = [u for u in units if u.status != ST_ARCHIVED]
    prop_media = (await media_for(session, "property", [prop.id])).get(prop.id, [])
    unit_media = await media_for(session, "unit", [u.id for u in units])
    ptype = await session.get(PropertyType, prop.type_id) if prop.type_id else None
    data: dict[str, Any] = {
        "id": str(prop.id), "name": prop.name, "description": prop.description, "rules": prop.rules,
        "type_id": str(prop.type_id) if prop.type_id else None, "type_name": ptype.name if ptype else None,
        "address_line1": prop.address_line1, "address_line2": prop.address_line2, "suburb": prop.suburb, "state": prop.state,
        "postcode": prop.postcode, "country": prop.country, "latitude": prop.latitude, "longitude": prop.longitude,
        "timezone": prop.timezone, "status": prop.status,
        "photos": [_media_json(m) for m in prop_media if host_view or m.status in PUBLIC_MEDIA],
        "units": [_unit_json(u, unit_media.get(u.id, []), host_view=host_view) for u in units],
    }
    if host_view:
        revisions = (await session.execute(
            select(ListingRevision).where(ListingRevision.status == "pending",
                                          ListingRevision.entity_id.in_([prop.id, *[u.id for u in units]]))
        )).scalars().all()
        data.update({
            "checkin_instructions": prop.checkin_instructions, "cancellation_template_key": prop.cancellation_template_key,
            "review_note": prop.review_note, "host_id": str(prop.host_id),
            "pending_revisions": [{"id": str(r.id), "entity_type": r.entity_type, "entity_id": str(r.entity_id),
                                   "fields": sorted(r.data or {}), "photos_changed": r.photos_changed} for r in revisions],
        })
        if with_checklist:
            data["checklist"] = await checklist(session, prop)
    return data


# ---------- admin decisions ----------

async def _owner_of(session: AsyncSession, prop: Property) -> tuple[HostProfile, User]:
    host = await session.get(HostProfile, prop.host_id)
    return host, await session.get(User, host.user_id)


def _notify(session: AsyncSession, owner: User, template: str, title: str, note: str | None) -> None:
    enqueue_email(session, to_email=owner.email, template=template, context={"name": owner.full_name, "title": title, "note": note or ""}, user_id=owner.id)


_DECISION_STATUS = {"approve": ST_APPROVED, "reject": ST_REJECTED, "request_changes": ST_CHANGES}
_DECISION_EMAIL = {"approve": "listing_approved", "reject": "listing_rejected", "request_changes": "listing_changes_requested"}


def _check_decision(decision: str, note: str | None) -> None:
    if decision not in _DECISION_STATUS:
        raise AppError(422, "INVALID_DECISION", "Decision must be approve, reject or request_changes.")
    if decision != "approve" and not (note or "").strip():
        raise AppError(422, "NOTE_REQUIRED", "Tell the host what to change or why it was rejected.")


async def decide_property(session: AsyncSession, prop: Property, *, decision: str, note: str | None, admin: User, ip: str | None) -> None:
    _check_decision(decision, note)
    if prop.status != ST_SUBMITTED:
        raise AppError(409, "NOT_PENDING", "This property is not waiting for review.")
    now = datetime.now(UTC)
    new_status = _DECISION_STATUS[decision]
    clean_note = (note or "").strip() or None
    prop.status = new_status
    prop.review_note = clean_note
    if decision == "approve":
        prop.approved_at = now
    for unit in prop.units:
        if unit.status == ST_SUBMITTED:
            unit.status = new_status
            unit.review_note = clean_note
            if decision == "approve":
                unit.approved_at = now
    audit.record(session, action=f"property.{decision}", entity_type="property", entity_id=prop.id, actor_id=admin.id,
                 before={"status": ST_SUBMITTED}, after={"status": new_status}, reason=clean_note, ip=ip)
    _, owner = await _owner_of(session, prop)
    _notify(session, owner, _DECISION_EMAIL[decision], prop.name, clean_note)


async def decide_unit(session: AsyncSession, unit: Unit, prop: Property, *, decision: str, note: str | None, admin: User, ip: str | None) -> None:
    _check_decision(decision, note)
    if unit.status != ST_SUBMITTED:
        raise AppError(409, "NOT_PENDING", "This unit is not waiting for review.")
    if prop.status not in LIVE_LIFECYCLE:
        raise AppError(409, "DECIDE_VIA_PROPERTY", "Decide on the property; its units are reviewed together.")
    unit.status = _DECISION_STATUS[decision]
    unit.review_note = (note or "").strip() or None
    if decision == "approve":
        unit.approved_at = datetime.now(UTC)
    audit.record(session, action=f"unit.{decision}", entity_type="unit", entity_id=unit.id, actor_id=admin.id,
                 before={"status": ST_SUBMITTED}, after={"status": unit.status}, reason=unit.review_note, ip=ip)
    _, owner = await _owner_of(session, prop)
    _notify(session, owner, _DECISION_EMAIL[decision], f"{prop.name}: {unit.name}", unit.review_note)


async def decide_revision(session: AsyncSession, rev: ListingRevision, *, decision: str, note: str | None, admin: User, ip: str | None) -> None:
    if decision not in ("approve", "reject"):
        raise AppError(422, "INVALID_DECISION", "Decision must be approve or reject.")
    if rev.status != "pending":
        raise AppError(409, "NOT_PENDING", "This change has already been decided.")
    if decision == "reject" and not (note or "").strip():
        raise AppError(422, "NOTE_REQUIRED", "Tell the host why the change was rejected.")
    if rev.entity_type == "property":
        entity = await load_property(session, rev.entity_id)
        prop = entity
    else:
        entity = (await session.execute(select(Unit).where(Unit.id == rev.entity_id).execution_options(populate_existing=True))).scalar_one()
        prop = await load_property(session, entity.property_id)
    media = (await media_for(session, rev.entity_type, [rev.entity_id])).get(rev.entity_id, [])
    if decision == "approve":
        if rev.entity_type == "property":
            _apply_property_values(entity, rev.data or {})
        else:
            await _apply_unit_values(session, entity, rev.data or {})
        for m in media:
            if m.status == "pending_add":
                m.status = "approved"
            elif m.status == "pending_delete":
                _delete_files(m)
                await session.delete(m)
    else:
        for m in media:
            if m.status == "pending_add":
                _delete_files(m)
                await session.delete(m)
            elif m.status == "pending_delete":
                m.status = "approved"
    rev.status = "approved" if decision == "approve" else "rejected"
    rev.reviewed_by = admin.id
    rev.reviewed_at = datetime.now(UTC)
    rev.note = (note or "").strip() or None
    audit.record(session, action=f"revision.{decision}", entity_type=rev.entity_type, entity_id=rev.entity_id, actor_id=admin.id,
                 after={"fields": sorted(rev.data or {}), "photos_changed": rev.photos_changed}, reason=rev.note, ip=ip)
    _, owner = await _owner_of(session, prop)
    title = prop.name if rev.entity_type == "property" else f"{prop.name}: {entity.name}"
    _notify(session, owner, "listing_approved" if decision == "approve" else "listing_rejected", f"{title} (changes)", rev.note)


async def set_unpublished(session: AsyncSession, prop: Property, *, unpublish: bool, reason: str | None, admin: User, ip: str | None) -> None:
    if unpublish:
        if prop.status not in (ST_APPROVED, ST_PAUSED):
            raise AppError(409, "NOT_LIVE", "Only live listings can be unpublished.")
        if not (reason or "").strip():
            raise AppError(422, "REASON_REQUIRED", "Give a reason.")
        prop.status = ST_UNPUBLISHED
    else:
        if prop.status != ST_UNPUBLISHED:
            raise AppError(409, "NOT_UNPUBLISHED", "This listing is not unpublished.")
        prop.status = ST_APPROVED
    audit.record(session, action="property.unpublished" if unpublish else "property.republished", entity_type="property",
                 entity_id=prop.id, actor_id=admin.id, reason=reason, ip=ip)


# ---------- public ----------

async def public_property(session: AsyncSession, property_id: uuid.UUID) -> dict:
    prop = await load_property(session, property_id)
    host = await session.get(HostProfile, prop.host_id)
    if prop.status != ST_APPROVED or not host_service.is_active(host):
        raise AppError(404, "NOT_FOUND", "Property not found")
    data = await serialize_property(session, prop, host_view=False)
    from app.modules.availability import service as availability  # local import: availability imports this module's models

    globals_ = await availability.load_global_rules(session)
    overrides = (await availability.overrides_for_hosts(session, {host.id})).get(host.id)
    by_id = {u.id: u for u in prop.units}
    for unit_json in data["units"]:
        r = availability.rules_for(by_id[uuid.UUID(unit_json["id"])], globals_, overrides)
        unit_json["rules"] = {
            "interval_minutes": r.interval_minutes, "step_hours": r.step_hours, "max_stay_hours": r.max_stay_hours,
            "min_lead_hours": r.min_lead_hours, "max_advance_days": r.max_advance_days,
        }
    owner = await session.get(User, host.user_id)
    data["host_first_name"] = owner.full_name.split()[0]
    # The exact address is only revealed to a guest after a confirmed booking.
    for hidden in ("address_line1", "address_line2", "postcode"):
        data.pop(hidden, None)
    if data.get("latitude") is not None and data.get("longitude") is not None:
        data["latitude"], data["longitude"] = round(data["latitude"], 2), round(data["longitude"], 2)  # about 1 km
    if not data["units"]:
        raise AppError(404, "NOT_FOUND", "Property not found")
    return data
