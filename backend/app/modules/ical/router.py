from datetime import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import get_current_user, require_admin
from app.core.errors import AppError
from app.core.ratelimit import rate_limit
from app.modules.hosts import service as host_service
from app.modules.ical import service
from app.modules.ical.models import IcalSource
from app.modules.notifications.service import kick_outbox
from app.modules.properties import service as properties
from app.modules.properties.models import Property, Unit
from app.modules.users.models import User

router = APIRouter(tags=["calendar sync"])


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2000)
    checkin_time: time = time(15, 0)
    checkout_time: time = time(11, 0)
    sync_now: bool = True


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    url: str | None = Field(default=None, max_length=2000)
    checkin_time: time | None = None
    checkout_time: time | None = None
    active: bool | None = None


class DismissIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _own_hosts(request: Request) -> set[str]:
    """Names our own calendar links can have, so a host cannot paste our feed back into our import."""
    settings = get_settings()
    hosts = {(request.url.hostname or "").lower(), (urlsplit(settings.app_base_url).hostname or "").lower()}
    hosts.update((urlsplit(o).hostname or "").lower() for o in settings.cors_origin_list)
    return {h for h in hosts if h}


async def _host_unit_ids(session: AsyncSession, host) -> set:
    rows = await session.execute(select(Unit.id).join(Property, Property.id == Unit.property_id).where(Property.host_id == host.id))
    return {r[0] for r in rows}


# ---------- host ----------

@router.get("/host/units/{unit_id}/ical")
async def unit_calendar_sync(unit_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    unit, _ = await properties.owned_unit(session, host, unit_id)
    eligible = service.single_room(unit) is not None
    sources = (await session.execute(select(IcalSource).where(IcalSource.unit_id == unit.id).order_by(IcalSource.created_at))).scalars().all()
    export = await service.export_for_unit(session, unit) if eligible else None
    await session.commit()
    conflicts = await service.list_conflicts(session, unit_ids={unit.id}, status="open")
    return {"eligible": eligible, "reason": None if eligible else "Calendar sync works for units with a single room. This unit has several.",
            "sources": [service.source_json(s) for s in sources], "export_path": f"/ical/{export.token}.ics" if export else None,
            "max_sources": service.MAX_SOURCES_PER_UNIT, "conflicts": conflicts["items"]}


@router.post("/host/units/{unit_id}/ical/sources", status_code=201, dependencies=[Depends(rate_limit("ical_write", 60, 3600))])
async def add_source(unit_id: str, body: SourceIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    properties.require_not_suspended(host)
    unit, _ = await properties.owned_unit(session, host, unit_id)
    source = await service.create_source(session, host, user, unit, name=body.name, url=body.url, checkin=body.checkin_time, checkout=body.checkout_time, own_hosts=_own_hosts(request))
    result = await service.sync_source(session, source) if body.sync_now else None
    await session.commit()
    kick_outbox()
    return {"source": service.source_json(source), "sync": result}


@router.patch("/host/ical/sources/{source_id}", dependencies=[Depends(rate_limit("ical_write", 60, 3600))])
async def edit_source(source_id: str, body: SourcePatch, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    properties.require_not_suspended(host)
    source = await service.load_source(session, source_id, await _host_unit_ids(session, host))
    await service.update_source(session, user, source, name=body.name, url=body.url, checkin=body.checkin_time, checkout=body.checkout_time, active=body.active, own_hosts=_own_hosts(request))
    await session.commit()
    return service.source_json(source)


@router.delete("/host/ical/sources/{source_id}", dependencies=[Depends(rate_limit("ical_write", 60, 3600))])
async def remove_source(source_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    source = await service.load_source(session, source_id, await _host_unit_ids(session, host))
    await service.delete_source(session, user, source)
    await session.commit()
    return {"ok": True}


@router.post("/host/ical/sources/{source_id}/sync", dependencies=[Depends(rate_limit("ical_sync", 30, 3600))])
async def sync_now(source_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    source = await service.load_source(session, source_id, await _host_unit_ids(session, host))
    result = await service.sync_source(session, source, manual=True)
    await session.commit()
    kick_outbox()
    return {"source": service.source_json(source), "sync": result}


@router.post("/host/units/{unit_id}/ical/export/rotate", dependencies=[Depends(rate_limit("ical_write", 60, 3600))])
async def rotate_link(unit_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    unit, _ = await properties.owned_unit(session, host, unit_id)
    if service.single_room(unit) is None:
        raise AppError(409, "NOT_ELIGIBLE", "Calendar sync works for units with a single room.")
    export = await service.rotate_export(session, user, unit)
    await session.commit()
    return {"export_path": f"/ical/{export.token}.ics"}


@router.get("/host/ical/conflicts")
async def host_conflicts(status: str | None = Query("open", pattern="^(open|resolved|dismissed)$"), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    return await service.list_conflicts(session, unit_ids=await _host_unit_ids(session, host), status=status)


@router.post("/host/ical/conflicts/{conflict_id}/dismiss")
async def host_dismiss(conflict_id: str, body: DismissIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    await service.dismiss_conflict(session, user, conflict_id, await _host_unit_ids(session, host), body.note)
    await session.commit()
    return {"ok": True}


# ---------- admin ----------

@router.get("/admin/ical")
async def admin_calendar_sync(status: str | None = Query("open", pattern="^(open|resolved|dismissed)$"), page: int = Query(1, ge=1, le=500),
                              _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return {"conflicts": await service.list_conflicts(session, unit_ids=None, status=status, page=page), "failing_sources": await service.failing_sources(session)}


@router.post("/admin/ical/conflicts/{conflict_id}/dismiss")
async def admin_dismiss(conflict_id: str, body: DismissIn, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    await service.dismiss_conflict(session, admin, conflict_id, None, body.note)
    await session.commit()
    return {"ok": True}


@router.post("/admin/ical/sources/{source_id}/sync")
async def admin_sync(source_id: str, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    source = await service.load_source(session, source_id)
    result = await service.sync_source(session, source, manual=True)
    await session.commit()
    return {"source": service.source_json(source), "sync": result}


# ---------- public: the private feed other sites read ----------

@router.get("/ical/{filename}", dependencies=[Depends(rate_limit("ical_feed", 120, 60))])
async def public_feed(filename: str, request: Request, session: AsyncSession = Depends(get_session)):
    """Anyone with the secret link can read it (that is how other sites fetch it). It holds dates only, never guest details."""
    token = filename.removesuffix(".ics")
    built = await service.build_export(session, token) if filename.endswith(".ics") and 16 <= len(token) <= 64 else None
    if built is None:
        raise AppError(404, "NOT_FOUND", "This calendar link is not valid.")
    body, etag = built
    headers = {"ETag": etag, "Cache-Control": "private, max-age=300"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="text/calendar; charset=utf-8", headers=headers)
