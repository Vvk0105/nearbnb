"""unit timing: flexible or fixed check-in and check-out

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-25 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every existing unit stays flexible, exactly as it works today.
    op.add_column('units', sa.Column('stay_mode', sa.String(length=10), server_default='flexible', nullable=False))
    op.add_column('units', sa.Column('fixed_checkin_time', sa.Time(), nullable=True))
    op.add_column('units', sa.Column('fixed_checkout_time', sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column('units', 'fixed_checkout_time')
    op.drop_column('units', 'fixed_checkin_time')
    op.drop_column('units', 'stay_mode')
