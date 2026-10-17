from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKey


class TaxRule(UUIDPrimaryKey, TimestampMixin, Base):
    """Tax for a country (and optionally a region). Admin-managed so no rate is hard-coded in the code."""

    __tablename__ = "tax_rules"

    country: Mapped[str] = mapped_column(String(2), index=True)
    region: Mapped[str] = mapped_column(String(50), default="")  # empty = whole country
    name: Mapped[str] = mapped_column(String(50))                # e.g. GST
    rate_bp: Mapped[int] = mapped_column(Integer)                # basis points: 1000 = 10.00%
    inclusive: Mapped[bool] = mapped_column(Boolean, default=False)  # True: listed prices already contain the tax
    applies_to: Mapped[list] = mapped_column(JSONB, default=list)    # accommodation | cleaning | service_fee
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
