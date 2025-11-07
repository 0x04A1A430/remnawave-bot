from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2b3c1d4e5f6a"
down_revision: Union[str, None] = "9f0f2d5a1c7b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platega_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("platega_transaction_id", sa.String(length=255), nullable=True, unique=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("amount_kopeks", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=10),
            nullable=False,
            server_default="RUB",
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payment_method_code", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "is_paid",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("redirect_url", sa.Text(), nullable=True),
        sa.Column("return_url", sa.Text(), nullable=True),
        sa.Column("failed_url", sa.Text(), nullable=True),
        sa.Column("payload", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("callback_payload", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_platega_payments_id", "platega_payments", ["id"])
    op.create_index("ix_platega_payments_user_id", "platega_payments", ["user_id"])
    op.create_index(
        "ix_platega_payments_platega_transaction_id",
        "platega_payments",
        ["platega_transaction_id"],
    )
    op.create_index(
        "ix_platega_payments_correlation_id",
        "platega_payments",
        ["correlation_id"],
        unique=True,
    )
    op.create_index(
        "ix_platega_payments_transaction_id",
        "platega_payments",
        ["transaction_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_platega_payments_transaction_id", table_name="platega_payments")
    op.drop_index("ix_platega_payments_correlation_id", table_name="platega_payments")
    op.drop_index(
        "ix_platega_payments_platega_transaction_id",
        table_name="platega_payments",
    )
    op.drop_index("ix_platega_payments_user_id", table_name="platega_payments")
    op.drop_index("ix_platega_payments_id", table_name="platega_payments")
    op.drop_table("platega_payments")
