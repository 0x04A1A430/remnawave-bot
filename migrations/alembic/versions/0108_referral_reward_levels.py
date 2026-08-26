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
и награда в днях туда физически не помещается. Отдельный UPDATE для старых строк не
нужен: колонки добавляются NOT NULL с server_default, поэтому PostgreSQL проставляет
'money'/1/0 всем существующим строкам сам.
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
        op.create_index('ix_referral_reward_levels_level', 'referral_reward_levels', ['level'], unique=True)

    if 'referral_earnings' not in tables:
        return

    existing_cols = {col['name'] for col in inspector.get_columns('referral_earnings')}
    added: list[str] = []
    for name, column in _EARNING_COLUMNS:
        if name not in existing_cols:
            op.add_column('referral_earnings', column)
            added.append(name)

    existing_fks = {fk['name'] for fk in inspector.get_foreign_keys('referral_earnings')}
    tariff_column_present = 'tariff_id' in existing_cols | set(added)
    if tariff_column_present and _EARNING_TARIFF_FK not in existing_fks and bind.dialect.name != 'sqlite':
        op.create_foreign_key(
            _EARNING_TARIFF_FK,
            'referral_earnings',
            'tariffs',
            ['tariff_id'],
            ['id'],
            ondelete='SET NULL',
        )

    # Индексы по reward_type/level не создаются намеренно: запросы, которые их
    # читают, либо уже сужены индексом по user_id, либо агрегируют всю таблицу.
    # Зато их построение — блокирующий CREATE INDEX внутри того же ACCESS
    # EXCLUSIVE, что взяли ALTER'ы выше: на большой таблице начислений это
    # остановило бы запись на всё время сборки, причём на старте бота.


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'referral_earnings' in tables:
        existing_fks = {fk['name'] for fk in inspector.get_foreign_keys('referral_earnings')}
        if _EARNING_TARIFF_FK in existing_fks and bind.dialect.name != 'sqlite':
            op.drop_constraint(_EARNING_TARIFF_FK, 'referral_earnings', type_='foreignkey')

        existing_cols = {col['name'] for col in inspector.get_columns('referral_earnings')}
        for name, _column in reversed(_EARNING_COLUMNS):
            if name in existing_cols:
                op.drop_column('referral_earnings', name)

    if 'referral_reward_levels' in tables:
        op.drop_table('referral_reward_levels')
