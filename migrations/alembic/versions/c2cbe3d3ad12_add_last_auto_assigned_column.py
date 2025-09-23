"""Add last_auto_assigned_promo_group_id to users

Revision ID: c2cbe3d3ad12
Revises: b6b5c77e2a9d
Create Date: 2024-05-21 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2cbe3d3ad12"
down_revision: Union[str, None] = "b6b5c77e2a9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

USERS_TABLE = "users"
LAST_AUTO_ASSIGN_COLUMN = "last_auto_assigned_promo_group_id"


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, USERS_TABLE):
        return

    if not _column_exists(inspector, USERS_TABLE, LAST_AUTO_ASSIGN_COLUMN):
        op.add_column(
            USERS_TABLE,
            sa.Column(LAST_AUTO_ASSIGN_COLUMN, sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, USERS_TABLE):
        return

    if _column_exists(inspector, USERS_TABLE, LAST_AUTO_ASSIGN_COLUMN):
        op.drop_column(USERS_TABLE, LAST_AUTO_ASSIGN_COLUMN)
