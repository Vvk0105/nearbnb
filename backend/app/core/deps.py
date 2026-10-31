import uuid

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cookies import access_token_from
from app.core.db import get_session
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.modules.users.models import USER_ACTIVE, USER_SUSPENDED, User


async def get_current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    token = access_token_from(request)
    if not token:
        raise AppError(401, "NOT_AUTHENTICATED", "Please log in")
    payload = decode_access_token(token)
    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None:
        raise AppError(401, "NOT_AUTHENTICATED", "Please log in")
    if user.status == USER_SUSPENDED:
        raise AppError(403, "ACCOUNT_SUSPENDED", "Your account has been suspended")
    if user.status != USER_ACTIVE:
        raise AppError(401, "NOT_AUTHENTICATED", "Please log in")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise AppError(403, "FORBIDDEN", "Administrator access required")
    return user
