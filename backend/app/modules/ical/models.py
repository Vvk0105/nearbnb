import uuid
from datetime import datetime, time

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey

CF_OPEN, CF_RESOLVED, CF_DISMISSED = "open", "resolved", "dismissed"


class IcalSource(UUIDPrimaryKey, Base):
    """An outside calendar (Airbnb, Booking.com...) whose booked dates are imported as blocks on one unit."""

    __tablename__ = "ical_sources"

    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(Text)
    # The outside calendar only knows whole days; these are the times we put on them (property local time).
    checkin_time: Mapped[time] = mapped_column(Time, default=time(15, 0))
    checkout_time: Mapped[time] = mapped_column(Time, default=time(11, 0))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(String(300))
    last_modified: Mapped[str | None] = mapped_column(String(100))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    failure_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    events_count: Mapped[int] = mapped_column(Integer, default=0)  # blocks currently created from this feed
    ranges: Mapped[list] = mapped_column(JSONB, default=list)     # the last parsed dates, so an unchanged feed can be re-applied
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IcalExport(UUIDPrimaryKey, Base):
    """The private link a host pastes into another site to share our bookings with it. One per unit."""

    __tablename__ = "ical_exports"

    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units.id", ondelete="CASCADE"), unique=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IcalConflict(UUIDPrimaryKey, Base):
    """The outside calendar says the dates are taken, but we already have a confirmed booking (or a pending request) there.
    Never resolved automatically: the host and the admin are told and a person decides."""

    __tablename__ = "ical_conflicts"
    __table_args__ = (UniqueConstraint("source_id", "booking_id", "external_start", "external_end", name="uq_ical_conflict"),)

    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ical_sources.id", ondelete="CASCADE"), index=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id"), index=True)
    external_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    external_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12), default=CF_OPEN, index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
