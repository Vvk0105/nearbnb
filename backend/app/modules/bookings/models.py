import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey

# requested -> accepted -> (payment_pending -> confirmed -> checked_in -> completed: later phases)
# requested -> rejected | expired | withdrawn ; accepted -> payment_expired | withdrawn
B_REQUESTED = "requested"
B_ACCEPTED = "accepted"
B_PAYMENT_PENDING = "payment_pending"
B_CONFIRMED = "confirmed"
B_CHECKED_IN = "checked_in"
B_COMPLETED = "completed"
B_REJECTED = "rejected"
B_EXPIRED = "expired"
B_WITHDRAWN = "withdrawn"
B_PAYMENT_EXPIRED = "payment_expired"
B_CANCELLED = "cancelled"

# While in these states the booking holds a room (a "hold" row in room_occupancy).
HOLDING = (B_REQUESTED, B_ACCEPTED, B_PAYMENT_PENDING)
# Guest/host contact details and check-in instructions are shared once payment has succeeded.
CONFIRMED_STATES = (B_CONFIRMED, B_CHECKED_IN, B_COMPLETED)


class Booking(UUIDPrimaryKey, Base):
    """A guest's request for one unit for an exact period. The room is assigned when the request is made.

    Amounts live in `pricing_snapshot` and the rules that applied in `policy_snapshot`, so later changes to
    settings, tax or prices never alter an existing booking."""

    __tablename__ = "bookings"
    __table_args__ = (
        Index("ix_bookings_status_expires", "status", "expires_at"),
        Index("ix_bookings_host_status", "host_id", "status"),
        Index("ix_bookings_guest_status", "guest_id", "status"),
    )

    reference: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    guest_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("host_profiles.id"))
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("properties.id"))
    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units.id"))
    room_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rooms.id"))

    check_in: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    check_out: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hours: Mapped[int] = mapped_column(Integer)
    guests: Mapped[int] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default=B_REQUESTED)

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # host must respond before this
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    closed_reason: Mapped[str | None] = mapped_column(Text)  # host's reason when rejecting, or why it ended
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    guest_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    pricing_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    policy_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)


class BookingEvent(UUIDPrimaryKey, Base):
    """Every status change of a booking: who, when, from what to what. Written by `transition` only."""

    __tablename__ = "booking_events"

    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
