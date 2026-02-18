"""add campaign_id to referral_earnings

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-18

Adds campaign_id FK to referral_earnings table and backfills
existing rows from advertising_campaign_registrations.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add column (idempotent check)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('referral_earnings')]

    if 'campaign_id' not in columns:
        op.add_column('referral_earnings', sa.Column('campaign_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            'fk_referral_earnings_campaign_id',
            'referral_earnings',
            'advertising_campaigns',
            ['campaign_id'],
            ['id'],
            ondelete='SET NULL',
        )
        op.create_index('ix_referral_earnings_campaign_id', 'referral_earnings', ['campaign_id'])

    # Backfill existing data — pick earliest campaign registration per user
    # (matches runtime logic in get_user_campaign_id: ORDER BY created_at ASC LIMIT 1)
    op.execute(
        """
        UPDATE referral_earnings re
        SET campaign_id = sub.campaign_id
        FROM (
            SELECT DISTINCT ON (user_id) user_id, campaign_id
            FROM advertising_campaign_registrations
            ORDER BY user_id, created_at ASC
        ) sub
        WHERE sub.user_id = re.referral_id
          AND re.campaign_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index('ix_referral_earnings_campaign_id', table_name='referral_earnings')
    op.drop_constraint('fk_referral_earnings_campaign_id', 'referral_earnings', type_='foreignkey')
    op.drop_column('referral_earnings', 'campaign_id')
