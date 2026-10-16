from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session

settings = get_settings()

app = FastAPI(
    title="NearBNB API",
    version="0.1.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
)

api = APIRouter(prefix="/api/v1")


@api.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}


app.include_router(api)


# nginx strips the /api prefix in production, so expose the same routes at the root too.
@app.get("/v1/health", include_in_schema=False)
async def health_via_proxy(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}
