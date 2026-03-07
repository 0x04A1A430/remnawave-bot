"""add CHECK constraints to landing_pages discount columns

Ensures discount_percent is in range 1-99 and discount_starts_at < discount_ends_at
at the database level as defense in depth.

Revision ID: 0028
Revises: 0027
"""

from typing import Sequence, Union

from alembic import op

revision: str = '0028'
down_revision: Union[str, None] = '0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        'chk_landing_discount_percent_range',
        'landing_pages',
        'discount_percent IS NULL OR (discount_percent >= 1 AND discount_percent <= 99)',
    )
    op.create_check_constraint(
        'chk_landing_discount_dates_order',
        'landing_pages',
        'discount_starts_at IS NULL OR discount_ends_at IS NULL OR discount_starts_at < discount_ends_at',
    )


def downgrade() -> None:
    op.drop_constraint('chk_landing_discount_dates_order', 'landing_pages', type_='check')
    op.drop_constraint('chk_landing_discount_percent_range', 'landing_pages', type_='check')
