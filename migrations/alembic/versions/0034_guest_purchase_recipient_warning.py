"""Add recipient_warning column to guest_purchases.

Revision ID: 0034
Revises: 0033
"""

from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if the column already exists before adding it
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        columns = inspector.get_columns("guest_purchases")
        if not any(c["name"] == "recipient_warning" for c in columns):
            op.add_column(
                "guest_purchases",
                sa.Column("recipient_warning", sa.String(50), nullable=True),
            )
        else:
            print(
                "Column 'recipient_warning' already exists in 'guest_purchases'. Skipping addition."
            )


def downgrade() -> None:
    op.drop_column("guest_purchases", "recipient_warning")
