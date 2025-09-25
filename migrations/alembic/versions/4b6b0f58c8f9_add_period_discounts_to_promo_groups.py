from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4b6b0f58c8f9"
down_revision: Union[str, None] = "1f5f3a3f5a4d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind else ""

    op.add_column(
        "promo_groups",
        sa.Column("period_discounts", sa.JSON(), nullable=True),
    )

    if dialect == "postgresql":
        op.execute("UPDATE promo_groups SET period_discounts = '{}'::jsonb WHERE period_discounts IS NULL")
    else:
        op.execute("UPDATE promo_groups SET period_discounts = '{}' WHERE period_discounts IS NULL")


def downgrade() -> None:
    op.drop_column("promo_groups", "period_discounts")
