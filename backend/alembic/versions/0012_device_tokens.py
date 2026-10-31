"""mobile app push: device tokens, and which notifications were pushed

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-31 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'device_tokens',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('token', sa.String(length=512), nullable=False),
        sa.Column('platform', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )
    op.create_index('ix_device_tokens_user_id', 'device_tokens', ['user_id'])
    op.add_column('notifications', sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True))
    # Everything already in people's bells is old news: never push it when this goes live.
    op.execute("UPDATE notifications SET pushed_at = now()")
    op.create_index('ix_notifications_unpushed', 'notifications', ['created_at'], postgresql_where=sa.text('pushed_at IS NULL'))


def downgrade() -> None:
    op.drop_index('ix_notifications_unpushed', table_name='notifications')
    op.drop_column('notifications', 'pushed_at')
    op.drop_index('ix_device_tokens_user_id', table_name='device_tokens')
    op.drop_table('device_tokens')
