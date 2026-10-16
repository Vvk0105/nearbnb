import logging

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.redis import get_redis

log = logging.getLogger(__name__)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def hit(key: str, limit: int, window_seconds: int) -> None:
    """Fixed-window counter. Fails open if Redis is unavailable (logged)."""
    if not get_settings().rate_limit_enabled:
        return
    try:
        redis = get_redis()
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_seconds)
    except Exception:  # noqa: BLE001
        log.exception("rate limiter unavailable, allowing request")
        return
    if count > limit:
        raise AppError(429, "RATE_LIMITED", "Too many attempts. Please wait and try again.")


def rate_limit(name: str, limit: int, window_seconds: int):
    async def dependency(request: Request) -> None:
        await hit(f"rl:{name}:{client_ip(request)}", limit, window_seconds)

    return dependency


async def cooldown(key: str, seconds: int) -> None:
    """Allow one action per `seconds` for this key (used for OTP resend)."""
    if not get_settings().rate_limit_enabled:
        return
    try:
        ok = await get_redis().set(f"cd:{key}", "1", ex=seconds, nx=True)
    except Exception:  # noqa: BLE001
        log.exception("cooldown unavailable, allowing request")
        return
    if not ok:
        raise AppError(429, "RESEND_TOO_SOON", f"Please wait {seconds} seconds before requesting another code.")
