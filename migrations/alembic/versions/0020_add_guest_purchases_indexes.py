"""add index on guest_purchases.landing_id

Revision ID: 0020
Revises: 0019
Create Date: 2026-03-06

"""

from typing import Sequence, Union

from alembic import op

revision: str = '0020'
down_revision: Union[str, None] = '0019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_guest_purchases_landing_id', 'guest_purchases', ['landing_id'])


def downgrade() -> None:
    op.drop_index('ix_guest_purchases_landing_id')
