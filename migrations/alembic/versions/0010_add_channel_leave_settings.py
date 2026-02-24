"""add per-channel disable settings (disable_trial_on_leave, disable_paid_on_leave)

Revision ID: 0010
Revises: 0009
Create Date: 2026-02-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'required_channels',
        sa.Column('disable_trial_on_leave', sa.Boolean(), nullable=False, server_default='true'),
    )
    op.add_column(
        'required_channels',
        sa.Column('disable_paid_on_leave', sa.Boolean(), nullable=False, server_default='false'),
    )


def downgrade() -> None:
    op.drop_column('required_channels', 'disable_paid_on_leave')
    op.drop_column('required_channels', 'disable_trial_on_leave')
