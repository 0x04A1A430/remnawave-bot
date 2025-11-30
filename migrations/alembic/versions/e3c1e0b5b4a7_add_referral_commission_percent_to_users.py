from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e3c1e0b5b4a7"
down_revision: Union[str, None] = "c2f9c3b5f5c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("referral_commission_percent", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "referral_commission_percent")
