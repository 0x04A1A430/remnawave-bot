"""rebuild apple iap ledgers

Revision ID: 0075
Revises: 0074
Create Date: 2026-05-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0075"
down_revision: Union[str, None] = "0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # --- add_column on apple_transactions ---
    existing_at_cols = {c["name"] for c in inspector.get_columns("apple_transactions")}
    new_at_cols = {
        "app_account_token": sa.String(length=36),
        "web_order_line_item_id": sa.String(length=64),
        "storefront": sa.String(length=16),
        "currency": sa.String(length=3),
        "price_micros": sa.BigInteger(),
        "purchase_date": sa.DateTime(timezone=True),
        "revocation_date": sa.DateTime(timezone=True),
        "revocation_reason": sa.String(length=50),
        "credited_at": sa.DateTime(timezone=True),
        "refund_reversed_at": sa.DateTime(timezone=True),
        "signed_transaction_hash": sa.String(length=64),
    }
    for col_name, col_type in new_at_cols.items():
        if col_name not in existing_at_cols:
            op.add_column(
                "apple_transactions", sa.Column(col_name, col_type, nullable=True)
            )

    existing_at_indexes = {
        idx["name"] for idx in inspector.get_indexes("apple_transactions")
    }
    if "ix_apple_transactions_app_account_token" not in existing_at_indexes:
        op.create_index(
            "ix_apple_transactions_app_account_token",
            "apple_transactions",
            ["app_account_token"],
        )
    if "ix_apple_transactions_web_order_line_item_id" not in existing_at_indexes:
        op.create_index(
            "ix_apple_transactions_web_order_line_item_id",
            "apple_transactions",
            ["web_order_line_item_id"],
        )

    # --- apple_iap_accounts ---
    if not conn.execute(
        sa.text("SELECT to_regclass('public.apple_iap_accounts')")
    ).scalar():
        op.create_table(
            "apple_iap_accounts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("account_token_uuid", sa.String(length=36), nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("user_id", name="uq_apple_iap_accounts_user_id"),
            sa.UniqueConstraint(
                "account_token_uuid", name="uq_apple_iap_accounts_token"
            ),
        )
        op.create_index("ix_apple_iap_accounts_id", "apple_iap_accounts", ["id"])

    # --- apple_notifications ---
    if not conn.execute(
        sa.text("SELECT to_regclass('public.apple_notifications')")
    ).scalar():
        op.create_table(
            "apple_notifications",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("notification_uuid", sa.String(length=64), nullable=False),
            sa.Column("notification_type", sa.String(length=64), nullable=False),
            sa.Column("subtype", sa.String(length=64), nullable=True),
            sa.Column("environment", sa.String(length=16), nullable=True),
            sa.Column("transaction_id", sa.String(length=64), nullable=True),
            sa.Column("original_transaction_id", sa.String(length=64), nullable=True),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="received",
            ),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("payload_hash", sa.String(length=64), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column(
                "received_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.UniqueConstraint(
                "notification_uuid", name="uq_apple_notifications_notification_uuid"
            ),
        )
        op.create_index("ix_apple_notifications_id", "apple_notifications", ["id"])
        op.create_index(
            "ix_apple_notifications_notification_uuid",
            "apple_notifications",
            ["notification_uuid"],
        )
        op.create_index(
            "ix_apple_notifications_notification_type",
            "apple_notifications",
            ["notification_type"],
        )
        op.create_index(
            "ix_apple_notifications_environment", "apple_notifications", ["environment"]
        )
        op.create_index(
            "ix_apple_notifications_transaction_id",
            "apple_notifications",
            ["transaction_id"],
        )
        op.create_index(
            "ix_apple_notifications_original_transaction_id",
            "apple_notifications",
            ["original_transaction_id"],
        )

    # --- apple_iap_abuse_events ---
    if not conn.execute(
        sa.text("SELECT to_regclass('public.apple_iap_abuse_events')")
    ).scalar():
        op.create_table(
            "apple_iap_abuse_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column(
                "severity",
                sa.String(length=16),
                nullable=False,
                server_default="warning",
            ),
            sa.Column("transaction_id", sa.String(length=64), nullable=True),
            sa.Column("product_id", sa.String(length=128), nullable=True),
            sa.Column("ip_address", sa.String(length=64), nullable=True),
            sa.Column("details_json", sa.JSON(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
        )
        op.create_index(
            "ix_apple_iap_abuse_events_id", "apple_iap_abuse_events", ["id"]
        )
        op.create_index(
            "ix_apple_iap_abuse_events_user_id", "apple_iap_abuse_events", ["user_id"]
        )
        op.create_index(
            "ix_apple_iap_abuse_events_event_type",
            "apple_iap_abuse_events",
            ["event_type"],
        )
        op.create_index(
            "ix_apple_iap_abuse_events_transaction_id",
            "apple_iap_abuse_events",
            ["transaction_id"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_apple_iap_abuse_events_transaction_id", table_name="apple_iap_abuse_events"
    )
    op.drop_index(
        "ix_apple_iap_abuse_events_event_type", table_name="apple_iap_abuse_events"
    )
    op.drop_index(
        "ix_apple_iap_abuse_events_user_id", table_name="apple_iap_abuse_events"
    )
    op.drop_index("ix_apple_iap_abuse_events_id", table_name="apple_iap_abuse_events")
    op.drop_table("apple_iap_abuse_events")

    op.drop_index(
        "ix_apple_notifications_original_transaction_id",
        table_name="apple_notifications",
    )
    op.drop_index(
        "ix_apple_notifications_transaction_id", table_name="apple_notifications"
    )
    op.drop_index(
        "ix_apple_notifications_environment", table_name="apple_notifications"
    )
    op.drop_index(
        "ix_apple_notifications_notification_type", table_name="apple_notifications"
    )
    op.drop_index(
        "ix_apple_notifications_notification_uuid", table_name="apple_notifications"
    )
    op.drop_index("ix_apple_notifications_id", table_name="apple_notifications")
    op.drop_table("apple_notifications")

    op.drop_index("ix_apple_iap_accounts_id", table_name="apple_iap_accounts")
    op.drop_table("apple_iap_accounts")

    op.drop_index(
        "ix_apple_transactions_web_order_line_item_id", table_name="apple_transactions"
    )
    op.drop_index(
        "ix_apple_transactions_app_account_token", table_name="apple_transactions"
    )
    op.drop_column("apple_transactions", "signed_transaction_hash")
    op.drop_column("apple_transactions", "refund_reversed_at")
    op.drop_column("apple_transactions", "credited_at")
    op.drop_column("apple_transactions", "revocation_reason")
    op.drop_column("apple_transactions", "revocation_date")
    op.drop_column("apple_transactions", "purchase_date")
    op.drop_column("apple_transactions", "price_micros")
    op.drop_column("apple_transactions", "currency")
    op.drop_column("apple_transactions", "storefront")
    op.drop_column("apple_transactions", "web_order_line_item_id")
    op.drop_column("apple_transactions", "app_account_token")
