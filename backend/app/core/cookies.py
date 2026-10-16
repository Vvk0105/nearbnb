from fastapi import Response

from app.core.config import get_settings

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
REFRESH_PATH = "/api/v1/auth"


def _common() -> dict:
    settings = get_settings()
    return {
        "secure": settings.is_production,
        "samesite": "lax",
        "domain": settings.cookie_domain or None,
    }


def set_session_cookies(response: Response, access: str, refresh: str, csrf: str) -> None:
    settings = get_settings()
    response.set_cookie(ACCESS_COOKIE, access, max_age=settings.access_token_minutes * 60, httponly=True, path="/", **_common())
    response.set_cookie(
        REFRESH_COOKIE, refresh, max_age=settings.refresh_token_days * 86400, httponly=True, path=REFRESH_PATH, **_common()
    )
    # Readable by the frontend so it can echo the value in the X-CSRF-Token header (double-submit).
    response.set_cookie(CSRF_COOKIE, csrf, max_age=settings.refresh_token_days * 86400, httponly=False, path="/", **_common())


def clear_session_cookies(response: Response) -> None:
    domain = get_settings().cookie_domain or None
    response.delete_cookie(ACCESS_COOKIE, path="/", domain=domain)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH, domain=domain)
    response.delete_cookie(CSRF_COOKIE, path="/", domain=domain)
