"""Add gift_type, discount_percent, balance_amount_kopeks to inline_gift_subscriptions

Revision ID: 0054
Revises: 0053
Create Date: 2026-04-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0096'
down_revision: Union[str, None] = '0095'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = {c['name'] for c in inspector.get_columns('inline_gift_subscriptions')}

    if 'gift_type' not in existing_cols:
        op.add_column(
            'inline_gift_subscriptions',
            sa.Column('gift_type', sa.String(20), nullable=False, server_default='subscription'),
        )
    if 'discount_percent' not in existing_cols:
        op.add_column(
            'inline_gift_subscriptions',
            sa.Column('discount_percent', sa.Integer(), nullable=True),
        )
    if 'balance_amount_kopeks' not in existing_cols:
        op.add_column(
            'inline_gift_subscriptions',
            sa.Column('balance_amount_kopeks', sa.Integer(), nullable=True),
        )
    # days was previously NOT NULL without default — add server_default for safety
    op.alter_column(
        'inline_gift_subscriptions',
        'days',
        existing_type=sa.Integer(),
        nullable=False,
        server_default='0',
    )


def downgrade() -> None:
    op.drop_column('inline_gift_subscriptions', 'balance_amount_kopeks')
    op.drop_column('inline_gift_subscriptions', 'discount_percent')
    op.drop_column('inline_gift_subscriptions', 'gift_type')
