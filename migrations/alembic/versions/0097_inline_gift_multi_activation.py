"""Add max_activations, activated_count, add_extra_squad to inline_gift_subscriptions

Revision ID: 0055
Revises: 0054
Create Date: 2026-05-17

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0097'
down_revision: Union[str, None] = '0096'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = {c['name'] for c in inspector.get_columns('inline_gift_subscriptions')}

    if 'max_activations' not in existing_cols:
        op.add_column(
            'inline_gift_subscriptions',
            sa.Column('max_activations', sa.Integer(), nullable=False, server_default='1'),
        )
    if 'activated_count' not in existing_cols:
        op.add_column(
            'inline_gift_subscriptions',
            sa.Column('activated_count', sa.Integer(), nullable=False, server_default='0'),
        )
    if 'add_extra_squad' not in existing_cols:
        op.add_column(
            'inline_gift_subscriptions',
            sa.Column('add_extra_squad', sa.Boolean(), nullable=False, server_default='false'),
        )
    # Sync activated_count for already-activated gifts
    op.execute("UPDATE inline_gift_subscriptions SET activated_count = 1 WHERE is_activated = true")


def downgrade() -> None:
    op.drop_column('inline_gift_subscriptions', 'add_extra_squad')
    op.drop_column('inline_gift_subscriptions', 'activated_count')
    op.drop_column('inline_gift_subscriptions', 'max_activations')
