"""Add send_on_every_start and last_pinned_message tracking

Revision ID: b2f5fdc69a2b
Revises: 7a3c0b8f5b84
Create Date: 2025-01-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b2f5fdc69a2b"
down_revision = "7a3c0b8f5b84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pinned_messages",
        sa.Column(
            "send_on_every_start",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )

    op.add_column(
        "users",
        sa.Column("last_pinned_message_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_last_pinned_message",
        "users",
        "pinned_messages",
        ["last_pinned_message_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_last_pinned_message", "users", type_="foreignkey")
    op.drop_column("users", "last_pinned_message_id")
    op.drop_column("pinned_messages", "send_on_every_start")
