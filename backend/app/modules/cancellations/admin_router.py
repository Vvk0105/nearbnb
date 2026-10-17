import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_admin
from app.core.errors import AppError
from app.modules.cancellations import service
from app.modules.users.models import User

router = APIRouter(tags=["cancellation policies"])


class TemplateIn(BaseModel):
    key: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    tiers: list[dict]
    strictness: int = Field(default=2, ge=1, le=10)


class TemplatePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    tiers: list[dict] | None = None
    strictness: int | None = Field(default=None, ge=1, le=10)
    active: bool | None = None


@router.get("/meta/cancellation")
async def public_templates(session: AsyncSession = Depends(get_session)):
    """The policies a host can choose from (also shown to guests on the property page)."""
    return [service.serialize(t) for t in await service.list_templates(session, active_only=True)]


@router.get("/admin/cancellation-templates")
async def list_templates(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return [service.serialize(t) for t in await service.list_templates(session)]


@router.post("/admin/cancellation-templates", status_code=201)
async def create_template(body: TemplateIn, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    t = await service.create_template(session, admin.id, key=body.key, name=body.name, description=body.description, tiers=body.tiers, strictness=body.strictness)
    await session.commit()
    return service.serialize(t)


@router.patch("/admin/cancellation-templates/{template_id}")
async def update_template(template_id: str, body: TemplatePatch, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    try:
        tid = uuid.UUID(template_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Policy not found.") from None
    from app.modules.cancellations.models import CancellationTemplate

    t = await session.get(CancellationTemplate, tid)
    if t is None:
        raise AppError(404, "NOT_FOUND", "Policy not found.")
    await service.update_template(session, admin.id, t, name=body.name, description=body.description, tiers=body.tiers, strictness=body.strictness, active=body.active)
    await session.commit()
    return service.serialize(t)
