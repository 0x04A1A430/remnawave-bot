"""add pinned start mode and user last pin

Revision ID: 1b2e3d4f5a6b
Revises: 7a3c0b8f5b84
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1b2e3d4f5a6b'
down_revision = '7a3c0b8f5b84'
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if not _table_exists(inspector, table_name):
        return False
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "pinned_messages"):
        if not _column_exists(inspector, "pinned_messages", "send_on_every_start"):
            op.add_column(
                'pinned_messages',
                sa.Column('send_on_every_start', sa.Boolean(), nullable=False, server_default='1'),
            )

    if _table_exists(inspector, "users"):
        if not _column_exists(inspector, "users", "last_pinned_message_id"):
            op.add_column(
                'users',
                sa.Column('last_pinned_message_id', sa.Integer(), nullable=True),
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, "users", "last_pinned_message_id"):
        op.drop_column('users', 'last_pinned_message_id')

    if _column_exists(inspector, "pinned_messages", "send_on_every_start"):
        op.drop_column('pinned_messages', 'send_on_every_start')
