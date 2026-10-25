import math
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, null, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.availability import service as availability
from app.modules.hosts.models import REVIEW_APPROVED, HostProfile
from app.modules.properties.models import ST_APPROVED, Media, Property, PropertyType, Unit, unit_amenities

MAX_CANDIDATES = 600
PUBLIC_MEDIA = ("approved", "pending_delete")


def _distance_km(lat: float, lng: float):
    """Great-circle distance (haversine) in SQL, so no extra extension is needed at MVP scale."""
    p_lat, p_lng = func.radians(Property.latitude), func.radians(Property.longitude)
    cos_angle = (func.cos(math.radians(lat)) * func.cos(p_lat) * func.cos(p_lng - math.radians(lng))
                 + math.sin(math.radians(lat)) * func.sin(p_lat))
    distance = 6371.0 * func.acos(func.least(1.0, func.greatest(-1.0, cos_angle)))
    # LEAST/GREATEST ignore NULLs in PostgreSQL, which would turn "no coordinates" into 20,015 km. Keep it NULL.
    return case((or_(Property.latitude.is_(None), Property.longitude.is_(None)), null()), else_=distance)


def _text_filter(q: str):
    tokens = [t for t in re.split(r"[,\s]+", q.strip()) if len(t) >= 2]
    if not tokens:
        return None
    clauses = []
    for token in tokens:
        like = f"%{token}%"
        clauses.append(or_(Property.name.ilike(like), Property.suburb.ilike(like), Property.state.ilike(like), Property.postcode.ilike(like)))
    return and_(*clauses)


def _active_host_filter():
    return and_(
        HostProfile.review_status == REVIEW_APPROVED, HostProfile.suspended_at.is_(None),
        HostProfile.stripe_details_submitted.is_(True), HostProfile.stripe_payouts_enabled.is_(True),
        HostProfile.stripe_transfers_active.is_(True),
    )


