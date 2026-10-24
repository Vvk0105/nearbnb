import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user, require_admin
from app.core.errors import AppError
from app.core.ratelimit import client_ip
from app.modules.audit import service as audit
from app.modules.dashboards import service
from app.modules.hosts import service as host_service
from app.modules.payments.models import StripeEvent
from app.modules.payments.router import dispatch_event
from app.modules.users.models import User

router = APIRouter(tags=["dashboards"])
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
DAY = r"^\d{4}-\d{2}-\d{2}$"


def _csv(name: str, header: list[str], rows: list[list]) -> Response:
    body = "﻿" + service.build_csv(header, rows)  # the byte-order mark makes Excel read accents and symbols correctly
    return Response(body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def _day(value: str | None, *, end: bool = False):
    try:
        return service.parse_day(value, end=end)
    except ValueError:
        raise AppError(422, "INVALID_DATE", "Use the format YYYY-MM-DD.") from None


# ---------- host ----------

@router.get("/host/dashboard")
async def host_dashboard(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    return await service.host_dashboard(session, host)


@router.get("/host/money")
async def host_money(month: str | None = Query(None, pattern=MONTH), page: int = Query(1, ge=1, le=500), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    return await service.host_money(session, host, month, page, 25)


@router.get("/host/money/export.csv")
async def host_money_export(month: str | None = Query(None, pattern=MONTH), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    host = await host_service.require_host(session, user)
    since = until = None
    if month:
        y, m = (int(x) for x in month.split("-"))
        since, until = datetime(y, m, 1, tzinfo=UTC), datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=UTC)
    rows = await service.host_money_rows(session, host, since=since, until=until)
    return _csv(f"nearbnb-earnings{'-' + month if month else ''}.csv", service.MONEY_COLUMNS, service.host_money_csv_rows(rows))


# ---------- admin ----------

@router.get("/admin/dashboard")
async def admin_dashboard(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await service.admin_dashboard(session)


def _revenue_range(date_from: str | None, date_to: str | None) -> tuple[date, date]:
    """Defaults to the last 30 days (today included)."""
    today = datetime.now(UTC).date()
    try:
        last = date.fromisoformat(date_to) if date_to else today
        first = date.fromisoformat(date_from) if date_from else last - timedelta(days=29)
    except ValueError:
        raise AppError(422, "INVALID_DATE", "Use the format YYYY-MM-DD.") from None
    return first, last


def _host_filter(host_id: str | None):
    if not host_id:
        return None
    try:
        return uuid.UUID(host_id)
    except ValueError:
        raise AppError(422, "INVALID_HOST", "Unknown host.") from None


async def _revenue(session: AsyncSession, date_from: str | None, date_to: str | None, host_id: str | None):
    first, last = _revenue_range(date_from, date_to)
    try:
        return first, last, await service.admin_revenue(session, first, last, _host_filter(host_id))
    except ValueError as exc:
        raise AppError(422, "INVALID_RANGE", str(exc)) from None


@router.get("/admin/revenue")
async def admin_revenue(date_from: str | None = Query(None, alias="from", pattern=DAY), date_to: str | None = Query(None, alias="to", pattern=DAY), host_id: str | None = Query(None),
                        _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return (await _revenue(session, date_from, date_to, host_id))[2]


@router.get("/admin/revenue.csv")
async def admin_revenue_csv(request: Request, date_from: str | None = Query(None, alias="from", pattern=DAY), date_to: str | None = Query(None, alias="to", pattern=DAY),
                            host_id: str | None = Query(None), by: str = Query("day", pattern="^(day|host)$"),
                            admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    """`by=day` (default): one row per day. `by=host`: one row per host."""
    first, last, data = await _revenue(session, date_from, date_to, host_id)
    if by == "host":
        header = ["host", "email", "bookings", "booking_value", "service_fees", "commission", "platform_revenue", "host_earnings", "paid_out"]
        rows = [[h["host"], h["email"], h["bookings"], *(service.dollars(h[k]) for k in header[3:])] for h in data["by_host"]]
    else:
        header = ["date", "guest_payments", "refunds", "platform_revenue", "bookings_confirmed"]
        rows = [[d["date"], service.dollars(d["paid"]), service.dollars(d["refunded"]), service.dollars(d["platform_revenue"]), d["bookings"]] for d in data["series"]]
    audit.record(session, action="export.downloaded", entity_type="export", entity_id="revenue", actor_id=admin.id, ip=client_ip(request),
                 after={"rows": len(rows), "from": first.isoformat(), "to": last.isoformat(), "by": by, "host_id": host_id})
    await session.commit()
    return _csv(f"nearbnb-revenue-by-{by}-{first.isoformat()}-to-{last.isoformat()}.csv", header, rows)


@router.get("/admin/payments")
async def admin_payments(status: str | None = Query(None, pattern="^(created|processing|succeeded|failed|cancelled)$"), q: str | None = Query(None, max_length=100),
                         page: int = Query(1, ge=1, le=1000), _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await service.admin_payments(session, status, q, page, 25)


@router.get("/admin/payouts")
async def admin_payouts(state: str | None = Query(None, pattern="^(scheduled|held|paid|failed|reversed)$"), page: int = Query(1, ge=1, le=1000),
                        _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await service.admin_payouts(session, state, page, 25)


@router.get("/admin/webhooks")
async def admin_webhooks(failed: bool = True, page: int = Query(1, ge=1, le=1000), _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await service.admin_webhooks(session, failed, page, 25)


@router.post("/admin/webhooks/{event_id}/reprocess")
async def reprocess_webhook(event_id: str, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    """Runs a stored Stripe event again, for example after fixing the cause of a failure. Handlers are idempotent."""
    import uuid

    try:
        eid = uuid.UUID(event_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Event not found.") from None
    row = (await session.execute(select(StripeEvent).where(StripeEvent.id == eid).with_for_update())).scalar_one_or_none()
    if row is None:
        raise AppError(404, "NOT_FOUND", "Event not found.")
    try:
        async with session.begin_nested():
            await dispatch_event(session, row.payload)
        row.processed_at, row.error = datetime.now(UTC), None
    except Exception as exc:  # noqa: BLE001
        row.error = str(exc)[:500]
    audit.record(session, action="webhook.reprocessed", entity_type="stripe_event", entity_id=row.event_id, actor_id=admin.id, ip=client_ip(request), after={"ok": row.error is None})
    await session.commit()
    return {"id": str(row.id), "processed": row.processed_at is not None, "error": row.error}


@router.get("/admin/bookings/{booking_id}")
async def admin_booking_detail(booking_id: str, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    await service.bookings.expire_due(session)
    await session.commit()
    return await service.admin_booking_detail(session, booking_id)


@router.get("/admin/exports/{kind}.csv")
async def admin_export(kind: str, request: Request, date_from: str | None = Query(None, alias="from", pattern=DAY), date_to: str | None = Query(None, alias="to", pattern=DAY),
                       admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if kind not in service.EXPORTS:
        raise AppError(404, "NOT_FOUND", "Unknown export.")
    rows = await service.export_rows(session, kind, _day(date_from), _day(date_to, end=True))
    audit.record(session, action="export.downloaded", entity_type="export", entity_id=kind, actor_id=admin.id, ip=client_ip(request), after={"rows": len(rows), "from": date_from, "to": date_to})
    await session.commit()
    return _csv(f"nearbnb-{kind}{'-' + date_from if date_from else ''}{'-to-' + date_to if date_to else ''}.csv", service.EXPORTS[kind], rows)
