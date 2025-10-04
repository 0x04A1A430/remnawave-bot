"""create system settings history table"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


revision: str = "2b1c8fd0dc42"
down_revision: Union[str, None] = "8fd1e338eb45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "system_settings_history"
KEY_INDEX = "ix_system_settings_history_key"


def _table_exists(inspector: Inspector) -> bool:
    return TABLE_NAME in inspector.get_table_names()


def _index_exists(inspector: Inspector, index_name: str) -> bool:
    try:
        indexes = inspector.get_indexes(TABLE_NAME)
    except sa.exc.NoSuchTableError:
        return False
    return any(index.get("name") == index_name for index in indexes)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    table_created = False

    if not _table_exists(inspector):
        op.create_table(
            TABLE_NAME,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(length=255), nullable=False),
            sa.Column("old_value", sa.Text(), nullable=True),
            sa.Column("new_value", sa.Text(), nullable=True),
            sa.Column("changed_by", sa.Integer(), nullable=True),
            sa.Column("changed_by_username", sa.String(length=255), nullable=True),
            sa.Column(
                "source",
                sa.String(length=50),
                nullable=False,
                server_default=sa.text("'bot'"),
            ),
            sa.Column("reason", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        table_created = True

    if table_created or not _index_exists(inspector, KEY_INDEX):
        op.create_index(KEY_INDEX, TABLE_NAME, ["key"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector):
        if _index_exists(inspector, KEY_INDEX):
            op.drop_index(KEY_INDEX, table_name=TABLE_NAME)
        op.drop_table(TABLE_NAME)
