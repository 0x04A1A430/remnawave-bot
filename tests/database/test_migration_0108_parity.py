"""Свежая установка и обновлённая обязаны прийти к одной схеме.

Свежая база создаётся ``Base.metadata.create_all`` по модели, обновлённая —
миграцией. Любое расхождение между ними живёт долго и тихо: autogenerate вечно
показывает фантомную разницу, а запрос, опирающийся на индекс, ведёт себя
по-разному на двух установках одного и того же бота.

Проверяется через SQLite: диалект другой, но состав колонок, индексов и внешних
ключей — то, что расходилось, — от него не зависит.
"""

import importlib.util
import pathlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database.models import Base, ReferralEarning, ReferralRewardLevel


MIGRATION = pathlib.Path(__file__).resolve().parents[2] / ('migrations/alembic/versions/0108_referral_reward_levels.py')


def _load_migration():
    spec = importlib.util.spec_from_file_location('m0108', MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fresh_install(path: pathlib.Path):
    """База, созданная по модели, — как на новой установке."""
    engine = sa.create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(
        engine,
        tables=[
            Base.metadata.tables['tariffs'],
            ReferralRewardLevel.__table__,
            ReferralEarning.__table__,
        ],
        checkfirst=True,
    )
    return engine


def _upgraded_install(path: pathlib.Path):
    """База в состоянии «до 0108», прогнанная миграцией."""
    engine = sa.create_engine(f'sqlite:///{path}')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE tariffs (id INTEGER PRIMARY KEY, name VARCHAR(100))'))
        conn.execute(
            sa.text(
                'CREATE TABLE referral_earnings ('
                ' id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, referral_id INTEGER NOT NULL,'
                ' amount_kopeks INTEGER NOT NULL, reason VARCHAR(100) NOT NULL,'
                ' referral_transaction_id INTEGER, campaign_id INTEGER, created_at TIMESTAMP)'
            )
        )

    module = _load_migration()
    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            module.upgrade()
    return engine


@pytest.fixture
def both(tmp_path):
    fresh = _fresh_install(tmp_path / 'fresh.db')
    upgraded = _upgraded_install(tmp_path / 'upgraded.db')
    return sa.inspect(fresh), sa.inspect(upgraded)


def test_reward_levels_columns_match(both):
    fresh, upgraded = both
    fresh_cols = {c['name'] for c in fresh.get_columns('referral_reward_levels')}
    upgraded_cols = {c['name'] for c in upgraded.get_columns('referral_reward_levels')}
    assert fresh_cols == upgraded_cols


def test_reward_levels_indexes_match(both):
    """Именно это и расходилось: create_all делает ix_..._id, миграция — не делала."""
    fresh, upgraded = both
    fresh_idx = {i['name'] for i in fresh.get_indexes('referral_reward_levels')}
    upgraded_idx = {i['name'] for i in upgraded.get_indexes('referral_reward_levels')}
    assert fresh_idx == upgraded_idx, (
        f'только в свежей: {fresh_idx - upgraded_idx}, только в обновлённой: {upgraded_idx - fresh_idx}'
    )


def test_new_earning_columns_match(both):
    fresh, upgraded = both
    new = {'reward_type', 'level', 'days_granted', 'tariff_id'}
    fresh_cols = {c['name'] for c in fresh.get_columns('referral_earnings')}
    upgraded_cols = {c['name'] for c in upgraded.get_columns('referral_earnings')}
    assert new <= fresh_cols
    assert new <= upgraded_cols


def test_no_duplicate_tariff_foreign_key(both):
    """Миграция не должна вешать второй FK поверх созданного по модели.

    На свежей установке ограничение безымянное — PostgreSQL называет его сам, и
    сверка по имени его не видит. Без проверки по колонке рядом появлялся второй,
    дублирующий внешний ключ на ту же tariff_id.
    """
    fresh, upgraded = both
    for inspector, label in ((fresh, 'свежая'), (upgraded, 'обновлённая')):
        tariff_fks = [
            fk
            for fk in inspector.get_foreign_keys('referral_earnings')
            if 'tariff_id' in (fk.get('constrained_columns') or [])
        ]
        assert len(tariff_fks) <= 1, f'{label}: внешних ключей на tariff_id {len(tariff_fks)}'
