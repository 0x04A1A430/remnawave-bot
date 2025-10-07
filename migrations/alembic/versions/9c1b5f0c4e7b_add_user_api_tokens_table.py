from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


revision: str = "9c1b5f0c4e7b"
down_revision: Union[str, None] = "8fd1e338eb45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "user_api_tokens"


def _table_exists(inspector: Inspector) -> bool:
    return TABLE_NAME in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector):
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        sa.Column("token_last_digits", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_ip", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_index("ix_user_api_tokens_last_used_at", TABLE_NAME, ["last_used_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector):
        return

    op.drop_index("ix_user_api_tokens_last_used_at", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
