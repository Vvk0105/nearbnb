from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user, require_admin
from app.core.ratelimit import client_ip, rate_limit
from app.modules.hosts import service as host_service
from app.modules.issues import service
from app.modules.issues.models import CATEGORIES
from app.modules.users.models import User

router = APIRouter(tags=["issues"])


class IssueIn(BaseModel):
    category: str = Field(max_length=30)
    body: str = Field(max_length=service.MAX_BODY + 500)


class IssuePatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(in_progress|resolved|dismissed)$")
    admin_note: str | None = Field(default=None, max_length=3000)
    resolution: str | None = Field(default=None, max_length=3000)
    blocks_payout: bool | None = None


@router.get("/meta/issue-categories")
async def categories():
    return {role: list(items) for role, items in CATEGORIES.items()}


@router.post("/me/bookings/{booking_id}/issues", status_code=201, dependencies=[Depends(rate_limit("issue", 20, 3600))])
async def guest_issue(booking_id: str, body: IssueIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    issue = await service.report(session, user, booking_id, "guest", body.category, body.body, client_ip(request))
    await session.commit()
    from app.modules.notifications.service import kick_outbox

    kick_outbox()
    return service.reporter_json(issue)


@router.post("/host/bookings/{booking_id}/issues", status_code=201, dependencies=[Depends(rate_limit("issue", 20, 3600))])
async def host_issue(booking_id: str, body: IssueIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await host_service.require_host(session, user)
    issue = await service.report(session, user, booking_id, "host", body.category, body.body, client_ip(request))
    await session.commit()
    from app.modules.notifications.service import kick_outbox

    kick_outbox()
    return service.reporter_json(issue)


@router.get("/me/issues")
async def my_issues(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """Problems this person reported, as guest or as host. The admin's internal notes are never included."""
    return await service.mine(session, user)


@router.get("/admin/issues")
async def admin_issues(status: str | None = Query(None, pattern="^(open|in_progress|resolved|dismissed|open_work)$"), q: str | None = Query(None, max_length=100),
                       page: int = Query(1, ge=1, le=1000), _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await service.admin_list(session, status=status, q=q, page=page)


@router.patch("/admin/issues/{issue_id}")
async def update_issue(issue_id: str, body: IssuePatch, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    issue = await service.update(session, admin, issue_id, status=body.status, admin_note=body.admin_note, resolution=body.resolution,
                                 blocks_payout=body.blocks_payout, ip=client_ip(request))
    await session.commit()
    from app.modules.notifications.service import kick_outbox

    kick_outbox()
    return await service.admin_json(session, issue)
