"""enable required postgres extensions

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")  # room_occupancy overlap constraint
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")    # gen_random_uuid()


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
    op.execute("DROP EXTENSION IF EXISTS postgis")
