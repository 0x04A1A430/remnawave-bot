"""add sent notifications table"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '8fd1e338eb45'
down_revision: Union[str, None] = '3d9b35c6bd8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sent_notifications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('subscription_id', sa.Integer(), sa.ForeignKey('subscriptions.id'), nullable=False),
        sa.Column('notification_type', sa.String(length=50), nullable=False),
        sa.Column('days_before', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'subscription_id', 'notification_type', 'days_before', name='uq_sent_notifications'),
    )


def downgrade() -> None:
    op.drop_table('sent_notifications')
