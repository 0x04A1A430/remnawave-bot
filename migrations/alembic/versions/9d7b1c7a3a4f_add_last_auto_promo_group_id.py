"""
Add last_auto_promo_group_id column to users.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


revision: str = "9d7b1c7a3a4f"
down_revision: Union[str, None] = "8fd1e338eb45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "users"
COLUMN_NAME = "last_auto_promo_group_id"
INDEX_NAME = "ix_users_last_auto_promo_group_id"
FK_NAME = "fk_users_last_auto_promo_group_id"


def _column_exists(inspector: Inspector) -> bool:
    return COLUMN_NAME in {column["name"] for column in inspector.get_columns(TABLE_NAME)}


def _index_exists(inspector: Inspector) -> bool:
    return INDEX_NAME in {index["name"] for index in inspector.get_indexes(TABLE_NAME)}


def _fk_exists(inspector: Inspector) -> bool:
    return FK_NAME in {fk["name"] for fk in inspector.get_foreign_keys(TABLE_NAME)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector):
        op.add_column(
            TABLE_NAME,
            sa.Column(COLUMN_NAME, sa.Integer(), nullable=True),
        )
        inspector = sa.inspect(bind)

    if _column_exists(inspector) and not _fk_exists(inspector):
        op.create_foreign_key(
            FK_NAME,
            TABLE_NAME,
            "promo_groups",
            [COLUMN_NAME],
            ["id"],
            ondelete="SET NULL",
        )
        inspector = sa.inspect(bind)

    if _column_exists(inspector) and not _index_exists(inspector):
        op.create_index(INDEX_NAME, TABLE_NAME, [COLUMN_NAME])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _index_exists(inspector):
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
        inspector = sa.inspect(bind)

    if _fk_exists(inspector):
        op.drop_constraint(FK_NAME, TABLE_NAME, type_="foreignkey")
        inspector = sa.inspect(bind)

    if _column_exists(inspector):
        op.drop_column(TABLE_NAME, COLUMN_NAME)
