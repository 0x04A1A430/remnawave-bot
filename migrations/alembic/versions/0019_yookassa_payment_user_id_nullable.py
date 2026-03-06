"""make yookassa_payments.user_id nullable for guest payments

Revision ID: 0019
Revises: 0018
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0019'
down_revision: Union[str, None] = '0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('yookassa_payments', 'user_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column('yookassa_payments', 'user_id', existing_type=sa.Integer(), nullable=False)
