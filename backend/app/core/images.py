import io

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import AppError

ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
Image.MAX_IMAGE_PIXELS = 60_000_000  # refuse decompression bombs


def process_photo(data: bytes, *, max_bytes: int, full_px: int = 1600, thumb_px: int = 480) -> tuple[bytes, bytes]:
    """Validates a real image, fixes orientation, strips metadata and returns (full.webp, thumb.webp)."""
    if not data:
        raise AppError(422, "EMPTY_FILE", "The file is empty.")
    if len(data) > max_bytes:
        raise AppError(413, "FILE_TOO_LARGE", f"Photos must be {max_bytes // (1024 * 1024)} MB or smaller.")
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        image = Image.open(io.BytesIO(data))
        if image.format not in ALLOWED_FORMATS:
            raise AppError(415, "UNSUPPORTED_FILE", "Use a JPEG, PNG or WebP image.")
        image = ImageOps.exif_transpose(image).convert("RGB")
    except AppError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise AppError(415, "UNSUPPORTED_FILE", "That file is not a valid image.") from exc

    def encode(max_px: int, quality: int) -> bytes:
        copy = image.copy()
        copy.thumbnail((max_px, max_px))
        out = io.BytesIO()
        copy.save(out, format="WEBP", quality=quality)
        return out.getvalue()

    return encode(full_px, 82), encode(thumb_px, 75)
