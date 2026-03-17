"""make riopay_payments.user_id nullable for guest purchases

Revision ID: 0039
Revises: 0038
Create Date: 2026-03-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0039'
down_revision: Union[str, None] = '0038'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('riopay_payments') as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('riopay_payments') as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=False)
