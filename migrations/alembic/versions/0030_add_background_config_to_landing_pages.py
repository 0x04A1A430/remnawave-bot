"""Add background_config to landing_pages

Revision ID: 0030
Revises: 0029
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0030'
down_revision: Union[str, None] = '0029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('landing_pages', sa.Column('background_config', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('landing_pages', 'background_config')
