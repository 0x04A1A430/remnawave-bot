"""add pinned start mode and user last pin

Revision ID: 1b2e3d4f5a6b
Revises: 7a3c0b8f5b84
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1b2e3d4f5a6b'
down_revision = '7a3c0b8f5b84'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'pinned_messages',
        sa.Column('send_on_every_start', sa.Boolean(), nullable=False, server_default='1'),
    )
    op.add_column(
        'users',
        sa.Column('last_pinned_message_id', sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column('users', 'last_pinned_message_id')
    op.drop_column('pinned_messages', 'send_on_every_start')
