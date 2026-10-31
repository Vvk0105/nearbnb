import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey

EMAIL_PENDING = "pending"
EMAIL_SENT = "sent"
EMAIL_FAILED = "failed"


class EmailOutbox(UUIDPrimaryKey, Base):
    """Emails are written in the same transaction as the event that caused them, then sent by a worker.
    Body text is cleared after sending because it can contain one-time codes."""

    __tablename__ = "email_outbox"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    to_email: Mapped[str] = mapped_column(String(320), index=True)
    template: Mapped[str] = mapped_column(String(60))
    subject: Mapped[str] = mapped_column(String(300))
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default=EMAIL_PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    send_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(UUIDPrimaryKey, Base):
    """One line in a person's bell. Created in the same transaction as the email for the same event (see inbox.py)."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", text("created_at DESC")),
        Index("ix_notifications_unread", "user_id", postgresql_where=text("read_at IS NULL")),
        Index("ix_notifications_unpushed", "created_at", postgresql_where=text("pushed_at IS NULL")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(60))      # the email template it came from
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(300), default="")
    tone: Mapped[str] = mapped_column(String(10), default="news")  # news | action | problem
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # handled by the push sender (sent, or nobody to send to)


class DeviceToken(UUIDPrimaryKey, Base):
    """A phone that has the app and allowed notifications. Firebase gives each install a token; one person can have several phones."""

    __tablename__ = "device_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(512), unique=True)
    platform: Mapped[str] = mapped_column(String(10))  # android | ios
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
