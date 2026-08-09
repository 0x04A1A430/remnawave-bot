"""Add personal_price_kopeks to User

Revision ID: 9106
Revises: 9105
Create Date: 2026-08-01

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9106"
down_revision: Union[str, None] = "9105"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_user_cols = {c["name"] for c in inspector.get_columns("users")}
    if "personal_price_kopeks" not in existing_user_cols:
        op.add_column(
            "users",
            sa.Column("personal_price_kopeks", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("users", "personal_price_kopeks")
