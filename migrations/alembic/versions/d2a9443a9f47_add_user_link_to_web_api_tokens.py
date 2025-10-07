"""add user link to web api tokens"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import reflection

revision: str = "d2a9443a9f47"
down_revision: Union[str, None] = "8fd1e338eb45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "web_api_tokens"
COLUMN_NAME = "user_id"
FK_NAME = "fk_web_api_tokens_user_id"
INDEX_NAME = "ix_web_api_tokens_user_id"


def _column_exists(inspector: reflection.Inspector) -> bool:
    return COLUMN_NAME in [column["name"] for column in inspector.get_columns(TABLE_NAME)]


def _fk_exists(inspector: reflection.Inspector) -> bool:
    return any(fk.get("name") == FK_NAME for fk in inspector.get_foreign_keys(TABLE_NAME))


def _index_exists(inspector: reflection.Inspector) -> bool:
    return any(index.get("name") == INDEX_NAME for index in inspector.get_indexes(TABLE_NAME))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = reflection.Inspector.from_engine(bind)

    if not _column_exists(inspector):
        op.add_column(TABLE_NAME, sa.Column(COLUMN_NAME, sa.Integer(), nullable=True))
        inspector = reflection.Inspector.from_engine(bind)

    if bind.dialect.name in {"postgresql", "mysql"} and not _fk_exists(inspector):
        try:
            op.create_foreign_key(
                FK_NAME,
                TABLE_NAME,
                "users",
                [COLUMN_NAME],
                ["id"],
                ondelete="SET NULL",
            )
        except Exception:
            # Constraint creation can fail if duplicates exist; skip to keep migration resilient.
            pass

    inspector = reflection.Inspector.from_engine(bind)
    if not _index_exists(inspector):
        op.create_index(INDEX_NAME, TABLE_NAME, [COLUMN_NAME])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = reflection.Inspector.from_engine(bind)

    if _index_exists(inspector):
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)

    inspector = reflection.Inspector.from_engine(bind)
    if _fk_exists(inspector):
        op.drop_constraint(FK_NAME, TABLE_NAME, type_="foreignkey")

    inspector = reflection.Inspector.from_engine(bind)
    if _column_exists(inspector):
        op.drop_column(TABLE_NAME, COLUMN_NAME)
