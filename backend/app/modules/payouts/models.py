import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey

TR_ELIGIBLE = "eligible"        # due, waiting for the next job run (or retrying after a failure)
TR_TRANSFERRED = "transferred"  # money moved to the host's Stripe account
TR_FAILED = "failed"
TR_REVERSED = "reversed"        # taken back from the host (refund after payout)
TR_SKIPPED = "skipped"          # nothing to pay out (for example a fully refunded booking)


class Transfer(UUIDPrimaryKey, Base):
    """The host's share of one booking, moved from the platform to the host's connected Stripe account.
    Stripe pays the host's bank on its own schedule; that is tracked separately in PayoutRecord."""

    __tablename__ = "transfers"

    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id"), unique=True)
    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("host_profiles.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default=TR_ELIGIBLE, index=True)
    stripe_transfer_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    transferred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_amount: Mapped[int] = mapped_column(Integer, default=0)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PayoutRecord(UUIDPrimaryKey, Base):
    """A bank payout Stripe made (or tried to make) to a host's connected account, learned from `payout.*` webhooks."""

    __tablename__ = "payout_records"

    host_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("host_profiles.id"), index=True)
    stripe_account_id: Mapped[str] = mapped_column(String(64), index=True)
    stripe_payout_id: Mapped[str] = mapped_column(String(100), unique=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20))  # pending | in_transit | paid | failed | canceled
    arrival_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
