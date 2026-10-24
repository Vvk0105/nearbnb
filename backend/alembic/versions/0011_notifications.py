"""in-app notifications (the bell)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-26 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=60), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('link', sa.String(length=300), nullable=False),
        sa.Column('tone', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_notifications_user_created', 'notifications', ['user_id', sa.text('created_at DESC')])
    op.create_index('ix_notifications_unread', 'notifications', ['user_id'], postgresql_where=sa.text('read_at IS NULL'))


def downgrade() -> None:
    op.drop_index('ix_notifications_unread', table_name='notifications')
    op.drop_index('ix_notifications_user_created', table_name='notifications')
    op.drop_table('notifications')
