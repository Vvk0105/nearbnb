from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_secret_key: str = "change-me"
    app_base_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"

    database_url: str = "postgresql+asyncpg://nearbnb:change-me@db:5432/nearbnb"
    redis_url: str = "redis://redis:6379/0"

    # Auth
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    cookie_domain: str = ""
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 60
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    password_min_length: int = 10

    # Abuse protection
    rate_limit_enabled: bool = True
    turnstile_secret_key: str = ""

    # Email
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_use_ssl: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = "NearBNB <noreply@nearbnb.com>"
    email_reply_to: str = ""
    email_kick_enabled: bool = True

    google_places_api_key: str = ""
    # Push notifications to the mobile app: the Firebase service-account JSON file, base64-encoded on one line
    # (`base64 -w0 key.json`). Empty = no pushes; the in-app list and emails work as before.
    firebase_service_account_b64: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""  # safe to show in the browser (pk_test_... / pk_live_...)

    storage_root: str = "/data/storage"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
