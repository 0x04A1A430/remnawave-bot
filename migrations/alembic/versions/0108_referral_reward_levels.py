"""referral_reward_levels + компоненты награды в referral_earnings

Revision ID: 0108
Revises: 0107
Create Date: 2026-08-26

Многоуровневая реферальная схема с наградой деньгами и/или днями подписки.

Конфигурация уровней — отдельная таблица, а не ключи в Settings: ключ, заданный в
.env, попадает в ENV_OVERRIDE_KEYS и перестаёт редактироваться из админки, а вся
реферальная конфигурация на типовой установке именно так и залочена.

Расширение ``referral_earnings`` обязательно, а не опционально: вся статистика,
партнёрка и расчёт доступного к выводу баланса построены на сумме ``amount_kopeks``,
и награда в днях туда физически не помещается. Старые строки бэкфиллятся в
reward_type='money', level=1 — иначе группировки дадут NULL-бакет.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0108'
down_revision: Union[str, None] = '0107'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_EARNING_COLUMNS = (
    ('reward_type', sa.Column('reward_type', sa.String(length=10), nullable=False, server_default='money')),
    ('level', sa.Column('level', sa.Integer(), nullable=False, server_default='1')),
    ('days_granted', sa.Column('days_granted', sa.Integer(), nullable=False, server_default='0')),
    ('tariff_id', sa.Column('tariff_id', sa.Integer(), nullable=True)),
)

# FK вешается отдельным шагом, а не инлайном в add_column: SQLite не умеет
# ALTER TABLE ADD CONSTRAINT и падает на этом с NotImplementedError.
_EARNING_TARIFF_FK = 'fk_referral_earnings_tariff_id'


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'referral_reward_levels' not in tables:
        op.create_table(
            'referral_reward_levels',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('level', sa.Integer(), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('reward_mode', sa.String(length=10), nullable=False, server_default='money'),
            sa.Column('trigger', sa.String(length=20), nullable=False, server_default='first_topup'),
            sa.Column('referrer_percent', sa.Integer(), nullable=True),
            sa.Column('referrer_fixed_kopeks', sa.Integer(), nullable=True),
            sa.Column('referrer_days', sa.Integer(), nullable=False, server_default='0'),
            sa.Column(
                'referrer_tariff_id',
                sa.Integer(),
                sa.ForeignKey('tariffs.id', ondelete='SET NULL'),
                nullable=True,
            ),
            sa.Column('referee_fixed_kopeks', sa.Integer(), nullable=True),
            sa.Column('referee_days', sa.Integer(), nullable=False, server_default='0'),
            sa.Column(
                'referee_tariff_id',
                sa.Integer(),
                sa.ForeignKey('tariffs.id', ondelete='SET NULL'),
                nullable=True,
            ),
            sa.Column('max_payments', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index('ux_referral_reward_levels_level', 'referral_reward_levels', ['level'], unique=True)

    if 'referral_earnings' not in tables:
        return

    existing_cols = {col['name'] for col in inspector.get_columns('referral_earnings')}
    added: list[str] = []
    for name, column in _EARNING_COLUMNS:
        if name not in existing_cols:
            op.add_column('referral_earnings', column)
            added.append(name)

    if 'tariff_id' in added and bind.dialect.name != 'sqlite':
        op.create_foreign_key(
            _EARNING_TARIFF_FK,
            'referral_earnings',
            'tariffs',
            ['tariff_id'],
            ['id'],
            ondelete='SET NULL',
        )

    existing_indexes = {idx['name'] for idx in inspector.get_indexes('referral_earnings')}
    if 'ix_referral_earnings_reward_type' not in existing_indexes and 'reward_type' in existing_cols | set(added):
        op.create_index('ix_referral_earnings_reward_type', 'referral_earnings', ['reward_type'])
    if 'ix_referral_earnings_level' not in existing_indexes and 'level' in existing_cols | set(added):
        op.create_index('ix_referral_earnings_level', 'referral_earnings', ['level'])

    # Бэкфилл: всё, что начислено до перехода, — это деньги первого уровня.
    # Без него группировки по reward_type/level дадут NULL-бакет в статистике.
    if added:
        op.execute(
            sa.text(
                "UPDATE referral_earnings SET reward_type = 'money', level = 1, days_granted = 0 "
                'WHERE reward_type IS NULL OR level IS NULL'
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'referral_earnings' in tables:
        existing_indexes = {idx['name'] for idx in inspector.get_indexes('referral_earnings')}
        for index_name in ('ix_referral_earnings_reward_type', 'ix_referral_earnings_level'):
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name='referral_earnings')

        existing_fks = {fk['name'] for fk in inspector.get_foreign_keys('referral_earnings')}
        if _EARNING_TARIFF_FK in existing_fks and bind.dialect.name != 'sqlite':
            op.drop_constraint(_EARNING_TARIFF_FK, 'referral_earnings', type_='foreignkey')

        existing_cols = {col['name'] for col in inspector.get_columns('referral_earnings')}
        for name, _column in reversed(_EARNING_COLUMNS):
            if name in existing_cols:
                op.drop_column('referral_earnings', name)

    if 'referral_reward_levels' in tables:
        op.drop_table('referral_reward_levels')
