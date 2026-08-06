"""Clean NaN-like remnawave_uuid values

v3.0.0 panel can return `uuid` as JSON-literal NaN (json.loads -> float('nan'))
or the string "NaN". Such values were persisted into `users`/`subscriptions`
and then built into `/api/users/NaN` URLs (panel rejects with 400 "received NaN").
Normalize leftover NaN-like identifiers to NULL.

Revision ID: 0102
Revises: 0101
Create Date: 2026-08-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0102"
down_revision: Union[str, None] = "0101"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAN_LIKE = "lower(remnawave_uuid) IN ('nan', 'inf', '-inf', 'infinity', '-infinity')"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(f"UPDATE users SET remnawave_uuid = NULL WHERE {_NAN_LIKE}"))
    conn.execute(sa.text(f"UPDATE subscriptions SET remnawave_uuid = NULL WHERE {_NAN_LIKE}"))


def downgrade() -> None:
    # Non-reversible data cleanup.
    pass
