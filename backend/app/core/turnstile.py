import logging

import httpx

from app.core.config import get_settings
from app.core.errors import AppError

log = logging.getLogger(__name__)
VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile(token: str | None, ip: str | None, *, mobile: bool = False) -> None:
    """No-op when TURNSTILE_SECRET_KEY is empty (development), and for the mobile app, which relies on rate limits."""
    secret = get_settings().turnstile_secret_key
    if not secret or mobile:
        return
    if not token:
        raise AppError(400, "CAPTCHA_REQUIRED", "Please complete the CAPTCHA.")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(VERIFY_URL, data={"secret": secret, "response": token, "remoteip": ip})
        ok = resp.json().get("success") is True
    except Exception:  # noqa: BLE001
        log.exception("turnstile verification failed")
        raise AppError(503, "CAPTCHA_UNAVAILABLE", "CAPTCHA check unavailable, try again.")
    if not ok:
        raise AppError(400, "CAPTCHA_FAILED", "CAPTCHA verification failed.")
