"""Add auto assigned promo group tracking to users

Revision ID: 3e6c4d6db780
Revises: b6b5c77e2a9d
Create Date: 2024-05-06 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3e6c4d6db780"
down_revision: Union[str, None] = "b6b5c77e2a9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

USERS_TABLE = "users"
COLUMN_NAME = "auto_assigned_promo_group_id"
PROMO_GROUPS_TABLE = "promo_groups"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if USERS_TABLE not in inspector.get_table_names():
        return

    if COLUMN_NAME in [column["name"] for column in inspector.get_columns(USERS_TABLE)]:
        return

    op.add_column(
        USERS_TABLE,
        sa.Column(COLUMN_NAME, sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_auto_assigned_promo_group_id",
        USERS_TABLE,
        PROMO_GROUPS_TABLE,
        [COLUMN_NAME],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if USERS_TABLE not in inspector.get_table_names():
        return

    if COLUMN_NAME not in [column["name"] for column in inspector.get_columns(USERS_TABLE)]:
        return

    fk_names = [fk["name"] for fk in inspector.get_foreign_keys(USERS_TABLE)]
    if "fk_users_auto_assigned_promo_group_id" in fk_names:
        op.drop_constraint(
            "fk_users_auto_assigned_promo_group_id",
            USERS_TABLE,
            type_="foreignkey",
        )
    op.drop_column(USERS_TABLE, COLUMN_NAME)
