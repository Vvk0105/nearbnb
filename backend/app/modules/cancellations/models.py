from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey


class CancellationTemplate(UUIDPrimaryKey, Base):
    """A cancellation policy hosts can choose. `tiers` say how much of the accommodation a guest gets back:
    [{"hours_before": 120, "refund_pct": 100}, {"hours_before": 24, "refund_pct": 50}] means 100% when cancelling at least
    120 hours before check-in, 50% from then until 24 hours before, nothing after that.

    A booking stores a copy of the tiers at the time it was requested, so editing a template never changes an existing booking."""

    __tablename__ = "cancellation_templates"

    key: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    tiers: Mapped[list] = mapped_column(JSONB, default=list)
    strictness: Mapped[int] = mapped_column(Integer, default=1)  # higher = stricter; hosts may not choose stricter than the admin's limit
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
