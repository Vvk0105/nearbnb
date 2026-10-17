import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey

IS_OPEN, IS_IN_PROGRESS, IS_RESOLVED, IS_DISMISSED = "open", "in_progress", "resolved", "dismissed"
OPEN_STATES = (IS_OPEN, IS_IN_PROGRESS)

CATEGORIES = {
    "guest": ("property_condition", "not_as_described", "host_behaviour", "safety", "payment_or_refund", "other"),
    "host": ("guest_behaviour", "damage", "safety", "payment_or_refund", "other"),
}


class Issue(UUIDPrimaryKey, Base):
    """A problem reported about a booking by the guest or the host. Admins work it from open to resolved or dismissed."""

    __tablename__ = "issues"

    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id"), index=True)
    reporter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    reporter_role: Mapped[str] = mapped_column(String(10))  # guest | host
    category: Mapped[str] = mapped_column(String(30))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default=IS_OPEN, index=True)
    admin_note: Mapped[str | None] = mapped_column(Text)    # internal, never shown to the reporter
    resolution: Mapped[str | None] = mapped_column(Text)    # the message the reporter receives when it is closed
    blocks_payout: Mapped[bool] = mapped_column(Boolean, default=False)  # while open, the host payout for this booking waits
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
