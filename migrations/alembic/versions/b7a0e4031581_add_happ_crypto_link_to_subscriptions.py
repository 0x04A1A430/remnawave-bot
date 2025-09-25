from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7a0e4031581"
down_revision: Union[str, None] = "5d1f1f8b2e9a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SUBSCRIPTIONS_TABLE = "subscriptions"
HAPP_CRYPTO_LINK_COLUMN = "happ_crypto_link"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if SUBSCRIPTIONS_TABLE in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns(SUBSCRIPTIONS_TABLE)}
        if HAPP_CRYPTO_LINK_COLUMN not in columns:
            op.add_column(
                SUBSCRIPTIONS_TABLE,
                sa.Column(HAPP_CRYPTO_LINK_COLUMN, sa.String(), nullable=True),
            )
    else:
        op.add_column(
            SUBSCRIPTIONS_TABLE,
            sa.Column(HAPP_CRYPTO_LINK_COLUMN, sa.String(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if SUBSCRIPTIONS_TABLE in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns(SUBSCRIPTIONS_TABLE)}
        if HAPP_CRYPTO_LINK_COLUMN in columns:
            op.drop_column(SUBSCRIPTIONS_TABLE, HAPP_CRYPTO_LINK_COLUMN)
