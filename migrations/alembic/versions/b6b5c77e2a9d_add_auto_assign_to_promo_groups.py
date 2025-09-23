"""add auto assign fields to promo groups"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


PROMO_GROUPS_TABLE = "promo_groups"
AUTO_ASSIGN_COLUMN = "auto_assign_enabled"
SPENT_THRESHOLD_COLUMN = "spent_threshold_kopeks"


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


revision: str = "b6b5c77e2a9d"
down_revision: Union[str, None] = "1f5f3a3f5a4d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, PROMO_GROUPS_TABLE):
        return

    if not _column_exists(inspector, PROMO_GROUPS_TABLE, AUTO_ASSIGN_COLUMN):
        op.add_column(
            PROMO_GROUPS_TABLE,
            sa.Column(
                AUTO_ASSIGN_COLUMN,
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    if not _column_exists(inspector, PROMO_GROUPS_TABLE, SPENT_THRESHOLD_COLUMN):
        op.add_column(
            PROMO_GROUPS_TABLE,
            sa.Column(
                SPENT_THRESHOLD_COLUMN,
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )

    op.execute(
        sa.text(
            f"UPDATE {PROMO_GROUPS_TABLE} "
            f"SET {AUTO_ASSIGN_COLUMN} = false, {SPENT_THRESHOLD_COLUMN} = 0 "
            f"WHERE {AUTO_ASSIGN_COLUMN} IS NULL OR {SPENT_THRESHOLD_COLUMN} IS NULL"
        )
    )

    op.alter_column(
        PROMO_GROUPS_TABLE,
        AUTO_ASSIGN_COLUMN,
        server_default=None,
    )
    op.alter_column(
        PROMO_GROUPS_TABLE,
        SPENT_THRESHOLD_COLUMN,
        server_default=None,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, PROMO_GROUPS_TABLE):
        return

    if _column_exists(inspector, PROMO_GROUPS_TABLE, AUTO_ASSIGN_COLUMN):
        op.drop_column(PROMO_GROUPS_TABLE, AUTO_ASSIGN_COLUMN)

    if _column_exists(inspector, PROMO_GROUPS_TABLE, SPENT_THRESHOLD_COLUMN):
        op.drop_column(PROMO_GROUPS_TABLE, SPENT_THRESHOLD_COLUMN)
