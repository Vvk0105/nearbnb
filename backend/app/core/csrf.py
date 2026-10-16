import hmac

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.cookies import ACCESS_COOKIE, CSRF_COOKIE

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def csrf_middleware(request: Request, call_next):
    """Double-submit CSRF check for cookie-authenticated unsafe requests.

    Requests without a session cookie are not exposed to CSRF (nothing to ride), so they pass
    (register, login, webhooks). Requests that carry the session cookie must echo the CSRF cookie
    in the X-CSRF-Token header."""
    if request.method not in SAFE_METHODS and request.cookies.get(ACCESS_COOKIE):
        header = request.headers.get("x-csrf-token")
        cookie = request.cookies.get(CSRF_COOKIE)
        if not header or not cookie or not hmac.compare_digest(header, cookie):
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "CSRF_FAILED", "message": "Missing or invalid CSRF token"}},
            )
    return await call_next(request)
