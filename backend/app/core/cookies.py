from fastapi import Request, Response

from app.core.config import get_settings

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
REFRESH_PATH = "/api/v1/auth"
# The mobile app has no cookie jar we trust: it says so with this header, gets its tokens in the response body and
# sends them back as "Authorization: Bearer <access>" and "X-Refresh-Token: <refresh>". The website is unchanged.
MOBILE_HEADER = "x-client"


def is_mobile(request: Request) -> bool:
    return request.headers.get(MOBILE_HEADER, "").lower() == "mobile"


def access_token_from(request: Request) -> str | None:
    """The cookie (website) or the bearer token (mobile app)."""
    if token := request.cookies.get(ACCESS_COOKIE):
        return token
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


def refresh_token_from(request: Request) -> str | None:
    return request.cookies.get(REFRESH_COOKIE) or request.headers.get("x-refresh-token") or None


def deliver_session(request: Request, response: Response, access: str, refresh: str, csrf: str) -> dict | None:
    """Website: sets the cookies and returns None. Mobile app: returns the tokens for the response body instead.
    No CSRF token for the app: CSRF rides on cookies the browser sends by itself, and the app sends none."""
    if is_mobile(request):
        return {"access_token": access, "refresh_token": refresh, "token_type": "bearer",
                "expires_in": get_settings().access_token_minutes * 60}
    set_session_cookies(response, access, refresh, csrf)
    return None


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
