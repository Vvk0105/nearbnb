from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as _models  # noqa: F401  (register all tables)
from app.core.config import get_settings
from app.core.csrf import csrf_middleware
from app.core.db import get_session
from app.core.errors import AppError, app_error_handler
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.legal.router import router as legal_router
from app.modules.media.router import router as media_router
from app.modules.users.router import router as me_router

settings = get_settings()

app = FastAPI(
    title="NearBNB API",
    version="0.2.0",
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
app.include_router(api)
