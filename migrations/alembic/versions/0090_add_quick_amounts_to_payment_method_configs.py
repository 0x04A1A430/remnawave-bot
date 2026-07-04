from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0090"
down_revision: Union[str, None] = "0089"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = {c["name"] for c in inspector.get_columns("payment_method_configs")}
    if "quick_amounts" not in existing_cols:
        op.add_column(
            "payment_method_configs",
            sa.Column("quick_amounts", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("payment_method_configs", "quick_amounts")
