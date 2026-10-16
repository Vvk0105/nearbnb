from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.core.errors import AppError
from app.core.storage import LocalStorage
from app.core.config import get_settings

router = APIRouter(prefix="/media", tags=["media"])

IMAGE_EXT = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


@router.get("/public/{path:path}")
async def public_file(path: str):
    """Serves only files under STORAGE_ROOT/public. Private files (ID documents) are never served here."""
    storage = LocalStorage(get_settings().storage_root)
    try:
        full = storage.resolve(f"public/{path}")
    except ValueError:
        raise AppError(404, "NOT_FOUND") from None
    media_type = IMAGE_EXT.get(full.suffix.lower())
    if media_type is None or not full.is_file():
        raise AppError(404, "NOT_FOUND")
    return FileResponse(full, media_type=media_type, headers={"Cache-Control": "public, max-age=3600"})
