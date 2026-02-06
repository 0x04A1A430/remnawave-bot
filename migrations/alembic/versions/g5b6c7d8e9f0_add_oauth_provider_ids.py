from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g5b6c7d8e9f0'
down_revision: Union[str, None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('google_id', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('yandex_id', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('discord_id', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('vk_id', sa.BigInteger(), nullable=True))

    op.create_unique_constraint('uq_users_google_id', 'users', ['google_id'])
    op.create_unique_constraint('uq_users_yandex_id', 'users', ['yandex_id'])
    op.create_unique_constraint('uq_users_discord_id', 'users', ['discord_id'])
    op.create_unique_constraint('uq_users_vk_id', 'users', ['vk_id'])

    op.create_index('ix_users_google_id', 'users', ['google_id'])
    op.create_index('ix_users_yandex_id', 'users', ['yandex_id'])
    op.create_index('ix_users_discord_id', 'users', ['discord_id'])
    op.create_index('ix_users_vk_id', 'users', ['vk_id'])


def downgrade() -> None:
    op.drop_index('ix_users_vk_id', table_name='users')
    op.drop_index('ix_users_discord_id', table_name='users')
    op.drop_index('ix_users_yandex_id', table_name='users')
    op.drop_index('ix_users_google_id', table_name='users')

    op.drop_constraint('uq_users_vk_id', 'users', type_='unique')
    op.drop_constraint('uq_users_discord_id', 'users', type_='unique')
    op.drop_constraint('uq_users_yandex_id', 'users', type_='unique')
    op.drop_constraint('uq_users_google_id', 'users', type_='unique')

    op.drop_column('users', 'vk_id')
    op.drop_column('users', 'discord_id')
    op.drop_column('users', 'yandex_id')
    op.drop_column('users', 'google_id')
