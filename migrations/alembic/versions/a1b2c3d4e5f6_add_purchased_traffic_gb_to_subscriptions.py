"""add purchased_traffic_gb to subscriptions

Revision ID: a1b2c3d4e5f6
Revises: f4a5b6c7d8e9
Create Date: 2024-12-25 14:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'f4a5b6c7d8e9'
branch_labels = None
depends_on = None


def upgrade():
    # Добавляем колонку purchased_traffic_gb для отслеживания докупленного трафика
    op.add_column('subscriptions', sa.Column('purchased_traffic_gb', sa.Integer(), nullable=True, server_default='0'))
    
    # Устанавливаем NOT NULL после добавления значения по умолчанию
    op.alter_column('subscriptions', 'purchased_traffic_gb', nullable=False, server_default=None)


def downgrade():
    op.drop_column('subscriptions', 'purchased_traffic_gb')
