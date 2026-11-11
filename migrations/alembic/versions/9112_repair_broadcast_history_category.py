"""Repair missing broadcast_history.category column

Some databases were restored/stamped at a later revision while the
``0054_add_broadcast_category`` column was absent. Alembic then considered
the migration applied, so every ORM query against ``broadcast_history``
failed with ``UndefinedColumnError`` and daily backups silently skipped the
table.

The repair is intentionally idempotent: on healthy databases (where the
column already exists) it is a no-op.

Revision ID: 9112
Revises: 9111
Create Date: 2026-11-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op


revision: str = '9112'
down_revision: Union[str, None] = '9111'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('broadcast_history'):
        return

    existing = {column['name'] for column in inspector.get_columns('broadcast_history')}
    if 'category' not in existing:
        op.add_column(
            'broadcast_history',
            sa.Column('category', sa.String(length=20), nullable=False, server_default='system'),
        )

    # A nullable/corrupt column may exist on partially repaired databases.
    conn.execute(text("UPDATE broadcast_history SET category = 'system' WHERE category IS NULL"))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('broadcast_history'):
        return

    existing = {column['name'] for column in inspector.get_columns('broadcast_history')}
    if 'category' in existing:
        op.drop_column('broadcast_history', 'category')
