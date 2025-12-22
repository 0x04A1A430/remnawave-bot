"""add delivery position to pinned messages

Revision ID: 0f3d1f5c2c0a
Revises: 5f2a3e099427
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0f3d1f5c2c0a'
down_revision = '5f2a3e099427'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'pinned_messages',
        sa.Column('delivery_position', sa.String(length=32), nullable=False, server_default='before_menu'),
    )
    op.execute("UPDATE pinned_messages SET delivery_position = 'before_menu' WHERE delivery_position IS NULL")
    op.alter_column('pinned_messages', 'delivery_position', server_default=None)


def downgrade() -> None:
    op.drop_column('pinned_messages', 'delivery_position')
