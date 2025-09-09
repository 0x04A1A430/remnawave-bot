"""add cascade delete to sent notifications"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'cbd1be472f3d'
down_revision: Union[str, None] = '8fd1e338eb45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('sent_notifications_user_id_fkey', 'sent_notifications', type_='foreignkey')
    op.drop_constraint('sent_notifications_subscription_id_fkey', 'sent_notifications', type_='foreignkey')
    op.create_foreign_key('fk_sent_notifications_user_id_users', 'sent_notifications', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('fk_sent_notifications_subscription_id_subscriptions', 'sent_notifications', 'subscriptions', ['subscription_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint('fk_sent_notifications_user_id_users', 'sent_notifications', type_='foreignkey')
    op.drop_constraint('fk_sent_notifications_subscription_id_subscriptions', 'sent_notifications', type_='foreignkey')
    op.create_foreign_key('sent_notifications_user_id_fkey', 'sent_notifications', 'users', ['user_id'], ['id'])
    op.create_foreign_key('sent_notifications_subscription_id_fkey', 'sent_notifications', 'subscriptions', ['subscription_id'], ['id'])
