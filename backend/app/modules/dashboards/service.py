"""Dashboards, money pages and the lists admins use to run the platform without touching the database."""
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Integer, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.bookings import service as bookings
from app.modules.bookings.models import (
    B_ACCEPTED, B_CANCELLED, B_CHECKED_IN, B_COMPLETED, B_CONFIRMED, B_PAYMENT_PENDING, B_REQUESTED, Booking, BookingEvent,
)
from app.modules.hosts import service as host_service
from app.modules.ical.models import IcalConflict
from app.modules.issues.models import OPEN_STATES, Issue
from app.modules.hosts.models import REVIEW_PENDING, HostProfile
from app.modules.payments.models import PAY_FAILED, Invoice, Payment, Refund, RefundRequest, StripeEvent
from app.modules.payouts import service as payouts
from app.modules.payouts.models import TR_ELIGIBLE, TR_FAILED, TR_REVERSED, TR_TRANSFERRED, Transfer
from app.modules.properties.models import ST_SUBMITTED, ListingRevision, Property
from app.modules.settings import service as settings_service
from app.modules.users.models import User


def _now() -> datetime:
    return datetime.now(UTC)


async def pending_listing_counts(session: AsyncSession) -> tuple[int, int]:
    """(listings submitted for approval, edits to approved listings waiting for approval)"""
    listings = (await session.execute(select(func.count()).select_from(Property).where(Property.status == ST_SUBMITTED))).scalar_one()
    revisions = (await session.execute(select(func.count()).select_from(ListingRevision).where(ListingRevision.status == "pending"))).scalar_one()
    return listings, revisions


def dollars(cents: int | None) -> str:
    """Whole cents as a plain decimal for spreadsheets: 29563 -> '295.63'."""
    return f"{(cents or 0) / 100:.2f}"


# ---------- host: money ----------

async def host_money_rows(session: AsyncSession, host: HostProfile, *, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
    """One row per booking the host has earned (or is owed) money from, with only facts a host may see."""
    q = select(Booking).where(Booking.host_id == host.id, Booking.status.in_(payouts.PAYABLE)).order_by(Booking.check_in.desc())
    if since:
        q = q.where(Booking.check_in >= since)
    if until:
        q = q.where(Booking.check_in < until)
    rows = (await session.execute(q.limit(5000))).scalars().all()
    if not rows:
        return []
    ids = [b.id for b in rows]
    transfers = {t.booking_id: t for t in (await session.execute(select(Transfer).where(Transfer.booking_id.in_(ids)))).scalars()}
    refunds: dict[uuid.UUID, list[Refund]] = {}
    for rf in (await session.execute(select(Refund).where(Refund.booking_id.in_(ids)))).scalars():
        refunds.setdefault(rf.booking_id, []).append(rf)
    props = {p.id: p for p in (await session.execute(select(Property).where(Property.id.in_({b.property_id for b in rows})))).scalars()}
    out = []
    for b in rows:
        if b.status == B_CANCELLED and payouts.base_amount(b) <= 0 and b.id not in transfers:
            continue  # cancelled with a full refund: nothing earned
        tr = transfers.get(b.id)
        view = payouts.payout_view(b, tr)
        deductions = sum(r.amount for r in refunds.get(b.id, []) if r.type == "exceptional" and r.status in ("pending", "succeeded"))
        p = b.pricing_snapshot
        base = payouts.base_amount(b)
        out.append({
            "booking_id": str(b.id), "reference": b.reference, "property": props[b.property_id].name, "status": b.status, "currency": b.currency,
            "check_in": b.check_in.isoformat(), "check_in_label": b.check_in.astimezone(_zone(b)).strftime("%Y-%m-%d %H:%M"), "nights": p["nights"],
            "gross": (p["accommodation"] + p["cleaning_fee"]) if b.settlement is None else (b.settlement["refund"]["host_accommodation"] + b.settlement["refund"]["host_cleaning_fee"]),
            "commission": p["commission"] if b.settlement is None else b.settlement["refund"]["host_commission"],
            "host_share": base, "deductions": deductions, "net": max(0, (tr.amount if tr else base - deductions)),
            "reversed": tr.reversed_amount if tr else 0, "payout_state": view["state"], "eligible_at": view.get("eligible_at"),
            "transferred_at": view.get("transferred_at"),
        })
    return out


def _zone(b: Booking):
    from zoneinfo import ZoneInfo

    return ZoneInfo(b.timezone)


def summarise_money(rows: list[dict], now: datetime | None = None) -> dict:
    now = now or _now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    paid_out = sum(r["net"] - r["reversed"] for r in rows if r["payout_state"] in ("paid", "reversed"))
    scheduled = [r for r in rows if r["payout_state"] in ("scheduled", "held", "failed") and r["net"] > 0]
    next_month = month_start.replace(year=month_start.year + (month_start.month == 12), month=month_start.month % 12 + 1)
    this_month = sum(r["net"] for r in rows if month_start <= datetime.fromisoformat(r["check_in"]) < next_month and r["payout_state"] != "reversed")
    due = sorted((r for r in scheduled if r["eligible_at"]), key=lambda r: r["eligible_at"])
    return {
        "paid_out": paid_out, "scheduled": sum(r["net"] for r in scheduled), "this_month": this_month,
        "next_payout": {"at": due[0]["eligible_at"], "amount": due[0]["net"], "reference": due[0]["reference"]} if due else None,
        "deductions": sum(r["deductions"] for r in rows),
    }


async def host_money(session: AsyncSession, host: HostProfile, month: str | None, page: int, page_size: int) -> dict:
    since = until = None
    if month:
        y, m = (int(x) for x in month.split("-"))
        since = datetime(y, m, 1, tzinfo=UTC)
        until = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=UTC)
    every = await host_money_rows(session, host)
    shown = await host_money_rows(session, host, since=since, until=until) if month else every
    records = await payouts.list_records(session, host.id)
    return {
        "summary": summarise_money(every), "total": len(shown), "page": page, "page_size": page_size,
        "items": shown[(page - 1) * page_size: page * page_size],
        "bank_payouts": [{"id": r.stripe_payout_id, "amount": r.amount, "currency": r.currency, "status": r.status,
                          "arrival_date": r.arrival_date.isoformat() if r.arrival_date else None, "failure_code": r.failure_code} for r in records],
    }


