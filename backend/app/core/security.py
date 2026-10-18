import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.errors import AppError

_hasher = PasswordHasher()
# Verified against when the user does not exist so response time does not reveal valid emails.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str) -> str:
    key = get_settings().app_secret_key.encode()
    return hmac.new(key, code.encode(), hashlib.sha256).hexdigest()


def codes_match(code: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_code(code), code_hash)


def generate_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, get_settings().app_secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AppError(401, "NOT_AUTHENTICATED", "Session expired or invalid") from exc
    if payload.get("typ") != "access":
        raise AppError(401, "NOT_AUTHENTICATED", "Invalid token")
    return payload


COMMON_PASSWORDS = {
    "password", "password1", "password12", "password123", "password1234", "passw0rd123", "p@ssword123",
    "qwertyuiop", "qwerty12345", "qwerty123456", "1234567890", "12345678910", "123456789012", "1q2w3e4r5t",
    "iloveyou123", "welcome123", "welcome1234", "admin12345", "administrator", "letmein1234", "changeme123",
    "abcdefghij", "abc1234567", "nearbnb1234", "nearbnb123",
}


def password_problem(password: str, email: str = "", minimum: int = 10) -> str | None:
    """Returns what is wrong with a password, or None when it is acceptable. One rule for sign-up, reset, change and the admin CLI."""
    if len(password) < minimum:
        return f"Password must be at least {minimum} characters"
    if len(password) > 128:
        return "Password must be at most 128 characters"
    if not (any(c.islower() for c in password) and any(c.isupper() for c in password) and any(c.isdigit() for c in password)):
        return "Password needs a lowercase letter, an uppercase letter and a number"
    squashed = "".join(c for c in password.lower() if c.isalnum())
    if squashed in COMMON_PASSWORDS or len(set(password)) < 5:
        return "That password is too common or too repetitive. Choose something less guessable"
    local = email.lower().split("@")[0]
    if email and (password.lower() == email.lower() or (len(local) >= 4 and local in password.lower())):
        return "Password cannot contain your email name"
    return None
