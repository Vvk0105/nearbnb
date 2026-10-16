import io
import re
from datetime import UTC, date, datetime
from urllib.parse import urlparse, urlunparse

from PIL import Image, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.storage import get_storage
from app.modules.legal import service as legal
from app.modules.settings import service as settings_service
from app.modules.users.models import User

PLATFORM_NAMES = {
    "instagram.com": "Instagram", "facebook.com": "Facebook", "linkedin.com": "LinkedIn",
    "x.com": "X", "twitter.com": "X",
}
MAX_SOCIAL_LINKS = 5
MAX_AVATAR_BYTES = 5 * 1024 * 1024
PHONE_RE = re.compile(r"^\+?[0-9 ()-]{8,20}$")


def photo_url(user: User) -> str | None:
    return f"/api/v1/media/{user.photo_path}" if user.photo_path else None


async def serialize_user(session: AsyncSession, user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "phone": user.phone,
        "dob": user.dob.isoformat() if user.dob else None,
        "photo_url": photo_url(user),
        "status": user.status,
        "email_verified": user.email_verified_at is not None,
        "is_admin": user.is_admin,
        "totp_enabled": user.totp_enabled,
        "pending_email": user.pending_email,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "social_links": [
            {"id": str(link.id), "platform": link.platform, "url": link.url} for link in user.social_links
        ],
        "pending_consents": await legal.pending_consents(session, user),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def validate_phone(phone: str) -> str:
    phone = phone.strip()
    if not PHONE_RE.match(phone):
        raise AppError(422, "INVALID_PHONE", "Enter a valid phone number, e.g. +61 400 000 000")
    return phone


async def validate_dob(session: AsyncSession, dob: date) -> date:
    today = datetime.now(UTC).date()
    min_age = await settings_service.get_value(session, "guest.min_age")
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if dob > today or age > 120:
        raise AppError(422, "INVALID_DOB", "Enter a valid date of birth")
    if age < min_age:
        raise AppError(422, "UNDERAGE", f"You must be at least {min_age} years old")
    return dob


async def validate_social_url(session: AsyncSession, raw: str) -> tuple[str, str]:
    """Returns (platform, normalised_url). Checks format and the admin-configured domain list only;
    ownership of the profile is not verified."""
    allowed = await settings_service.get_value(session, "guest.allowed_social_domains")
    raw = raw.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if parsed.scheme not in ("http", "https") or not host:
        raise AppError(422, "SOCIAL_URL_INVALID", "Enter a valid profile link")
    domain = next((d for d in allowed if host == d or host.endswith("." + d)), None)
    if domain is None:
        raise AppError(422, "SOCIAL_DOMAIN_NOT_ALLOWED", "Use a profile link from: " + ", ".join(allowed))
    if not parsed.path.strip("/") and not parsed.query:
        raise AppError(422, "SOCIAL_URL_INVALID", "Enter the link to your profile, not just the website home page")
    normalised = urlunparse(("https", host, parsed.path.rstrip("/"), "", parsed.query, ""))
    return PLATFORM_NAMES.get(domain, domain), normalised


def process_avatar(data: bytes) -> bytes:
    """Validates it is a real image, strips metadata, resizes, and re-encodes as WebP."""
    if len(data) > MAX_AVATAR_BYTES:
        raise AppError(413, "FILE_TOO_LARGE", "Photo must be 5 MB or smaller")
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))
        if image.format not in ("JPEG", "PNG", "WEBP"):
            raise AppError(415, "UNSUPPORTED_FILE", "Use a JPEG, PNG or WebP image")
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise AppError(415, "UNSUPPORTED_FILE", "That file is not a valid image") from exc
    image.thumbnail((512, 512))
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=85)
    return out.getvalue()


def save_avatar(user: User, data: bytes) -> None:
    path = f"public/avatars/{user.id}.webp"
    get_storage().save(path, process_avatar(data))
    user.photo_path = path
