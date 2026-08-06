"""Add temp_traffic params to inline gifts

Store the temporary-traffic component separately from the subscription
traffic limit so a single gift can mix subscription + temp traffic (combo).

- add `temp_traffic_gb` and `temp_traffic_days` columns
- backfill existing `temp_traffic` gifts: move GB out of `traffic_limit_gb`
  into `temp_traffic_gb` and default the duration to 30 days

Revision ID: 0103
Revises: 0102
Create Date: 2026-08-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0103"
down_revision: Union[str, None] = "0102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("inline_gift_subscriptions")}

    if "temp_traffic_gb" not in existing:
        op.add_column(
            "inline_gift_subscriptions",
            sa.Column("temp_traffic_gb", sa.Integer(), nullable=True),
        )
    if "temp_traffic_days" not in existing:
        op.add_column(
            "inline_gift_subscriptions",
            sa.Column("temp_traffic_days", sa.Integer(), nullable=True),
        )

    # Backfill legacy temp_traffic gifts whose GB was stored in traffic_limit_gb
    conn.execute(
        sa.text(
            "UPDATE inline_gift_subscriptions "
            "SET temp_traffic_gb = traffic_limit_gb, temp_traffic_days = 30 "
            "WHERE gift_type = 'temp_traffic' AND traffic_limit_gb IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("inline_gift_subscriptions", "temp_traffic_days")
    op.drop_column("inline_gift_subscriptions", "temp_traffic_gb")