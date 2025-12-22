"""add send_before_menu to pinned messages

Revision ID: 7a3c0b8f5b84
Revises: 5f2a3e099427
Create Date: 2025-02-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7a3c0b8f5b84"
down_revision = "5f2a3e099427"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pinned_messages",
        sa.Column(
            "send_before_menu",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    op.drop_column("pinned_messages", "send_before_menu")