MONEY_COLUMNS = ["reference", "property", "check_in", "nights", "status", "currency", "gross", "commission", "host_share", "deductions", "net", "payout_state", "transferred_at"]


def host_money_csv_rows(rows: list[dict]) -> list[list]:
    money = {"gross", "commission", "host_share", "deductions", "net"}
    return [[dollars(r[c]) if c in money else r.get(c) for c in MONEY_COLUMNS] for r in rows]


# ---------- host: dashboard ----------

async def host_dashboard(session: AsyncSession, host: HostProfile) -> dict:
    now = _now()
    counts = {}
    for key, statuses in (("pending", (B_REQUESTED,)), ("awaiting_payment", (B_ACCEPTED, B_PAYMENT_PENDING))):
        counts[key] = (await session.execute(select(func.count()).select_from(Booking).where(Booking.host_id == host.id, Booking.status.in_(statuses)))).scalar_one()
    stays = (await session.execute(
        select(Booking).where(Booking.host_id == host.id, Booking.status.in_((B_CONFIRMED, B_CHECKED_IN)), Booking.check_out > now).order_by(Booking.check_in).limit(10)
    )).scalars().all()
    serialized = await bookings.serialize(session, list(stays), "host")
    current = [s for s, b in zip(serialized, stays) if b.check_in <= now]
    upcoming = [s for s, b in zip(serialized, stays) if b.check_in > now]
    props = (await session.execute(select(Property.status, func.count()).where(Property.host_id == host.id).group_by(Property.status))).all()
    money = summarise_money(await host_money_rows(session, host))
    return {
        "state": host_service.host_state(host), "requests": counts, "current_stays": current, "upcoming_stays": upcoming,
        "listings": {status: n for status, n in props}, "money": money,
        "needs_attention": [msg for ok, msg in (
            (host_service.host_state(host) == "restricted", "Stripe needs more information before you can receive payouts."),
            (counts["pending"] > 0, f"{counts['pending']} booking request(s) waiting for your reply."),
        ) if ok],
    }


