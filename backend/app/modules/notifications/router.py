import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user
from app.core.errors import AppError
from app.core.ratelimit import rate_limit
from app.modules.notifications import inbox, push
from app.modules.users.models import User

router = APIRouter(prefix="/me/notifications", tags=["notifications"])


class DeviceIn(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    platform: str = Field(pattern="^(android|ios)$")


class DeviceOut(BaseModel):
    token: str = Field(min_length=20, max_length=512)


# The mobile app registers its Firebase token after sign-in, and removes it before signing out.
# Declared before "/{notification_id}/read" so "devices" is never read as a notification id.
@router.post("/devices", dependencies=[Depends(rate_limit("device_register", 30, 3600))])
async def register_device(body: DeviceIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await push.register_device(session, user.id, body.token, body.platform)
    await session.commit()
    return {"ok": True}


@router.post("/devices/remove")
async def remove_device(body: DeviceOut, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await push.unregister_device(session, user.id, body.token)
    await session.commit()
    return {"ok": True}


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
