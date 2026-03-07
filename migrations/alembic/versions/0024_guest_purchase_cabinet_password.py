"""add cabinet_password column to guest_purchases

Stores the generated plain-text password for guest email purchasers
so it can be shown on the success page and sent in credentials email.

Revision ID: 0024
Revises: 0023
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0024'
down_revision: Union[str, None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('guest_purchases', sa.Column('cabinet_password', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('guest_purchases', 'cabinet_password')
