"""dedupe multi-tariff subscriptions: collapse expired same-tariff duplicates

Multi-tariff users accumulated a stack of EXPIRED subscriptions of the same
tariff: re-buying a tariff after its subscription lapsed created a NEW row
instead of reviving the old one (the partial unique index
``uq_subscriptions_user_tariff_active`` only guards the alive statuses). The
code fix makes purchases revive the existing record; this migration cleans up
the duplicates that already piled up.

Per (user_id, tariff_id) it keeps a single survivor — the most "alive" one
(active > limited > trial > expired > other), then the latest end_date — and
deletes the redundant EXPIRED/DISABLED duplicates. Alive subscriptions
(active / limited / trial) and lone (non-duplicate) rows are never deleted.
FK children (traffic_purchases, …) go away via their ON DELETE CASCADE /
SET NULL rules. Idempotent: a second run finds nothing to delete.

Note: the Remnawave panel users behind the removed expired rows are left as-is
(inactive in the panel already); panel-side cleanup is out of scope here.

Irreversible data cleanup — downgrade is a no-op.

Revision ID: 0088
Revises: 0087
Create Date: 2026-06-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0088'
down_revision: Union[str, None] = '0087'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Rank subscriptions within each (user, tariff) group; keep rn = 1 (the survivor)
# and delete only the redundant rows that are EXPIRED or DISABLED. The status
# guard on the DELETE makes it impossible to ever remove an active/limited/trial
# subscription, even in a degenerate group.
_DEDUPE_SQL = """
WITH ranked AS (
    SELECT
        id,
        status,
        ROW_NUMBER() OVER (
            PARTITION BY user_id, tariff_id
            ORDER BY
                CASE status
                    WHEN 'active' THEN 0
                    WHEN 'limited' THEN 1
                    WHEN 'trial' THEN 2
                    WHEN 'expired' THEN 3
                    ELSE 4
                END,
                end_date DESC NULLS LAST,
                id DESC
        ) AS rn
    FROM subscriptions
    WHERE tariff_id IS NOT NULL AND is_trial = false
)
DELETE FROM subscriptions
WHERE id IN (
    SELECT id FROM ranked WHERE rn > 1 AND status IN ('expired', 'disabled')
)
"""


def upgrade() -> None:
    bind = op.get_bind()
    # PostgreSQL-only cleanup (window functions + NULLS LAST). The bot runs on
    # PostgreSQL; on any other backend just skip so the migration never hard-fails.
    if bind.dialect.name != 'postgresql':
        return
    result = bind.execute(sa.text(_DEDUPE_SQL))
    removed = result.rowcount if result.rowcount is not None else -1
    print(f'[0088] dedupe_tariff_subscriptions: removed {removed} duplicate expired/disabled subscription(s)')


def downgrade() -> None:
    # Irreversible data cleanup — nothing to restore.
    pass
