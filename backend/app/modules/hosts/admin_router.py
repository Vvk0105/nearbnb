import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_admin
from app.core.errors import AppError
from app.core.ratelimit import client_ip
from app.core.storage import get_storage
from app.modules.audit import service as audit
from app.modules.hosts import service
from app.modules.hosts.models import HostDocument, HostProfile
from app.modules.notifications.service import kick_outbox
from app.modules.settings.registry import REGISTRY
from app.modules.users.models import User

router = APIRouter(prefix="/admin/hosts", tags=["admin-hosts"], dependencies=[Depends(require_admin)])


class DecisionIn(BaseModel):
    decision: str
    note: str | None = Field(default=None, max_length=2000)


class ReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class OverrideIn(BaseModel):
    value: Any
    reason: str | None = Field(default=None, max_length=500)


async def _load(session: AsyncSession, host_id: str) -> HostProfile:
    try:
        hid = uuid.UUID(host_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND") from None
    host = await session.get(HostProfile, hid)
    if host is None:
        raise AppError(404, "NOT_FOUND", "Host not found")
    return host


@router.get("")
async def list_hosts(
    state: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(HostProfile, User).join(User, User.id == HostProfile.user_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.email.ilike(like), User.full_name.ilike(like)))
    if state in ("draft", "pending_review", "changes_requested", "rejected"):
        stmt = stmt.where(HostProfile.review_status == state, HostProfile.suspended_at.is_(None))
    rows = (await session.execute(stmt.order_by(HostProfile.submitted_at.desc().nullslast(), HostProfile.created_at.desc()))).all()
    # The derived state (active / approved / restricted / suspended) lives in Python, so filter after loading.
    items = []
    for host, user in rows:
        host_state = service.host_state(host)
        if state in ("approved", "active", "restricted", "suspended") and host_state != state:
            continue
        items.append({
            "id": str(host.id), "state": host_state, "email": user.email, "full_name": user.full_name,
            "submitted_at": host.submitted_at.isoformat() if host.submitted_at else None,
            "created_at": host.created_at.isoformat(),
        })
    total = len(items)
    return {"total": total, "page": page, "page_size": page_size, "items": items[(page - 1) * page_size: page * page_size]}


@router.get("/{host_id}")
async def host_detail(host_id: str, session: AsyncSession = Depends(get_session)):
    host = await _load(session, host_id)
    user = await session.get(User, host.user_id)
    data = await service.serialize_host(session, host, for_admin=True)
    overrides = await service.get_overrides(session, host.id)
    from app.modules.settings import service as settings_service

    catalogue = []
    for definition in REGISTRY.values():
        if definition.host_override:
            effective = await settings_service.get_effective(session, definition.key, overrides=overrides)
            global_value = await settings_service.get_effective(session, definition.key)
            catalogue.append({
                "key": definition.key, "label": definition.label, "unit": definition.unit, "type": definition.type,
                "choices": list(definition.choices), "global_value": global_value.value,
                "value": effective.value, "overridden": definition.key in overrides,
            })
    return {**data, "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name, "phone": user.phone},
            "overrides": catalogue}


@router.get("/{host_id}/documents/{doc_id}/file")
async def download_document(host_id: str, doc_id: str, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    host = await _load(session, host_id)
    try:
        did = uuid.UUID(doc_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND") from None
    doc = await session.get(HostDocument, did)
    if doc is None or doc.host_id != host.id:
        raise AppError(404, "NOT_FOUND")
    audit.record(session, action="host_document.viewed", entity_type="host", entity_id=host.id, actor_id=admin.id,
                 after={"document": str(doc.id), "type": doc.doc_type}, ip=client_ip(request))
    await session.commit()
    return FileResponse(
        get_storage().resolve(doc.storage_path), media_type=doc.content_type,
        headers={"Cache-Control": "no-store", "Content-Disposition": f'inline; filename="{doc.id}"', "X-Content-Type-Options": "nosniff"},
    )


@router.post("/{host_id}/decision")
async def decide(host_id: str, body: DecisionIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    host = await _load(session, host_id)
    await service.decide(session, host, decision=body.decision, note=body.note, admin=admin, ip=client_ip(request))
    await session.commit()
    kick_outbox()
    return await service.serialize_host(session, host, for_admin=True)


@router.post("/{host_id}/suspend")
async def suspend(host_id: str, body: ReasonIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    host = await _load(session, host_id)
    await service.set_suspended(session, host, suspend=True, reason=body.reason, admin=admin, ip=client_ip(request))
    await session.commit()
    kick_outbox()
    return await service.serialize_host(session, host, for_admin=True)


@router.post("/{host_id}/reactivate")
async def reactivate(host_id: str, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    host = await _load(session, host_id)
    await service.set_suspended(session, host, suspend=False, reason=None, admin=admin, ip=client_ip(request))
    await session.commit()
    kick_outbox()
    return await service.serialize_host(session, host, for_admin=True)


@router.put("/{host_id}/overrides/{key}")
async def set_override(host_id: str, key: str, body: OverrideIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    host = await _load(session, host_id)
    value = await service.set_override(session, host, key, body.value, reason=body.reason, admin=admin, ip=client_ip(request))
    await session.commit()
    return {"key": key, "value": value}


@router.delete("/{host_id}/overrides/{key}")
async def clear_override(host_id: str, key: str, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    host = await _load(session, host_id)
    await service.clear_override(session, host, key, admin=admin, ip=client_ip(request))
    await session.commit()
    return {"ok": True}


@router.get("/_stats/counts")
async def counts(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(HostProfile.review_status, func.count()).group_by(HostProfile.review_status))).all()
    return {status: n for status, n in rows}
