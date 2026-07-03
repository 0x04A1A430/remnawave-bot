"""add pending_campaign_slug to users

Revision ID: 0055
Revises: 0054
Create Date: 2026-04-13

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0055'
down_revision: Union[str, None] = '0054'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = {c['name'] for c in inspector.get_columns('users')}
    if 'pending_campaign_slug' not in existing_cols:
        op.add_column(
            'users',
            sa.Column('pending_campaign_slug', sa.String(64), nullable=True),
        )


def downgrade() -> None:
    op.drop_column('users', 'pending_campaign_slug')
