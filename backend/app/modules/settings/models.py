import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey


class Setting(UUIDPrimaryKey, Base):
    """One row per (key, scope, scope_ref, version). The highest effective version wins."""

    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("key", "scope", "scope_ref", "version", name="uq_setting_version"),)

    key: Mapped[str] = mapped_column(String(100), index=True)
    scope: Mapped[str] = mapped_column(String(20), default="global")  # global | country
    scope_ref: Mapped[str] = mapped_column(String(10), default="")  # country code for scope=country
    value: Mapped[dict] = mapped_column(JSONB)  # stored as {"v": <value>}
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
