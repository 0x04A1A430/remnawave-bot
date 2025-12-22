"""add send_before_menu to pinned messages

Revision ID: 7a3c0b8f5b84
Revises: 5f2a3e099427
Create Date: 2025-02-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7a3c0b8f5b84"
down_revision = "5f2a3e099427"
branch_labels = None
depends_on = None


TABLE_NAME = "pinned_messages"


def _table_exists(inspector: sa.Inspector) -> bool:
    return TABLE_NAME in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, column_name: str) -> bool:
    if not _table_exists(inspector):
        return False
    columns = {col["name"] for col in inspector.get_columns(TABLE_NAME)}
    return column_name in columns


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector):
        return

    if _column_exists(inspector, "send_before_menu"):
        return

    op.add_column(
        TABLE_NAME,
        sa.Column(
            "send_before_menu",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, "send_before_menu"):
        op.drop_column(TABLE_NAME, "send_before_menu")
