"""add auto purchase after topup flag"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2acb8b40cb5"
down_revision: Union[str, None] = "9f0f2d5a1c7b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

USERS_TABLE = "users"
COLUMN_NAME = "auto_purchase_after_topup_enabled"


def upgrade() -> None:
    op.add_column(
        USERS_TABLE,
        sa.Column(
            COLUMN_NAME,
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column(
        USERS_TABLE,
        COLUMN_NAME,
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column(USERS_TABLE, COLUMN_NAME)
