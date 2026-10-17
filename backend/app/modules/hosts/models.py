import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKey

# Review lifecycle of the host application (the admin's decision).
REVIEW_DRAFT = "draft"
REVIEW_PENDING = "pending_review"
REVIEW_CHANGES = "changes_requested"
REVIEW_REJECTED = "rejected"
REVIEW_APPROVED = "approved"

DOC_TYPES = ("government_id", "selfie", "business_proof")


class HostProfile(UUIDPrimaryKey, TimestampMixin, Base):
    """A user who applied to host. 'Active' is derived (approved + verified + Stripe-ready), never stored directly."""

    __tablename__ = "host_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True, index=True)
    review_status: Mapped[str] = mapped_column(String(20), default=REVIEW_DRAFT, index=True)
    application_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    review_note: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    stripe_account_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    stripe_details_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    stripe_payouts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    stripe_transfers_active: Mapped[bool] = mapped_column(Boolean, default=False)
    stripe_requirements: Mapped[list] = mapped_column(JSONB, default=list)
    stripe_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HostDocument(UUIDPrimaryKey, Base):
    """Identity documents. Stored under private/ and only ever served to admins."""

    __tablename__ = "host_documents"

    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("host_profiles.id", ondelete="CASCADE"), index=True)
    doc_type: Mapped[str] = mapped_column(String(30))
    storage_path: Mapped[str] = mapped_column(String(500))
    original_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HostSettingOverride(UUIDPrimaryKey, TimestampMixin, Base):
    """Per-host value for a setting marked host_override in the registry. History lives in the audit log."""

    __tablename__ = "host_setting_overrides"
    __table_args__ = (UniqueConstraint("host_id", "key", name="uq_host_setting"),)

    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("host_profiles.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[dict] = mapped_column(JSONB)  # {"v": ...}
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
