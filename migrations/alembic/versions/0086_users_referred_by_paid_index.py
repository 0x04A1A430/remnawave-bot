"""composite index on users(referred_by_id, has_made_first_topup)

The tiered partner commission policy (commit 4b48d519) introduced
``get_paid_referrals_count(referrer_id)``, which executes

    SELECT COUNT(*) FROM users
    WHERE referred_by_id = $1 AND has_made_first_topup = true

on every referral commission calculation — i.e. on every paying
referral's top-up. The pre-existing single-column index on
``users.referred_by_id`` is selective enough for partners with a handful
of referrals, but for partners with thousands of referrals (campaign
landings, KOL bots) PostgreSQL has to fetch each row and re-filter on
``has_made_first_topup``.

A composite index lets the query plan as an index-only scan and keeps
tier selection O(log N) in referral count.

The migration is idempotent: ``CREATE INDEX IF NOT EXISTS`` so re-running
``alembic upgrade head`` on an environment that already has the index
(applied manually as a hotfix, or restored from a backup) does not fail.

Revision ID: 0086
Revises: 0085
Create Date: 2026-05-28
"""

from typing import Sequence, Union

from alembic import op


revision: str = '0086'
down_revision: Union[str, None] = '0085'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_users_referred_by_paid
        ON users (referred_by_id, has_made_first_topup)
        """
    )


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_users_referred_by_paid')
