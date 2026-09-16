"""add missing broadcast_history columns

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-23

Adds blocked_count, channel, email_subject, email_html_content
to broadcast_history. The blocked_count column was defined in 0003/0005
but may not have been applied. The email columns were never migrated.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return column in [c['name'] for c in inspector.get_columns(table)]


def _has_table(table: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return table in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table('broadcast_history'):
        return

    if not _has_column('broadcast_history', 'blocked_count'):
        op.add_column('broadcast_history', sa.Column('blocked_count', sa.Integer(), nullable=True, server_default='0'))

    if not _has_column('broadcast_history', 'channel'):
        op.add_column(
            'broadcast_history',
            sa.Column('channel', sa.String(20), nullable=False, server_default='telegram'),
        )

    if not _has_column('broadcast_history', 'email_subject'):
        op.add_column('broadcast_history', sa.Column('email_subject', sa.String(255), nullable=True))

    if not _has_column('broadcast_history', 'email_html_content'):
        op.add_column('broadcast_history', sa.Column('email_html_content', sa.Text(), nullable=True))


def downgrade() -> None:
    if not _has_table('broadcast_history'):
        return

    if _has_column('broadcast_history', 'email_html_content'):
        op.drop_column('broadcast_history', 'email_html_content')

    if _has_column('broadcast_history', 'email_subject'):
        op.drop_column('broadcast_history', 'email_subject')

    if _has_column('broadcast_history', 'channel'):
        op.drop_column('broadcast_history', 'channel')

    # blocked_count is not dropped here — it belongs to migration 0003
