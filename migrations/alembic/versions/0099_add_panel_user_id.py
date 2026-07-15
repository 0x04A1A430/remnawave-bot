"""Add panel_user_id to User and Subscription for Remnawave v3.0.0 migration

Revision ID: 0099
Revises: 0098
Create Date: 2026-07-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0099"
down_revision: Union[str, None] = "0098"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Users table
    existing_user_cols = {c["name"] for c in inspector.get_columns("users")}
    if "panel_user_id" not in existing_user_cols:
        op.add_column(
            "users",
            sa.Column("panel_user_id", sa.Integer(), nullable=True),
        )
        op.create_index("ix_users_panel_user_id", "users", ["panel_user_id"])

    # Subscriptions table
    existing_sub_cols = {c["name"] for c in inspector.get_columns("subscriptions")}
    if "panel_user_id" not in existing_sub_cols:
        op.add_column(
            "subscriptions",
            sa.Column("panel_user_id", sa.Integer(), nullable=True),
        )
        op.create_index(
            "ix_subscriptions_panel_user_id",
            "subscriptions",
            ["panel_user_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_subscriptions_panel_user_id", table_name="subscriptions")
    op.drop_column("subscriptions", "panel_user_id")
    op.drop_index("ix_users_panel_user_id", table_name="users")
    op.drop_column("users", "panel_user_id")
