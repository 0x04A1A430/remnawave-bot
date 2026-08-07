"""coupon_batches.max_per_user

0 (без ограничения) — прежнее поведение, пользователь может активировать любое
количество купонов партии. Больше 1 ограничивает активации одного пользователя.

Revision ID: 0110
Revises: 0109
"""

from alembic import op
import sqlalchemy as sa


revision = '0110'
down_revision = '0109'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('coupon_batches') as batch:
        batch.add_column(sa.Column('max_per_user', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('coupon_batches') as batch:
        batch.drop_column('max_per_user')