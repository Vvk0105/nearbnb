import logging

from fastapi import APIRouter, Depends, FastAPI
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
from app.modules.hosts.admin_router import router as admin_hosts_router
from app.modules.hosts.router import router as host_router
from app.modules.legal.router import router as legal_router
from app.modules.media.router import router as media_router
from app.modules.payments.router import router as webhooks_router
from app.modules.places.router import router as places_router
from app.modules.pricing.admin_router import router as admin_tax_router
from app.modules.properties.admin_router import router as admin_listings_router
from app.modules.properties.router import router as listings_router
from app.modules.search.router import router as search_router
from app.modules.users.admin_router import router as admin_users_router
from app.modules.users.router import router as me_router

settings = get_settings()

if settings.is_production and not settings.turnstile_secret_key:
    logging.getLogger(__name__).warning("TURNSTILE_SECRET_KEY is empty in production: CAPTCHA checks are DISABLED")

app = FastAPI(
    title="NearBNB API",
    version="0.8.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_exception_handler(AppError, app_error_handler)
app.middleware("http")(csrf_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

api = APIRouter(prefix="/api/v1")


@api.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}


api.include_router(auth_router)
api.include_router(me_router)
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
api.include_router(dashboards_router)
api.include_router(admin_users_router, dependencies=[Depends(require_admin)])
api.include_router(cancellations_router)
api.include_router(cancellation_templates_router)
app.include_router(api)
