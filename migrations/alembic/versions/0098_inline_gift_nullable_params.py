"""Make inline_gift days/traffic/devices nullable (NULL = no change)

Revision ID: 0056
Revises: 0055
Create Date: 2026-05-17

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0098'
down_revision: Union[str, None] = '0097'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Make days nullable: NULL = no change, 0 = forever, N = add N days
    op.alter_column('inline_gift_subscriptions', 'days',
                    existing_type=sa.Integer(), nullable=True, server_default=None)
    # Make traffic nullable: NULL = no change, 0 = unlimited, -1 = set unlimited, N = set N GB
    op.alter_column('inline_gift_subscriptions', 'traffic_limit_gb',
                    existing_type=sa.Integer(), nullable=True, server_default=None)
    # Make device_limit nullable: NULL = no change
    op.alter_column('inline_gift_subscriptions', 'device_limit',
                    existing_type=sa.Integer(), nullable=True, server_default=None)

    # Fix stale -2 sentinel values written by the previous code
    op.execute("UPDATE inline_gift_subscriptions SET traffic_limit_gb = NULL WHERE traffic_limit_gb = -2")
    op.execute("UPDATE inline_gift_subscriptions SET device_limit = NULL WHERE device_limit = 0 AND gift_type = 'subscription'")


def downgrade() -> None:
    op.execute("UPDATE inline_gift_subscriptions SET traffic_limit_gb = 0 WHERE traffic_limit_gb IS NULL")
    op.execute("UPDATE inline_gift_subscriptions SET device_limit = 1 WHERE device_limit IS NULL")
    op.execute("UPDATE inline_gift_subscriptions SET days = 0 WHERE days IS NULL")
    op.alter_column('inline_gift_subscriptions', 'device_limit',
                    existing_type=sa.Integer(), nullable=False, server_default='1')
    op.alter_column('inline_gift_subscriptions', 'traffic_limit_gb',
                    existing_type=sa.Integer(), nullable=False, server_default='0')
    op.alter_column('inline_gift_subscriptions', 'days',
                    existing_type=sa.Integer(), nullable=False, server_default='0')
