import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user
from app.core.errors import AppError
from app.modules.notifications import inbox
from app.modules.users.models import User

router = APIRouter(prefix="/me/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(page: int = Query(1, ge=1, le=1000), page_size: int = Query(20, ge=1, le=100), unread: bool = False,
                             user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return await inbox.list_for(session, user.id, page=page, page_size=page_size, unread_only=unread)


@router.get("/unread-count")
async def unread_count(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """Cheap enough for the bell to ask every minute."""
    return {"unread": await inbox.unread_count(session, user.id)}


@router.post("/read-all")
async def read_all(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    changed = await inbox.mark_all_read(session, user.id)
    await session.commit()
    return {"marked": changed, "unread": 0}


@router.post("/{notification_id}/read")
async def read_one(notification_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    try:
        nid = uuid.UUID(notification_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "Notification not found.") from None
    await inbox.mark_read(session, user.id, nid)
    await session.commit()
    return {"unread": await inbox.unread_count(session, user.id)}
