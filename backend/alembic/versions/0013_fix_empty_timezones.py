"""repair properties saved with an empty timezone (the host had cleared the box): set it from the state

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-31 18:00:00.000000
"""
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

# Same table as AU_TIMEZONES in app/modules/properties/service.py (copied: a migration must not change if that code does).
FIX = """
UPDATE properties SET timezone = CASE
    WHEN upper(coalesce(country, 'AU')) <> 'AU' THEN 'UTC'
    WHEN upper(trim(state)) IN ('NSW', 'ACT') THEN 'Australia/Sydney'
    WHEN upper(trim(state)) = 'VIC' THEN 'Australia/Melbourne'
    WHEN upper(trim(state)) = 'TAS' THEN 'Australia/Hobart'
    WHEN upper(trim(state)) = 'QLD' THEN 'Australia/Brisbane'
    WHEN upper(trim(state)) = 'SA' THEN 'Australia/Adelaide'
    WHEN upper(trim(state)) = 'WA' THEN 'Australia/Perth'
    WHEN upper(trim(state)) = 'NT' THEN 'Australia/Darwin'
    ELSE 'Australia/Sydney'
END
WHERE timezone IS NULL OR trim(timezone) = ''
"""


def upgrade() -> None:
    op.execute(FIX)
    # A change waiting for admin review could still carry the empty value: drop just that field from it.
    op.execute("""
        UPDATE listing_revisions SET data = data - 'timezone'
        WHERE status = 'pending' AND data ? 'timezone' AND trim(coalesce(data->>'timezone', '')) = ''
    """)


def downgrade() -> None:
    pass  # nothing to undo: an empty timezone was never valid
