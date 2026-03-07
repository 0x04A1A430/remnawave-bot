"""add external_squad_uuid column to tariffs

Stores the RemnaWave External Squad UUID to assign to users on this tariff.

Revision ID: 0026
Revises: 0025
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0026'
down_revision: Union[str, None] = '0025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tariffs', sa.Column('external_squad_uuid', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('tariffs', 'external_squad_uuid')
