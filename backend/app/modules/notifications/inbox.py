"""In-app notifications (the bell). Every moment that sends someone an email also leaves a notification for them.

Not every email: codes and security messages are only ever emailed. For the rest, the title is the email's subject and the body is
the reviewer's note when there is one. A notification also has a link to the page where the person can act, and a tone:
  * "action":  something the person has to do (reply, pay, fix a listing)
  * "problem": something went wrong
  * "news":    information
"""
import uuid
from dataclasses import dataclass

from jinja2 import Environment
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.notifications.models import Notification

_env = Environment(autoescape=False)


@dataclass(frozen=True)
class Notice:
    link: str  # a Jinja template over the email's context, e.g. "/account/trips/{{ booking_id }}"
    tone: str = "news"


TRIP = "/account/trips/{{ booking_id }}"
PAY = "/account/trips/{{ booking_id }}/pay"

# template name -> where the notification leads and how loudly it is shown. Templates not listed here (verification codes,
# password and account security messages) never become notifications.
NOTICES: dict[str, Notice] = {
    # guest side of a booking
    "booking_request_guest": Notice(TRIP),
    "booking_accepted_guest": Notice(PAY, "action"),
    "booking_rejected_guest": Notice(TRIP),
    "booking_expired_guest": Notice(TRIP),
    "booking_payment_reminder_guest": Notice(PAY, "action"),
    "booking_payment_expired_guest": Notice(TRIP),
    "payment_confirmed_guest": Notice(TRIP),
    "payment_failed_guest": Notice(PAY, "problem"),
    "payment_late_refund_guest": Notice(TRIP, "problem"),
    "booking_cancelled_guest": Notice(TRIP),
    "refund_issued_guest": Notice(TRIP),
    "review_invite_guest": Notice(TRIP, "action"),
    "review_reminder_guest": Notice(TRIP, "action"),
    "checkin_reminder_guest": Notice(TRIP),
    "checkout_reminder_guest": Notice(TRIP),
    # host side of a booking
    "booking_request_host": Notice("/host/requests", "action"),
    "booking_reminder_host": Notice("/host/requests", "action"),
    "booking_withdrawn_host": Notice("/host/requests"),
    "booking_expired_host": Notice("/host/requests"),
    "booking_payment_expired_host": Notice("/host/requests"),
    "booking_confirmed_host": Notice("/host/requests"),
    "booking_cancelled_host": Notice("/host/requests"),
    "refund_request_decided_host": Notice("/host/money"),
    "payout_sent_host": Notice("/host/money"),
    "review_invite_host": Notice("/host/requests", "action"),
    "review_reminder_host": Notice("/host/requests", "action"),
    "checkin_reminder_host": Notice("/host/requests"),
    # reports and problems (either side)
    "issue_received_reporter": Notice(TRIP),
    "issue_updated_reporter": Notice(TRIP),
    # hosting setup and listings
    "host_submitted": Notice("/host/apply"),
    "host_approved": Notice("/host"),
    "host_changes_requested": Notice("/host/apply", "action"),
    "host_rejected": Notice("/host/apply", "action"),
    "host_active": Notice("/host"),
    "host_suspended": Notice("/host/apply", "problem"),
    "host_reactivated": Notice("/host"),
    "host_stripe_action_needed": Notice("/host/payouts", "action"),
    "listing_approved": Notice("/host/properties"),
    "listing_changes_requested": Notice("/host/properties", "action"),
    "listing_rejected": Notice("/host/properties", "action"),
    "ical_conflict_host": Notice("/host/properties", "action"),
    "ical_sync_failing": Notice("/host/properties", "problem"),
    "account_suspended": Notice("/account", "problem"),
    "account_reactivated": Notice("/account"),
    # administrators
    "refund_requested_admin": Notice("/admin/refunds", "action"),
    "payout_failed_admin": Notice("/admin/payouts", "problem"),
    "host_cancellation_alert_admin": Notice("/admin/hosts", "problem"),
    "issue_reported_admin": Notice("/admin/issues", "action"),
    "ical_conflict_admin": Notice("/admin/calendar", "problem"),
}


def build(user_id: uuid.UUID, template: str, subject: str, context: dict) -> Notification | None:
    """The notification that goes with an email, or None when this kind of email is not shown in the app."""
    notice = NOTICES.get(template)
    if notice is None:
        return None
    link = _env.from_string(notice.link).render(**context).strip()
    return Notification(user_id=user_id, kind=template, title=subject[:300], body=str(context.get("note") or "")[:1000], link=link, tone=notice.tone)


# ---------- reading and marking ----------

async def unread_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    return (await session.execute(select(func.count()).select_from(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None)))).scalar_one()


async def list_for(session: AsyncSession, user_id: uuid.UUID, *, page: int, page_size: int, unread_only: bool) -> dict:
    where = [Notification.user_id == user_id] + ([Notification.read_at.is_(None)] if unread_only else [])
    total = (await session.execute(select(func.count()).select_from(Notification).where(*where))).scalar_one()
    rows = (await session.execute(select(Notification).where(*where).order_by(Notification.created_at.desc(), Notification.id.desc())
                                  .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"total": total, "page": page, "page_size": page_size, "unread": await unread_count(session, user_id), "items": [serialize(n) for n in rows]}


def serialize(n: Notification) -> dict:
    return {"id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body, "link": n.link, "tone": n.tone,
            "created_at": n.created_at.isoformat(), "read": n.read_at is not None}


async def mark_read(session: AsyncSession, user_id: uuid.UUID, notification_id: uuid.UUID) -> None:
    n = await session.get(Notification, notification_id)
    if n is None or n.user_id != user_id:  # someone else's notification looks exactly like one that does not exist
        raise AppError(404, "NOT_FOUND", "Notification not found.")
    if n.read_at is None:
        n.read_at = func.now()


async def mark_all_read(session: AsyncSession, user_id: uuid.UUID) -> int:
    result = await session.execute(update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None)).values(read_at=func.now()))
    return result.rowcount or 0
