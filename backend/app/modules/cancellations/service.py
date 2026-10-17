"""Cancellation policy templates: seeding, validation, editing, and the copy stored on each booking."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.audit import service as audit
from app.modules.cancellations.models import CancellationTemplate
from app.modules.settings import service as settings_service

DEFAULT_TEMPLATES = [
    {"key": "flexible", "name": "Flexible", "strictness": 1,
     "description": "Full refund of the accommodation up to 24 hours before check-in, then nothing.",
     "tiers": [{"hours_before": 24, "refund_pct": 100}]},
    {"key": "moderate", "name": "Moderate", "strictness": 2,
     "description": "Full refund up to 5 days before check-in, 50% from then until 24 hours before, then nothing.",
     "tiers": [{"hours_before": 120, "refund_pct": 100}, {"hours_before": 24, "refund_pct": 50}]},
    {"key": "strict", "name": "Strict", "strictness": 3,
     "description": "Full refund up to 14 days before check-in, 50% up to 7 days before, then nothing.",
     "tiers": [{"hours_before": 336, "refund_pct": 100}, {"hours_before": 168, "refund_pct": 50}]},
]


async def seed_templates(session: AsyncSession) -> int:
    existing = {k for (k,) in (await session.execute(select(CancellationTemplate.key))).all()}
    added = 0
    for t in DEFAULT_TEMPLATES:
        if t["key"] not in existing:
            session.add(CancellationTemplate(**t))
            added += 1
    if added:
        await session.commit()
    return added


def validate_tiers(tiers) -> list[dict]:
    """Largest 'hours before' first; the refund percentage can only fall as the check-in gets closer."""
    def bad(msg: str) -> AppError:
        return AppError(422, "INVALID_TIERS", msg)

    if not isinstance(tiers, list) or len(tiers) > 6:
        raise bad("Add up to 6 steps.")
    clean = []
    for t in tiers:
        if not isinstance(t, dict) or set(t) != {"hours_before", "refund_pct"}:
            raise bad("Each step needs 'hours before check-in' and 'refund %'.")
        hours, pct = t["hours_before"], t["refund_pct"]
        if isinstance(hours, bool) or isinstance(pct, bool) or not isinstance(hours, int) or not isinstance(pct, int):
            raise bad("Hours and percentages must be whole numbers.")
        if not (0 <= hours <= 24 * 90):
            raise bad("Hours before check-in must be between 0 and 2160.")
        if not (0 <= pct <= 100):
            raise bad("The refund percentage must be between 0 and 100.")
        clean.append({"hours_before": hours, "refund_pct": pct})
    clean.sort(key=lambda t: t["hours_before"], reverse=True)
    for earlier, later in zip(clean, clean[1:]):
        if earlier["hours_before"] == later["hours_before"]:
            raise bad("Two steps cannot start at the same number of hours.")
        if later["refund_pct"] > earlier["refund_pct"]:
            raise bad("The refund cannot go up as check-in gets closer.")
    return clean


def serialize(t: CancellationTemplate) -> dict:
    return {"id": str(t.id), "key": t.key, "name": t.name, "description": t.description, "tiers": t.tiers, "strictness": t.strictness,
            "active": t.active, "version": t.version}


async def list_templates(session: AsyncSession, *, active_only: bool = False) -> list[CancellationTemplate]:
    q = select(CancellationTemplate).order_by(CancellationTemplate.strictness, CancellationTemplate.name)
    if active_only:
        q = q.where(CancellationTemplate.active.is_(True))
    return list((await session.execute(q)).scalars())


async def get_template(session: AsyncSession, key: str) -> CancellationTemplate | None:
    return (await session.execute(select(CancellationTemplate).where(CancellationTemplate.key == key))).scalar_one_or_none()


async def effective_template(session: AsyncSession, key: str | None) -> CancellationTemplate:
    """The host's chosen template, or the default if none was chosen or the chosen one has been switched off."""
    chosen = await get_template(session, key) if key else None
    if chosen is not None and chosen.active:
        return chosen
    default_key = await settings_service.get_value(session, "cancellation.default_template")
    default = await get_template(session, default_key)
    if default is None:
        raise AppError(500, "NO_CANCELLATION_TEMPLATE", "No cancellation policy is configured.")
    return default


async def snapshot_for(session: AsyncSession, key: str | None) -> dict:
    """The copy stored on a booking when it is requested."""
    t = await effective_template(session, key)
    return {"key": t.key, "name": t.name, "version": t.version, "tiers": t.tiers}


async def check_host_choice(session: AsyncSession, key: str | None) -> None:
    """A host may pick any active template that is not stricter than the admin's limit."""
    if not key:
        return
    t = await get_template(session, key)
    if t is None or not t.active:
        raise AppError(422, "INVALID_CANCELLATION_TEMPLATE", "Choose one of the listed cancellation policies.")
    limit = await get_template(session, await settings_service.get_value(session, "cancellation.guardrail_template"))
    if limit is not None and t.strictness > limit.strictness:
        raise AppError(422, "CANCELLATION_TOO_STRICT", f"Hosts cannot choose a policy stricter than {limit.name}.")


async def create_template(session: AsyncSession, actor_id: uuid.UUID, *, key: str, name: str, description: str, tiers, strictness: int) -> CancellationTemplate:
    key = key.strip().lower()
    if not key.replace("_", "").isalnum() or len(key) > 50:
        raise AppError(422, "INVALID_KEY", "The key may contain only letters, numbers and underscores.")
    if await get_template(session, key) is not None:
        raise AppError(409, "KEY_TAKEN", "A policy with that key already exists.")
    t = CancellationTemplate(key=key, name=name.strip(), description=description.strip(), tiers=validate_tiers(tiers), strictness=strictness)
    session.add(t)
    await session.flush()
    audit.record(session, action="cancellation_template.created", entity_type="cancellation_template", entity_id=t.id, actor_id=actor_id, after=serialize(t))
    return t


async def update_template(session: AsyncSession, actor_id: uuid.UUID, t: CancellationTemplate, *, name=None, description=None, tiers=None,
                          strictness=None, active=None) -> CancellationTemplate:
    before = serialize(t)
    if active is False and t.key == await settings_service.get_value(session, "cancellation.default_template"):
        raise AppError(409, "IS_DEFAULT", "Choose a different default policy in Settings before switching this one off.")
    changed_terms = False
    if name is not None:
        t.name = name.strip()
    if description is not None:
        t.description = description.strip()
    if tiers is not None:
        clean = validate_tiers(tiers)
        changed_terms = clean != t.tiers
        t.tiers = clean
    if strictness is not None:
        t.strictness = strictness
    if active is not None:
        t.active = active
    if changed_terms:
        t.version += 1  # new bookings use the new terms; existing bookings keep the copy they were made with
    audit.record(session, action="cancellation_template.updated", entity_type="cancellation_template", entity_id=t.id, actor_id=actor_id, before=before, after=serialize(t))
    return t
