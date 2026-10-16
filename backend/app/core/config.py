from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_secret_key: str = "change-me"
    app_base_url: str = "http://localhost:8080"

    database_url: str = "postgresql+asyncpg://nearbnb:change-me@db:5432/nearbnb"
    redis_url: str = "redis://redis:6379/0"

    smtp_host: str = ""
    smtp_port: int = 465
    smtp_use_ssl: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = "NearBNB <noreply@nearbnb.com>"
    email_reply_to: str = ""

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    storage_root: str = "/data/storage"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
