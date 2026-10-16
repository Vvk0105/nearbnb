import asyncio
import os
import re
from urllib.parse import urlsplit, urlunsplit

# Must be set before the app is imported.
_base_url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://nearbnb:change-me@db:5432/nearbnb")
_parts = urlsplit(_base_url)
TEST_DB = "nearbnb_test"
os.environ["DATABASE_URL"] = urlunsplit(_parts._replace(path=f"/{TEST_DB}"))
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["EMAIL_KICK_ENABLED"] = "false"
os.environ["TURNSTILE_SECRET_KEY"] = ""
os.environ["APP_ENV"] = "test"
os.environ["STORAGE_ROOT"] = "/tmp/nearbnb-test-storage"

import asyncpg  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.legal import service as legal_service  # noqa: E402
from app.modules.notifications.models import EmailOutbox  # noqa: E402

PASSWORD = "Sup3r-secret-pass"


@pytest.fixture(scope="session", autouse=True)
def _database():
    asyncio.run(_setup_database())
    yield


async def _setup_database():
    admin_dsn = urlunsplit(_parts._replace(scheme="postgresql", path="/postgres"))
    conn = await asyncpg.connect(admin_dsn)
    if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB):
        await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    await conn.close()
    async with engine.begin() as c:
        await c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await c.run_sync(Base.metadata.drop_all)
        await c.run_sync(Base.metadata.create_all)
        # Same append-only guard the migration installs.
        await c.execute(text(
            "CREATE OR REPLACE FUNCTION audit_logs_block_mutation() RETURNS trigger AS $fn$ "
            "BEGIN RAISE EXCEPTION 'audit_logs is append-only'; END; $fn$ LANGUAGE plpgsql"
        ))
        await c.execute(text(
            "CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs "
            "FOR EACH ROW EXECUTE FUNCTION audit_logs_block_mutation()"
        ))


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables():
    async with engine.begin() as c:
        await c.execute(
            text("TRUNCATE users, user_social_links, user_consents, otp_codes, refresh_tokens, "
                 "audit_logs, settings, email_outbox, legal_documents RESTART IDENTITY CASCADE")
        )
    async with SessionLocal() as session:
        await legal_service.seed_placeholders(session)
    yield


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db():
    async with SessionLocal() as session:
        yield session


async def latest_code(email: str) -> str:
    """Reads the most recent one-time code emailed to `email` from the outbox."""
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(EmailOutbox).where(EmailOutbox.to_email == email).order_by(EmailOutbox.created_at.desc()).limit(1)
            )
        ).scalar_one()
    match = re.search(r"\b(\d{6})\b", row.body_text or "")
    assert match, f"no code in email: {row.body_text!r}"
    return match.group(1)


def csrf_headers(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": token} if token else {}


async def register_and_verify(client: AsyncClient, email: str = "guest@example.com", password: str = PASSWORD):
    r = await client.post("/api/v1/auth/register", json={
        "email": email, "password": password, "full_name": "Test Guest", "accept_terms": True,
    })
    assert r.status_code == 202, r.text
    code = await latest_code(email)
    r = await client.post("/api/v1/auth/verify-email", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def make_admin():
    from datetime import UTC, datetime

    from app.core.security import hash_password
    from app.modules.users.models import USER_ACTIVE, User

    async def _make(email: str = "admin@example.com", password: str = PASSWORD) -> User:
        async with SessionLocal() as session:
            user = User(email=email, full_name="Admin", password_hash=hash_password(password), is_admin=True,
                        status=USER_ACTIVE, email_verified_at=datetime.now(UTC))
            session.add(user)
            await session.commit()
            return user

    return _make
