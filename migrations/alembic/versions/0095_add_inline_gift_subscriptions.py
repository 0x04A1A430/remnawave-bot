"""add inline_gift_subscriptions table

Revision ID: 0053
Revises: 0052
Create Date: 2026-04-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0095'
down_revision: Union[str, None] = '0094'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT to_regclass('public.inline_gift_subscriptions')")).scalar():
        return
    op.create_table(
        'inline_gift_subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('gift_code', sa.String(64), nullable=False, unique=True),
        sa.Column('recipient_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('sender_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('days', sa.Integer(), nullable=False),
        sa.Column('traffic_limit_gb', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('device_limit', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('inline_message_id', sa.String(255), nullable=True),
        sa.Column('inline_chat_id', sa.BigInteger(), nullable=True),
        sa.Column('inline_msg_id', sa.BigInteger(), nullable=True),
        sa.Column('is_activated', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('activated_by_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('subscription_id', sa.Integer(), sa.ForeignKey('subscriptions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_inline_gifts_gift_code', 'inline_gift_subscriptions', ['gift_code'], unique=True)
    op.create_index('ix_inline_gifts_recipient_tg_id', 'inline_gift_subscriptions', ['recipient_telegram_id'])
    op.create_index('ix_inline_gifts_sender_id', 'inline_gift_subscriptions', ['sender_user_id'])


def downgrade() -> None:
    op.drop_index('ix_inline_gifts_sender_id', table_name='inline_gift_subscriptions')
    op.drop_index('ix_inline_gifts_recipient_tg_id', table_name='inline_gift_subscriptions')
    op.drop_index('ix_inline_gifts_gift_code', table_name='inline_gift_subscriptions')
    op.drop_table('inline_gift_subscriptions')