def _like(text: str) -> str:
    """The text as a LIKE pattern that matches it literally (a typed % or _ is just a character)."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def suggest(session: AsyncSession, q: str, limit: int = 5) -> dict:
    """Search-box suggestions from the places guests can really book today: property names, and the areas those properties are in.

    Uses the same rules as search: an approved property, with at least one approved unit, whose host is active."""
    text = q.strip()
    if len(text) < 2:
        return {"places": [], "properties": []}
    like = _like(text)
    has_unit = select(Unit.id).where(Unit.property_id == Property.id, Unit.status == ST_APPROVED).exists()
    visible = [Property.status == ST_APPROVED, has_unit, _active_host_filter()]

    names = (await session.execute(
        select(Property.id, Property.name, Property.suburb, Property.state, PropertyType.name)
        .join(HostProfile, HostProfile.id == Property.host_id).join(PropertyType, PropertyType.id == Property.type_id, isouter=True)
        .where(*visible, Property.name.ilike(like, escape="\\")).order_by(func.lower(Property.name), Property.id).limit(limit))).all()

    areas = (await session.execute(
        select(Property.suburb, Property.state, func.count())
        .join(HostProfile, HostProfile.id == Property.host_id)
        .where(*visible, Property.suburb != "", or_(Property.suburb.ilike(like, escape="\\"), Property.postcode.ilike(like, escape="\\")))
        .group_by(Property.suburb, Property.state).order_by(func.count().desc(), Property.suburb).limit(limit))).all()

    return {
        "places": [{"text": ", ".join(p for p in (suburb, state) if p), "suburb": suburb, "state": state, "properties": int(n)} for suburb, state, n in areas],
        "properties": [{"id": str(pid), "name": name, "suburb": suburb, "state": state, "type_name": type_name} for pid, name, suburb, state, type_name in names],
    }


async def search(
    session: AsyncSession, *, q: str | None, lat: float | None, lng: float | None, radius_km: float, checkin: str | None, hours: int,
    guests: int, min_price: int | None, max_price: int | None, type_ids: list[uuid.UUID], amenity_ids: list[uuid.UUID],
    sort: str, page: int, page_size: int,
) -> dict:
    now = datetime.now(UTC)
    checkin_local = availability.parse_local(checkin) if checkin else None
    globals_ = await availability.load_global_rules(session)
    if hours < globals_["booking.duration_step_hours"] or hours % globals_["booking.duration_step_hours"] or hours > 2160:
        raise AppError(422, "INVALID_DURATION", "Choose a stay in multiples of 24 hours.")

    geo = lat is not None and lng is not None
    distance = _distance_km(lat, lng).label("distance") if geo else None
    columns = [Unit, Property, HostProfile] + ([distance] if geo else [])
    stmt = (
        select(*columns).join(Property, Property.id == Unit.property_id).join(HostProfile, HostProfile.id == Property.host_id)
        .where(Property.status == ST_APPROVED, Unit.status == ST_APPROVED, _active_host_filter(), Unit.capacity >= guests)
        .execution_options(populate_existing=True)
    )
    text = _text_filter(q) if q else None
    if geo:
        d_lat = radius_km / 111.0
        d_lng = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
        near = and_(Property.latitude.between(lat - d_lat, lat + d_lat), Property.longitude.between(lng - d_lng, lng + d_lng),
                    _distance_km(lat, lng) <= radius_km)
        # Properties without coordinates can still match on the place name the guest typed.
        stmt = stmt.where(or_(near, and_(Property.latitude.is_(None), text)) if text is not None else near)
    elif text is not None:
        stmt = stmt.where(text)
    if min_price is not None:
        stmt = stmt.where(Unit.price_per_24h >= min_price)
    if max_price is not None:
        stmt = stmt.where(Unit.price_per_24h <= max_price)
    if type_ids:
        stmt = stmt.where(Property.type_id.in_(type_ids))
    for amenity_id in amenity_ids:  # a unit must have every selected amenity
        stmt = stmt.where(Unit.id.in_(select(unit_amenities.c.unit_id).where(unit_amenities.c.amenity_id == amenity_id)))
    if sort == "price_asc":
        stmt = stmt.order_by(Unit.price_per_24h.asc())
    elif sort == "price_desc":
        stmt = stmt.order_by(Unit.price_per_24h.desc())
    elif geo and sort != "newest":
        stmt = stmt.order_by(distance.asc().nulls_last())
    else:
        stmt = stmt.order_by(Property.approved_at.desc().nullslast())
    rows = (await session.execute(stmt.limit(MAX_CANDIDATES))).all()

    hint = None
    if checkin_local is not None and rows:
        overrides = await availability.overrides_for_hosts(session, {r[2].id for r in rows})
        passing, by_period = [], defaultdict(list)
        first_violation = None
        for row in rows:
            unit, prop, host = row[0], row[1], row[2]
            tz = ZoneInfo(prop.timezone)
            rules = availability.rules_for(unit, globals_, overrides.get(host.id))
            violation = availability.check_stay(rules, unit.capacity, tz, checkin_local, hours, guests, now)
            if violation:
                first_violation = first_violation or violation
                continue
            # units that share a time zone and timing (almost all do) are checked together; a fixed unit has its own check-in and check-out
            by_period[availability.stay_period(rules, tz, checkin_local, hours)].append(row)
        free: set[uuid.UUID] = set()
        for (start, end), group in by_period.items():
            free |= await availability.units_with_free_room(session, [r[0].id for r in group], start, end)
        passing = [r for period_rows in by_period.values() for r in period_rows if r[0].id in free]
        order = {id(r): i for i, r in enumerate(rows)}
        rows = sorted(passing, key=lambda r: order[id(r)])
        if not rows and first_violation and not any(by_period.values()):
            hint = first_violation.message

    grouped: dict[uuid.UUID, dict] = {}
    for row in rows:
        unit, prop = row[0], row[1]
        dist = row[3] if geo else None
        entry = grouped.setdefault(prop.id, {"property": prop, "units": [], "distance": dist})
        entry["units"].append(unit)

    prop_ids = list(grouped)
    covers: dict[uuid.UUID, Media] = {}
    if prop_ids:
        media = (await session.execute(
            select(Media).where(Media.owner_type == "property", Media.owner_id.in_(prop_ids), Media.status.in_(PUBLIC_MEDIA))
            .order_by(Media.position, Media.created_at)
        )).scalars().all()
        for m in media:
            covers.setdefault(m.owner_id, m)
    type_names = {t.id: t.name for t in (await session.execute(select(PropertyType))).scalars()}
    from app.modules.reviews import service as review_service

    ratings = await review_service.property_ratings(session, prop_ids)

    items = []
    for pid, entry in grouped.items():
        prop, units = entry["property"], entry["units"]
        cover = covers.get(pid)
        items.append({
            "id": str(pid), "name": prop.name, "type_name": type_names.get(prop.type_id), "suburb": prop.suburb, "state": prop.state,
            "cover_url": f"/api/v1/media/{cover.path}" if cover else None, "thumb_url": f"/api/v1/media/{cover.thumb_path}" if cover else None,
            "currency": units[0].currency, "min_price": min(u.price_per_24h for u in units),
            "distance_km": round(entry["distance"], 1) if entry["distance"] is not None else None, "rating": ratings.get(pid),
            "units": [{"id": str(u.id), "name": u.name, "price_per_24h": u.price_per_24h, "capacity": u.capacity, "stay_mode": u.stay_mode} for u in units],
        })
    if sort in ("price_asc", "price_desc"):
        items.sort(key=lambda i: i["min_price"], reverse=sort == "price_desc")

    total = len(items)
    start_idx = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size, "items": items[start_idx:start_idx + page_size], "hint": hint}
