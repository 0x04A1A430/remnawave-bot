"""add promo groups table and link users"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "1f5f3a3f5a4d"
down_revision: Union[str, None] = "cbd1be472f3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promo_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "server_discount_percent",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "traffic_discount_percent",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "device_discount_percent",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_promo_groups_name"),
    )

    op.add_column(
        "users",
        sa.Column("promo_group_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_users_promo_group_id", "users", ["promo_group_id"], unique=False
    )
    op.create_foreign_key(
        "fk_users_promo_group_id_promo_groups",
        "users",
        "promo_groups",
        ["promo_group_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    promo_groups_table = sa.table(
        "promo_groups",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("server_discount_percent", sa.Integer()),
        sa.column("traffic_discount_percent", sa.Integer()),
        sa.column("device_discount_percent", sa.Integer()),
        sa.column("is_default", sa.Boolean()),
    )

    connection = op.get_bind()
    default_group_id = connection.execute(
        sa.insert(promo_groups_table)
        .values(
            name="Базовый юзер",
            server_discount_percent=0,
            traffic_discount_percent=0,
            device_discount_percent=0,
            is_default=True,
        )
        .returning(promo_groups_table.c.id)
    ).scalar_one()

    users_table = sa.table(
        "users",
        sa.column("promo_group_id", sa.Integer()),
    )
    connection.execute(
        sa.update(users_table)
        .where(users_table.c.promo_group_id.is_(None))
        .values(promo_group_id=default_group_id)
    )

    op.alter_column(
        "users",
        "promo_group_id",
        existing_type=sa.Integer(),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "promo_group_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.drop_constraint(
        "fk_users_promo_group_id_promo_groups", "users", type_="foreignkey"
    )
    op.drop_index("ix_users_promo_group_id", table_name="users")
    op.drop_column("users", "promo_group_id")
    op.drop_table("promo_groups")
