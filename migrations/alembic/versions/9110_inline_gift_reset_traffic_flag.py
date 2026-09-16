"""Add reset-traffic flag to inline gifts

Allow an admin gift to reset a user's traffic usage via a `-r` flag
(e.g. `@user -r`), optionally mixed with other components.

- add `reset_traffic` boolean column

Revision ID: 9110
Revises: 9109
Create Date: 2026-08-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9110"
down_revision: Union[str, None] = "9109"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("inline_gift_subscriptions")}

    if "reset_traffic" not in existing:
        op.add_column(
            "inline_gift_subscriptions",
            sa.Column(
                "reset_traffic",
                sa.Boolean(),
                nullable=True,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    op.drop_column("inline_gift_subscriptions", "reset_traffic")