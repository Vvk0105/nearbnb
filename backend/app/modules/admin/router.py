import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_admin
from app.core.errors import AppError
from app.core.ratelimit import client_ip
from app.modules.audit.models import AuditLog
from app.modules.legal import service as legal
from app.modules.legal.models import DOC_TYPES
from app.modules.settings import service as settings_service
from app.modules.settings.models import Setting
from app.modules.settings.registry import REGISTRY, get_def
from app.modules.users.models import User

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class SettingUpdate(BaseModel):
    value: Any
    reason: str | None = Field(default=None, max_length=500)


class LegalPublish(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)


@router.get("/settings")
async def list_settings(session: AsyncSession = Depends(get_session)):
    items = []
    for definition in REGISTRY.values():
        current = await settings_service.get_effective(session, definition.key)
        items.append(
            {
                "key": definition.key, "group": definition.group, "label": definition.label,
                "description": definition.description, "type": definition.type, "unit": definition.unit,
                "min": definition.min, "max": definition.max, "choices": list(definition.choices), "host_override": definition.host_override,
                "default": definition.default, "value": current.value, "source": current.source,
                "version": current.version,
            }
        )
    return items


@router.put("/settings/{key}")
async def update_setting(key: str, body: SettingUpdate, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    get_def(key)
    result = await settings_service.set_global(
        session, key, body.value, actor_id=admin.id, reason=body.reason, ip=client_ip(request)
    )
    return {"key": key, "value": result.value, "version": result.version}


@router.get("/settings/{key}/history")
async def setting_history(key: str, session: AsyncSession = Depends(get_session)):
    get_def(key)
    rows = (
        await session.execute(
            select(Setting, User.email)
            .join(User, User.id == Setting.created_by, isouter=True)
            .where(Setting.key == key, Setting.scope == "global")
            .order_by(Setting.version.desc())
            .limit(100)
        )
    ).all()
    return [
        {"version": s.version, "value": s.value["v"], "reason": s.reason, "created_at": s.created_at, "by": email}
        for s, email in rows
    ]


@router.get("/audit")
async def list_audit(
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)
    if entity_id:
        filters.append(AuditLog.entity_id == entity_id)
    total = (await session.execute(select(func.count()).select_from(AuditLog).where(*filters))).scalar_one()
    rows = (
        await session.execute(
            select(AuditLog, User.email)
            .join(User, User.id == AuditLog.actor_id, isouter=True)
            .where(*filters)
            .order_by(AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [
            {
                "id": a.id, "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id,
                "actor": email, "before": a.before, "after": a.after, "reason": a.reason, "ip": a.ip,
                "created_at": a.created_at,
            }
            for a, email in rows
        ],
    }


@router.post("/legal/{doc_type}", status_code=201)
async def publish_legal(doc_type: str, body: LegalPublish, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if doc_type not in DOC_TYPES:
        raise AppError(404, "UNKNOWN_DOCUMENT")
    doc = await legal.publish(session, doc_type, body.title, body.body, actor_id=admin.id, ip=client_ip(request))
    return {"doc_type": doc.doc_type, "version": doc.version}
