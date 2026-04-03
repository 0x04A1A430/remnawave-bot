"""include limited status in partial unique index for subscriptions

Revision ID: 0053
Revises: 0052
Create Date: 2026-04-03

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0053'
down_revision: Union[str, None] = '0052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old partial unique index that only covered active/trial
    op.execute(sa.text('DROP INDEX IF EXISTS uq_subscriptions_user_tariff_active'))

    # Recreate with limited status included — a limited subscription (traffic
    # exhausted but time remaining) is still "alive" and should prevent
    # duplicate subscriptions for the same user+tariff combination.
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uq_subscriptions_user_tariff_active
            ON subscriptions (user_id, tariff_id)
            WHERE tariff_id IS NOT NULL AND status IN ('active', 'trial', 'limited')
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text('DROP INDEX IF EXISTS uq_subscriptions_user_tariff_active'))
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uq_subscriptions_user_tariff_active
            ON subscriptions (user_id, tariff_id)
            WHERE tariff_id IS NOT NULL AND status IN ('active', 'trial')
            """
        )
    )
