import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_admin
from app.core.errors import AppError
from app.core.ratelimit import client_ip
from app.modules.audit import service as audit
from app.modules.pricing.models import TaxRule
from app.modules.pricing.service import TAX_COMPONENTS
from app.modules.users.models import User

router = APIRouter(prefix="/admin/tax-rules", tags=["admin-tax"], dependencies=[Depends(require_admin)])


class TaxIn(BaseModel):
    country: str | None = Field(default=None, min_length=2, max_length=2)
    region: str = Field(default="", max_length=50)
    name: str = Field(min_length=1, max_length=50)
    rate_percent: float = Field(ge=0, le=100)
    inclusive: bool = False
    applies_to: list[str] = Field(default_factory=lambda: list(TAX_COMPONENTS))
    active: bool = True


def _json(r: TaxRule) -> dict:
    return {"id": str(r.id), "country": r.country, "region": r.region, "name": r.name, "rate_percent": r.rate_bp / 100,
            "inclusive": r.inclusive, "applies_to": r.applies_to, "active": r.active}


def _check(body: TaxIn) -> None:
    if not body.applies_to or any(c not in TAX_COMPONENTS for c in body.applies_to):
        raise AppError(422, "INVALID_TAX_BASE", f"Tax can apply to: {', '.join(TAX_COMPONENTS)}.")


@router.get("")
async def list_rules(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(TaxRule).order_by(TaxRule.country, TaxRule.region))).scalars().all()
    return [_json(r) for r in rows]


@router.post("", status_code=201)
async def create_rule(body: TaxIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    _check(body)
    if not body.country:
        raise AppError(422, "COUNTRY_REQUIRED", "Choose a country.")
    rule = TaxRule(country=body.country.upper(), region=body.region.strip(), name=body.name.strip(), rate_bp=round(body.rate_percent * 100),
                   inclusive=body.inclusive, applies_to=body.applies_to, active=body.active)
    session.add(rule)
    await session.flush()
    audit.record(session, action="tax_rule.created", entity_type="tax_rule", entity_id=rule.id, actor_id=admin.id, after=_json(rule), ip=client_ip(request))
    await session.commit()
    return _json(rule)


@router.put("/{rule_id}")
async def update_rule(rule_id: str, body: TaxIn, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    _check(body)
    try:
        rule = await session.get(TaxRule, uuid.UUID(rule_id))
    except ValueError:
        rule = None
    if rule is None:
        raise AppError(404, "NOT_FOUND", "Tax rule not found")
    before = _json(rule)
    rule.name, rule.rate_bp = body.name.strip(), round(body.rate_percent * 100)
    rule.inclusive, rule.applies_to, rule.active = body.inclusive, body.applies_to, body.active
    audit.record(session, action="tax_rule.updated", entity_type="tax_rule", entity_id=rule.id, actor_id=admin.id,
                 before=before, after=_json(rule), ip=client_ip(request))
    await session.commit()
    return _json(rule)
