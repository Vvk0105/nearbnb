import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.core.db import task_session
from app.jobs.celery_app import celery_app
from app.modules.auth.models import OtpCode, RefreshToken
from app.modules.notifications.service import process_outbox


async def _send_pending() -> int:
    async with task_session() as session:
        return await process_outbox(session)


@celery_app.task(name="emails.send_pending")
def send_pending_emails() -> int:
    return asyncio.run(_send_pending())


async def _cleanup() -> dict[str, int]:
    cutoff = datetime.now(UTC) - timedelta(days=1)
    async with task_session() as session:
        otp = await session.execute(delete(OtpCode).where(OtpCode.expires_at < cutoff))
        tokens = await session.execute(delete(RefreshToken).where(RefreshToken.expires_at < cutoff))
        await session.commit()
        return {"otp": otp.rowcount, "refresh_tokens": tokens.rowcount}


@celery_app.task(name="auth.cleanup")
def cleanup_auth_data() -> dict[str, int]:
    return asyncio.run(_cleanup())
