import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_unit(session: AsyncSession, unit_id: uuid.UUID) -> None:
    """Serialises everything that reserves rooms of one unit (guest requests, host blocks) until the transaction ends.

    The overlap constraint alone is not enough: two transactions inserting overlapping rows at the same moment can
    deadlock on it instead of one cleanly losing. Taking this lock first makes the second one wait, then see the
    first one's row and answer "not available"."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"unit:{unit_id}"})
