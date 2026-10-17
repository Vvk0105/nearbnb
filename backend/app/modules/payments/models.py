import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Sequence, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey


class StripeEvent(UUIDPrimaryKey, Base):
    """Every webhook event is stored once; the unique event id makes processing idempotent."""

    __tablename__ = "stripe_events"

    event_id: Mapped[str] = mapped_column(String(100), unique=True)
    type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


PAY_CREATED = "created"
PAY_PROCESSING = "processing"
PAY_SUCCEEDED = "succeeded"
PAY_FAILED = "failed"
PAY_CANCELLED = "cancelled"


class Payment(UUIDPrimaryKey, Base):
    """One Stripe PaymentIntent for one booking. The amount always comes from the booking's price snapshot."""

    __tablename__ = "payments"

    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id"), index=True)
    stripe_payment_intent_id: Mapped[str] = mapped_column(String(100), unique=True)
    stripe_charge_id: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[int] = mapped_column(Integer)  # minor units
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default=PAY_CREATED, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    failure_code: Mapped[str | None] = mapped_column(String(100))
    failure_message: Mapped[str | None] = mapped_column(Text)
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Refund(UUIDPrimaryKey, Base):
    """Money returned to a guest. Phase 5 only creates the automatic refund for a payment that arrived too late;
    cancellation and exceptional refunds (Phase 6) use the same table."""

    __tablename__ = "refunds"

    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id"), index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payments.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(20))  # late_payment | policy | host_cancel | exceptional | admin
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | succeeded | failed
    stripe_refund_id: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    host_reversal_amount: Mapped[int] = mapped_column(Integer, default=0)  # taken back from the host's payout first
    stripe_reversal_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Invoice(UUIDPrimaryKey, Base):
    """The tax invoice / receipt issued when a booking is paid. `data` is a frozen copy of everything printed on it."""

    __tablename__ = "invoices"

    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id"), unique=True)
    seq: Mapped[int] = mapped_column(Integer, Sequence("invoice_number_seq"), unique=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


RR_PENDING, RR_APPROVED, RR_REJECTED = "pending", "approved", "rejected"


class RefundRequest(UUIDPrimaryKey, Base):
    """A host asking an admin to refund (part of) a guest's payment out of the host's own earnings, for example after a
    complaint. Nothing is refunded until an admin approves."""

    __tablename__ = "refund_requests"

    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id"), index=True)
    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("host_profiles.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=RR_PENDING, index=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    refund_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("refunds.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
