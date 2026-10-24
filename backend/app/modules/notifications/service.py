import asyncio
import logging
import smtplib
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.notifications import inbox
from app.modules.notifications.models import EMAIL_FAILED, EMAIL_PENDING, EMAIL_SENT, EmailOutbox
from app.modules.notifications.templates import render

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5


def enqueue_email(
    session: AsyncSession,
    *,
    to_email: str,
    template: str,
    context: dict,
    user_id: uuid.UUID | None = None,
) -> EmailOutbox:
    """Adds an email to the outbox in the caller's transaction. Call kick_outbox() after commit."""
    subject, text, html = render(template, context)
    row = EmailOutbox(
        user_id=user_id, to_email=to_email, template=template, subject=subject, body_text=text, body_html=html
    )
    session.add(row)
    if user_id is not None:  # the same moment also leaves a line in the person's bell (codes and security emails do not)
        notification = inbox.build(user_id, template, subject, context)
        if notification is not None:
            session.add(notification)
    return row


def kick_outbox() -> None:
    """Best-effort: ask a worker to send now. The beat schedule is the safety net if this fails."""
    if not get_settings().email_kick_enabled:
        return
    try:
        from app.jobs.celery_app import celery_app

        celery_app.send_task("emails.send_pending")
    except Exception:  # noqa: BLE001
        log.warning("could not enqueue outbox task; beat will pick it up")


def _send_smtp(row: EmailOutbox) -> None:
    settings = get_settings()
    if not settings.smtp_host:
        # Development without SMTP: print the email so codes can be copied from the worker log.
        log.warning("SMTP not configured. EMAIL to=%s subject=%s\n%s", row.to_email, row.subject, row.body_text)
        return
    msg = EmailMessage()
    msg["Subject"] = row.subject
    msg["From"] = settings.email_from
    msg["To"] = row.to_email
    if settings.email_reply_to:
        msg["Reply-To"] = settings.email_reply_to
    msg.set_content(row.body_text or "")
    if row.body_html:
        msg.add_alternative(row.body_html, subtype="html")
    if settings.smtp_use_ssl:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        server.starttls()
    with server:
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)


async def process_outbox(session: AsyncSession, limit: int = 50) -> int:
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(EmailOutbox)
            .where(EmailOutbox.status == EMAIL_PENDING, EmailOutbox.send_after <= now)
            .order_by(EmailOutbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    sent = 0
    for row in rows:
        try:
            await asyncio.to_thread(_send_smtp, row)
            row.status = EMAIL_SENT
            row.sent_at = now
            row.body_text = None  # may contain one-time codes
            row.body_html = None
            sent += 1
        except Exception as exc:  # noqa: BLE001
            row.attempts += 1
            row.last_error = str(exc)[:500]
            if row.attempts >= MAX_ATTEMPTS:
                row.status = EMAIL_FAILED
            else:
                row.send_after = now + timedelta(minutes=2**row.attempts)
            log.exception("email send failed id=%s", row.id)
    await session.commit()
    return sent
