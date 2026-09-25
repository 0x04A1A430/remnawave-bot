"""Add intended_recipient to inline gifts

Fix inline feedback: the intended-recipient sentinel (`u:`/`tid:`) used to
overwrite the real `inline_message_id` in `handle_chosen_inline_result`,
so the "Активировать" button could never be updated after activation.

- add `intended_recipient` string column
- backfill: move existing `u:`/`tid:` sentinels out of `inline_message_id`

Revision ID: 9111
Revises: 9110
Create Date: 2026-09-25

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

revision: str = "9111"
down_revision: Union[str, None] = "9110"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SENTINEL_SELECT = text(
    "SELECT id, inline_message_id FROM inline_gift_subscriptions "
    "WHERE inline_message_id LIKE 'u:%' OR inline_message_id LIKE 'tid:%'"
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("inline_gift_subscriptions")}

    if "intended_recipient" not in existing:
        op.add_column(
            "inline_gift_subscriptions",
            sa.Column("intended_recipient", sa.String(length=64), nullable=True),
        )

    rows = conn.execute(_SENTINEL_SELECT).fetchall()
    for row in rows:
        conn.execute(
            text(
                "UPDATE inline_gift_subscriptions "
                "SET intended_recipient = :sentinel, inline_message_id = NULL "
                "WHERE id = :row_id"
            ),
            {"sentinel": row.inline_message_id, "row_id": row.id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            "UPDATE inline_gift_subscriptions "
            "SET inline_message_id = intended_recipient, intended_recipient = NULL "
            "WHERE intended_recipient IS NOT NULL AND (inline_message_id IS NULL OR inline_message_id = '')"
        )
    )
    op.drop_column("inline_gift_subscriptions", "intended_recipient")
