import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.settings.models import Setting
from app.modules.settings.registry import get_def


@dataclass(frozen=True)
class SettingValue:
    key: str
    value: Any
    source: str  # override | country | global | default
    version: int | None


async def _latest(session: AsyncSession, key: str, scope: str, scope_ref: str) -> Setting | None:
    stmt = (
        select(Setting)
        .where(
            Setting.key == key,
            Setting.scope == scope,
            Setting.scope_ref == scope_ref,
            Setting.effective_from <= datetime.now(UTC),
        )
        .order_by(Setting.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_effective(
    session: AsyncSession,
    key: str,
    *,
    country: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> SettingValue:
    """Resolution order: per-host override, then country, then global, then registry default."""
    definition = get_def(key)
    if overrides and key in overrides:
        return SettingValue(key, overrides[key], "override", None)
    if country:
        row = await _latest(session, key, "country", country.upper())
        if row:
            return SettingValue(key, row.value["v"], "country", row.version)
    row = await _latest(session, key, "global", "")
    if row:
        return SettingValue(key, row.value["v"], "global", row.version)
    return SettingValue(key, definition.default, "default", None)


async def get_value(session: AsyncSession, key: str, **kwargs: Any) -> Any:
    return (await get_effective(session, key, **kwargs)).value


async def set_global(
    session: AsyncSession,
    key: str,
    value: Any,
    *,
    actor_id: uuid.UUID,
    reason: str | None,
    ip: str | None,
) -> SettingValue:
    """Validates, writes a NEW version (history is never overwritten) and audits the change."""
    definition = get_def(key)
    clean = definition.validate(value)

    if key == "booking.max_stay_hours":
        step = await get_value(session, "booking.duration_step_hours")
        if clean % step != 0:
            from app.core.errors import AppError

            raise AppError(422, "INVALID_SETTING", f"Maximum stay must be a multiple of {step} hours")

    before = await get_effective(session, key)
    latest = await _latest(session, key, "global", "")
    next_version = (latest.version if latest else 0) + 1
    session.add(
        Setting(
            key=key, scope="global", scope_ref="", value={"v": clean},
            version=next_version, reason=reason, created_by=actor_id,
        )
    )
    audit.record(
        session,
        action="setting.updated",
        entity_type="setting",
        entity_id=key,
        actor_id=actor_id,
        before={"value": before.value, "version": before.version},
        after={"value": clean, "version": next_version},
        reason=reason,
        ip=ip,
    )
    await session.commit()
    return SettingValue(key, clean, "global", next_version)