# ---------- admin: dashboard ----------

async def _daily_trend(session: AsyncSession, now: datetime, days: int = 30) -> list[dict]:
    """One row per UTC day for the last `days` days (zeros included, so a chart has no gaps): requests made and money paid."""
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    day_b = func.date(func.timezone("UTC", Booking.requested_at))
    booked = dict((await session.execute(select(day_b, func.count()).where(Booking.requested_at >= start).group_by(day_b))).all())
    day_p = func.date(func.timezone("UTC", Payment.succeeded_at))
    paid = dict((await session.execute(select(day_p, func.coalesce(func.sum(Payment.amount), 0))
                                       .where(Payment.status == "succeeded", Payment.succeeded_at >= start).group_by(day_p))).all())
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).date()
        out.append({"date": d.isoformat(), "bookings": int(booked.get(d, 0)), "paid": int(paid.get(d, 0))})
    return out


async def admin_dashboard(session: AsyncSession) -> dict:
    now = _now()
    week_ago, month_ago = now - timedelta(days=7), now - timedelta(days=30)

    async def count(model, *where) -> int:
        return (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()

    window = await settings_service.get_value(session, "alerts.host_cancellation_days")
    threshold = await settings_service.get_value(session, "alerts.host_cancellation_count")
    alert_hosts = (await session.execute(
        select(Booking.host_id).where(Booking.cancelled_by_role == "host", Booking.cancelled_at >= now - timedelta(days=window))
        .group_by(Booking.host_id).having(func.count() >= threshold))).all()
    listings, revisions = await pending_listing_counts(session)
    gross = (await session.execute(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "succeeded", Payment.succeeded_at >= month_ago))).scalar_one()
    refunded = (await session.execute(select(func.coalesce(func.sum(Refund.amount), 0)).where(Refund.status.in_(("pending", "succeeded")), Refund.created_at >= month_ago))).scalar_one()
    fee_rows = (await session.execute(
        select(func.coalesce(func.sum(cast(Booking.pricing_snapshot["service_fee"].astext, Integer)), 0),
               func.coalesce(func.sum(cast(Booking.pricing_snapshot["commission"].astext, Integer)), 0))
        .where(Booking.confirmed_at >= month_ago, Booking.status.in_((B_CONFIRMED, B_CHECKED_IN, B_COMPLETED))))).one()
    alerts = [
        ("applications", "Host applications to review", await count(HostProfile, HostProfile.review_status == REVIEW_PENDING), "/admin/hosts", "action"),
        ("listings", "Listings waiting for approval", listings, "/admin/listings", "action"),
        ("revisions", "Listing changes waiting for approval", revisions, "/admin/listings", "action"),
        ("refund_requests", "Refund requests to decide", await count(RefundRequest, RefundRequest.status == "pending"), "/admin/refunds", "action"),
        ("failed_refunds", "Refunds that failed", await count(Refund, Refund.status == "failed"), "/admin/refunds", "problem"),
        ("failed_payments", "Failed payments (7 days)", await count(Payment, Payment.status == PAY_FAILED, Payment.updated_at >= week_ago), "/admin/payments", "problem"),
        ("failed_payouts", "Host payouts that failed", await count(Transfer, Transfer.status == TR_FAILED), "/admin/payouts", "problem"),
        ("held_payouts", "Payouts on hold", await count(Booking, Booking.payout_hold.is_(True)), "/admin/payouts", "info"),
        ("failed_webhooks", "Stripe webhooks that failed", await count(StripeEvent, StripeEvent.error.is_not(None), StripeEvent.processed_at.is_(None)), "/admin/webhooks", "problem"),
        ("host_cancellations", "Hosts at the cancellation alert level", len(alert_hosts), "/admin/hosts", "problem"),
        ("open_issues", "Reported problems to look at", await count(Issue, Issue.status.in_(OPEN_STATES)), "/admin/issues", "action"),
        ("ical_conflicts", "Calendar conflicts to resolve", await count(IcalConflict, IcalConflict.status == "open"), "/admin/calendar", "problem"),
        ("suspended_users", "Suspended users", await count(User, User.status == "suspended"), "/admin/users", "info"),
    ]
    recent = (await session.execute(select(AuditLog, User.email).join(User, User.id == AuditLog.actor_id, isouter=True).order_by(AuditLog.id.desc()).limit(8))).all()
    trend = await _daily_trend(session, now)
    return {
        "trend": trend,
        "alerts": [{"key": k, "label": label, "count": n, "href": href, "severity": sev if n else "none"} for k, label, n, href, sev in alerts],
        "bookings": {
            "requests_waiting": await count(Booking, Booking.status == B_REQUESTED),
            "awaiting_payment": await count(Booking, Booking.status.in_((B_ACCEPTED, B_PAYMENT_PENDING))),
            "upcoming": await count(Booking, Booking.status == B_CONFIRMED, Booking.check_in > now),
            "in_progress": await count(Booking, Booking.status == B_CHECKED_IN),
            "completed_30_days": await count(Booking, Booking.status == B_COMPLETED, Booking.check_out >= month_ago),
            "cancelled_30_days": await count(Booking, Booking.status == B_CANCELLED, Booking.cancelled_at >= month_ago),
        },
        "money_30_days": {"gross_paid": int(gross), "refunded": int(refunded), "net_collected": int(gross) - int(refunded), "service_fees": int(fee_rows[0]), "commission": int(fee_rows[1])},
        "totals": {"users": await count(User), "hosts": await count(HostProfile), "properties": await count(Property, Property.status == "approved")},
        "recent": [{"id": a.id, "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id, "actor": email, "at": a.created_at.isoformat(), "reason": a.reason} for a, email in recent],
    }


# ---------- admin: lists ----------

async def admin_payments(session: AsyncSession, status: str | None, q: str | None, page: int, page_size: int) -> dict:
    query = select(Payment, Booking, User).join(Booking, Booking.id == Payment.booking_id).join(User, User.id == Booking.guest_id)
    if status:
        query = query.where(Payment.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Booking.reference.ilike(like), User.email.ilike(like), Payment.stripe_payment_intent_id.ilike(like), Payment.stripe_charge_id.ilike(like)))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await session.execute(query.order_by(Payment.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [
        {"id": str(p.id), "booking_id": str(b.id), "reference": b.reference, "guest": u.email, "amount": p.amount, "currency": p.currency, "status": p.status,
         "attempt": p.attempt, "failure_code": p.failure_code, "failure_message": p.failure_message, "intent_id": p.stripe_payment_intent_id,
         "charge_id": p.stripe_charge_id, "created_at": p.created_at.isoformat(), "succeeded_at": p.succeeded_at.isoformat() if p.succeeded_at else None}
        for p, b, u in rows]}


PAYOUT_FILTERS = {
    "paid": lambda: Transfer.status == TR_TRANSFERRED,
    "failed": lambda: Transfer.status == TR_FAILED,
    "reversed": lambda: Transfer.status == TR_REVERSED,
    "held": lambda: and_(Booking.payout_hold.is_(True), or_(Transfer.id.is_(None), Transfer.status != TR_TRANSFERRED)),
    "scheduled": lambda: and_(Booking.payout_hold.is_(False), or_(Transfer.id.is_(None), Transfer.status == TR_ELIGIBLE)),
}


async def admin_payouts(session: AsyncSession, state: str | None, page: int, page_size: int) -> dict:
    query = (select(Booking, Transfer, HostProfile, User).outerjoin(Transfer, Transfer.booking_id == Booking.id)
             .join(HostProfile, HostProfile.id == Booking.host_id).join(User, User.id == HostProfile.user_id)
             .where(Booking.status.in_(payouts.PAYABLE)))
    if state:
        query = query.where(PAYOUT_FILTERS[state]())
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await session.execute(query.order_by(Booking.check_in.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    items = []
    for b, tr, host, owner in rows:
        view = payouts.payout_view(b, tr)
        if view["state"] == "none" and tr is None:
            continue
        items.append({"booking_id": str(b.id), "reference": b.reference, "host": owner.full_name, "host_email": owner.email, "currency": b.currency, "check_in": b.check_in.isoformat(),
                      "stripe_transfer_id": tr.stripe_transfer_id if tr else None, **view})
    return {"total": total, "page": page, "page_size": page_size, "items": items}


async def admin_webhooks(session: AsyncSession, failed_only: bool, page: int, page_size: int) -> dict:
    where = [StripeEvent.error.is_not(None), StripeEvent.processed_at.is_(None)] if failed_only else []
    total = (await session.execute(select(func.count()).select_from(StripeEvent).where(*where))).scalar_one()
    rows = (await session.execute(select(StripeEvent).where(*where).order_by(StripeEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"total": total, "page": page, "page_size": page_size, "items": [
        {"id": str(e.id), "event_id": e.event_id, "type": e.type, "created_at": e.created_at.isoformat(), "processed_at": e.processed_at.isoformat() if e.processed_at else None, "error": e.error}
        for e in rows]}


async def admin_booking_detail(session: AsyncSession, booking_id: str) -> dict:
    """Everything about one booking: money, timeline, payments, refunds, payout and who did what."""
    base = await bookings.get_for(session, booking_id, viewer="admin")
    bid = uuid.UUID(booking_id)
    events = (await session.execute(select(BookingEvent, User.email).join(User, User.id == BookingEvent.actor_id, isouter=True)
                                    .where(BookingEvent.booking_id == bid).order_by(BookingEvent.at))).all()
    pays = (await session.execute(select(Payment).where(Payment.booking_id == bid).order_by(Payment.created_at))).scalars().all()
    refs = (await session.execute(select(Refund).where(Refund.booking_id == bid).order_by(Refund.created_at))).scalars().all()
    reqs = (await session.execute(select(RefundRequest).where(RefundRequest.booking_id == bid).order_by(RefundRequest.created_at))).scalars().all()
    invoice = (await session.execute(select(Invoice).where(Invoice.booking_id == bid))).scalar_one_or_none()
    audit = (await session.execute(select(AuditLog, User.email).join(User, User.id == AuditLog.actor_id, isouter=True)
                                   .where(AuditLog.entity_type == "booking", AuditLog.entity_id == booking_id).order_by(AuditLog.id))).all()
    booking = await session.get(Booking, bid)
    return {
        **base,
        "timeline": [{"at": e.at.isoformat(), "from": e.from_status, "to": e.to_status, "actor": email, "note": e.note} for e, email in events],
        "payments": [{"id": str(p.id), "status": p.status, "amount": p.amount, "attempt": p.attempt, "intent_id": p.stripe_payment_intent_id, "charge_id": p.stripe_charge_id,
                      "failure_message": p.failure_message, "created_at": p.created_at.isoformat()} for p in pays],
        "refunds": [{"id": str(r.id), "type": r.type, "status": r.status, "amount": r.amount, "reason": r.reason, "error": r.error, "stripe_refund_id": r.stripe_refund_id,
                     "host_reversal_amount": r.host_reversal_amount, "created_at": r.created_at.isoformat()} for r in refs],
        "refund_requests": [{"id": str(r.id), "status": r.status, "amount": r.amount, "reason": r.reason, "note": r.note} for r in reqs],
        "invoice": {"number": invoice.number, "issued_at": invoice.issued_at.isoformat()} if invoice else None,
        "settlement": booking.settlement, "snapshot": booking.pricing_snapshot,
        "audit": [{"at": a.created_at.isoformat(), "action": a.action, "actor": email, "reason": a.reason} for a, email in audit],
    }


# ---------- CSV exports ----------

def parse_day(value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    d = date.fromisoformat(value)
    return datetime(d.year, d.month, d.day, tzinfo=UTC) + (timedelta(days=1) if end else timedelta())


EXPORTS = {
    "bookings": ["reference", "status", "requested_at", "confirmed_at", "guest_name", "guest_email", "host_name", "host_email", "property", "unit", "check_in_local", "check_out_local",
                 "hours", "guests", "currency", "accommodation", "cleaning_fee", "service_fee", "tax", "total", "commission", "host_payable", "cancelled_by", "closed_reason"],
    "payments": ["payment_id", "booking", "guest_email", "stripe_payment_intent", "stripe_charge", "amount", "currency", "status", "attempt", "failure_code", "created_at", "succeeded_at"],
    "refunds": ["refund_id", "booking", "type", "amount", "currency", "status", "stripe_refund_id", "host_reversal_amount", "reason", "error", "created_at"],
    "payouts": ["booking", "host_name", "host_email", "amount", "currency", "status", "stripe_transfer_id", "eligible_at", "transferred_at", "reversed_amount", "attempts", "error"],
}


async def export_rows(session: AsyncSession, kind: str, since: datetime | None, until: datetime | None, limit: int = 50_000) -> list[list]:
    def span(column):
        return [c for c in (column >= since if since else None, column < until if until else None) if c is not None]

    iso = lambda d: d.isoformat() if d else ""  # noqa: E731
    if kind == "bookings":
        q = (select(Booking, User, HostProfile).join(User, User.id == Booking.guest_id).join(HostProfile, HostProfile.id == Booking.host_id).where(*span(Booking.requested_at))
             .order_by(Booking.requested_at).limit(limit))
        rows = (await session.execute(q)).all()
        host_users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_({h.user_id for _, _, h in rows})))).scalars()} if rows else {}
        props = {p.id: p.name for p in (await session.execute(select(Property).where(Property.id.in_({b.property_id for b, _, _ in rows})))).scalars()} if rows else {}
        units = {}
        if rows:
            from app.modules.properties.models import Unit

            units = {u.id: u.name for u in (await session.execute(select(Unit).where(Unit.id.in_({b.unit_id for b, _, _ in rows})))).scalars()}
        out = []
        for b, g, h in rows:
            p, hu = b.pricing_snapshot, host_users[h.user_id]
            out.append([b.reference, b.status, iso(b.requested_at), iso(b.confirmed_at), g.full_name, g.email, hu.full_name, hu.email, props[b.property_id], units[b.unit_id],
                        bookings.availability.iso_local(b.check_in, _zone(b)), bookings.availability.iso_local(b.check_out, _zone(b)), b.hours, b.guests, b.currency,
                        dollars(p["accommodation"]), dollars(p["cleaning_fee"]), dollars(p["service_fee"]), dollars(p["tax"]), dollars(p["total"]), dollars(p["commission"]),
                        dollars(p["host_payable"]), b.cancelled_by_role or "", b.closed_reason or ""])
        return out
    if kind == "payments":
        q = select(Payment, Booking, User).join(Booking, Booking.id == Payment.booking_id).join(User, User.id == Booking.guest_id).where(*span(Payment.created_at)).order_by(Payment.created_at).limit(limit)
        return [[str(p.id), b.reference, u.email, p.stripe_payment_intent_id, p.stripe_charge_id or "", dollars(p.amount), p.currency, p.status, p.attempt, p.failure_code or "", iso(p.created_at), iso(p.succeeded_at)]
                for p, b, u in (await session.execute(q)).all()]
    if kind == "refunds":
        q = select(Refund, Booking).join(Booking, Booking.id == Refund.booking_id).where(*span(Refund.created_at)).order_by(Refund.created_at).limit(limit)
        return [[str(r.id), b.reference, r.type, dollars(r.amount), b.currency, r.status, r.stripe_refund_id or "", dollars(r.host_reversal_amount), r.reason or "", r.error or "", iso(r.created_at)]
                for r, b in (await session.execute(q)).all()]
    if kind == "payouts":
        q = (select(Transfer, Booking, User).join(Booking, Booking.id == Transfer.booking_id).join(HostProfile, HostProfile.id == Transfer.host_id).join(User, User.id == HostProfile.user_id)
             .where(*span(Transfer.created_at)).order_by(Transfer.created_at).limit(limit))
        return [[b.reference, u.full_name, u.email, dollars(t.amount), t.currency, t.status, t.stripe_transfer_id or "", iso(t.eligible_at), iso(t.transferred_at), dollars(t.reversed_amount), t.attempts, t.error or ""]
                for t, b, u in (await session.execute(q)).all()]
    raise ValueError(kind)


def csv_safe(value) -> str:
    """Spreadsheets run text starting with = + - @ as a formula. Guest-supplied names must never do that."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def build_csv(header: list[str], rows: list[list]) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([csv_safe(c) for c in row])
    return buf.getvalue()
