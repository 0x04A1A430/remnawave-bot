"""repair pending_campaign_slug when an older migration was stamped early

Revision ID: 0124
Revises: 0123
Create Date: 2026-09-16

Some installations have revision 0055 recorded in ``alembic_version`` while
the corresponding column is missing from ``users``.  The ORM then fails on
every query that loads a User.  Repair the schema without changing data.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0124'
down_revision: Union[str, None] = '0123'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return True
    return column in {item['name'] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column('users', 'pending_campaign_slug'):
        op.add_column('users', sa.Column('pending_campaign_slug', sa.String(64), nullable=True))


def downgrade() -> None:
    if not _has_column('users', 'pending_campaign_slug'):
        return
    op.drop_column('users', 'pending_campaign_slug')
