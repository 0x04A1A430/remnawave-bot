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


def _has_table(name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return name in inspector.get_table_names()


def _has_column(table: str, column: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return column in [c['name'] for c in inspector.get_columns(table)]


def upgrade() -> None:
    # Skip if referral_earnings table doesn't exist yet
    # (fresh DBs create it via create_all in 0001 with campaign_id already present)
    if not _has_table('referral_earnings'):
        return

    if not _has_column('referral_earnings', 'campaign_id'):
        op.add_column('referral_earnings', sa.Column('campaign_id', sa.Integer(), nullable=True))

        # Only create FK if target table exists
        if _has_table('advertising_campaigns'):
            op.create_foreign_key(
                'fk_referral_earnings_campaign_id',
                'referral_earnings',
                'advertising_campaigns',
                ['campaign_id'],
                ['id'],
                ondelete='SET NULL',
            )

        op.create_index('ix_referral_earnings_campaign_id', 'referral_earnings', ['campaign_id'])

    # Backfill existing data — only if source table exists
    if _has_table('advertising_campaign_registrations') and _has_table('referral_earnings'):
        op.execute(
            sa.text("""
            UPDATE referral_earnings re
            SET campaign_id = sub.campaign_id
            FROM (
                SELECT DISTINCT ON (user_id) user_id, campaign_id
                FROM advertising_campaign_registrations
                ORDER BY user_id, created_at ASC
            ) sub
            WHERE sub.user_id = re.referral_id
              AND re.campaign_id IS NULL
            """)
        )


def downgrade() -> None:
    if _has_table('referral_earnings') and _has_column('referral_earnings', 'campaign_id'):
        op.drop_index('ix_referral_earnings_campaign_id', table_name='referral_earnings')
        op.drop_constraint('fk_referral_earnings_campaign_id', 'referral_earnings', type_='foreignkey')
        op.drop_column('referral_earnings', 'campaign_id')
