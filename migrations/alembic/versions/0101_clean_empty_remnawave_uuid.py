"""Clean empty remnawave_uuid values

Remnawave v3.0.0 removed `uuid` from user responses, so the bot could write
`remnawave_uuid = ''` into `users` (UNIQUE column) and `subscriptions`.
Postgres treats `''` as a real value (unlike NULL), so a second user synced
without a uuid violates the `users_remnawave_uuid_key` unique constraint.
Normalize leftover `''` to NULL.

Revision ID: 0101
Revises: 0100
Create Date: 2026-08-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0101"
down_revision: Union[str, None] = "0100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE users SET remnawave_uuid = NULL WHERE remnawave_uuid = ''"))
    conn.execute(sa.text("UPDATE subscriptions SET remnawave_uuid = NULL WHERE remnawave_uuid = ''"))


def downgrade() -> None:
    # Non-reversible data cleanup: empty strings are meaningless identifiers.
    pass
