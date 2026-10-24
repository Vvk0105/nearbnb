import logging

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as _models  # noqa: F401  (register all tables)
from app.core.config import get_settings
from app.core.csrf import csrf_middleware
from app.core.db import get_session
from app.core.deps import require_admin
from app.core.errors import AppError, app_error_handler
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.bookings.router import router as bookings_router
from app.modules.dashboards.router import router as dashboards_router
from app.modules.cancellations.admin_router import router as cancellation_templates_router
from app.modules.cancellations.router import router as cancellations_router
from app.modules.ical.router import router as ical_router
from app.modules.issues.router import router as issues_router
from app.modules.hosts.admin_router import router as admin_hosts_router
from app.modules.hosts.router import router as host_router
from app.modules.legal.router import router as legal_router
from app.modules.notifications.router import router as notifications_router
from app.modules.media.router import router as media_router
from app.modules.payments.router import router as webhooks_router
from app.modules.places.router import router as places_router
from app.modules.pricing.admin_router import router as admin_tax_router
from app.modules.properties.admin_router import router as admin_listings_router
from app.modules.properties.router import router as listings_router
from app.modules.reviews.router import router as reviews_router
from app.modules.search.router import router as search_router
from app.modules.users.admin_router import router as admin_users_router
from app.modules.users.router import router as me_router

settings = get_settings()

if settings.is_production and not settings.turnstile_secret_key:
    logging.getLogger(__name__).warning("TURNSTILE_SECRET_KEY is empty in production: CAPTCHA checks are DISABLED")

app = FastAPI(
    title="NearBNB API",
    version="1.0.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_exception_handler(AppError, app_error_handler)
app.middleware("http")(csrf_middleware)


DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
DOCS_CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; connect-src 'self'; frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Safe defaults on every answer. The API serves JSON, files and calendars, never pages, so framing and sniffing are off."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path in DOCS_PATHS:
        # The Swagger page (development only: it is switched off in production) loads its files from a CDN and runs a small inline script.
        response.headers["Content-Security-Policy"] = DOCS_CSP
    else:
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
    # Private answers must never sit in a shared cache. Public images, feeds and docs set their own caching.
    if request.url.path.startswith("/api/v1") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

api = APIRouter(prefix="/api/v1")


# HEAD is allowed too: uptime monitors (UptimeRobot and others) check with HEAD, and would otherwise get 405.
@api.api_route("/health", methods=["GET", "HEAD"])
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}


@api.api_route("/health/ready", methods=["GET", "HEAD"])
async def ready(session: AsyncSession = Depends(get_session)):
    """Deeper check for monitors: database, Redis and whether the database is on the latest migration."""
    from fastapi.responses import JSONResponse

    from app.core.redis import get_redis

    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "down"
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "down"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "degraded", **checks})


api.include_router(auth_router)
api.include_router(me_router)
api.include_router(notifications_router)
api.include_router(legal_router)
api.include_router(media_router)
api.include_router(admin_router)
api.include_router(admin_hosts_router)
api.include_router(admin_listings_router)
api.include_router(host_router)
api.include_router(listings_router)
api.include_router(webhooks_router)
api.include_router(search_router)
api.include_router(places_router)
api.include_router(admin_tax_router)
api.include_router(bookings_router)
api.include_router(reviews_router)
api.include_router(issues_router)
api.include_router(ical_router)
api.include_router(dashboards_router)
api.include_router(admin_users_router, dependencies=[Depends(require_admin)])
api.include_router(cancellations_router)
api.include_router(cancellation_templates_router)
app.include_router(api)
