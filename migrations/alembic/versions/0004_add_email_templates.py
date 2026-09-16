"""add email_templates table

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-18

Adds email_templates table for custom email template overrides.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return table in inspector.get_table_names()


def upgrade() -> None:
    if _has_table('email_templates'):
        return

    op.create_table(
        'email_templates',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('notification_type', sa.String(100), nullable=False),
        sa.Column('language', sa.String(10), nullable=False),
        sa.Column('subject', sa.String(500), nullable=False),
        sa.Column('body_html', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('notification_type', 'language', name='uq_email_templates_type_lang'),
    )
    op.create_index('ix_email_templates_notification_type', 'email_templates', ['notification_type'])


def downgrade() -> None:
    op.drop_index('ix_email_templates_notification_type', table_name='email_templates')
    op.drop_table('email_templates')
