import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey

DIR_GUEST_TO_HOST = "guest_to_host"  # public on the property page and feeds its rating
DIR_HOST_TO_GUEST = "host_to_guest"  # private: only hosts who receive a request from that guest, and admins

RV_SUBMITTED = "submitted"  # written, waiting for the other side (or for the window to close)
RV_PUBLISHED = "published"
RV_HIDDEN = "hidden"        # taken down by an admin


class Review(UUIDPrimaryKey, Base):
    """One side's review of one stay. Reviews are blind: neither is visible until both are in or the window has closed."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("booking_id", "direction", name="uq_review_per_side"),
        Index("ix_reviews_property_status", "property_id", "direction", "status"),
        Index("ix_reviews_subject", "subject_id", "direction", "status"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id"))
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("properties.id"))
    direction: Mapped[str] = mapped_column(String(16))
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default=RV_SUBMITTED)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_reason: Mapped[str | None] = mapped_column(Text)
    hidden_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
