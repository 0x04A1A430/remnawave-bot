"""
Проверка SQL миграции 0088 (схлопывание дублей подписок одного тарифа).

Гоняем НАСТОЯЩИЙ ``_DEDUPE_SQL`` из миграции на in-memory SQLite (поддерживает
оконные функции и NULLS LAST), на сценарии со скриншота репорта + крайних
случаях. Цель — убедиться, что удаляются ТОЛЬКО лишние expired/disabled дубли,
а живые/одиночные/pending записи остаются.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest


_MIG_PATH = (
    Path(__file__).resolve().parents[2] / 'migrations' / 'alembic' / 'versions' / '0088_dedupe_tariff_subscriptions.py'
)


def _load_dedupe_sql() -> str:
    spec = importlib.util.spec_from_file_location('mig_0088', _MIG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._DEDUPE_SQL


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.executescript(
        """
        CREATE TABLE subscriptions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            tariff_id INTEGER,
            status TEXT NOT NULL,
            end_date TEXT,
            is_trial INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    yield c
    c.close()


def _insert(conn, rows):
    conn.executemany(
        'INSERT INTO subscriptions (id, user_id, tariff_id, status, end_date, is_trial) VALUES (?,?,?,?,?,?)',
        rows,
    )
    conn.commit()


def _ids(conn):
    return {r[0] for r in conn.execute('SELECT id FROM subscriptions').fetchall()}


def test_dedupe_matches_report_scenario(conn):
    # (id, user_id, tariff_id, status, end_date, is_trial)
    _insert(
        conn,
        [
            # user 1, тариф 1 (Стандартный): active + 2 истёкших → остаётся active
            (1, 1, 1, 'active', '2026-06-16', 0),
            (2, 1, 1, 'expired', '2026-05-18', 0),
            (3, 1, 1, 'expired', '2026-04-04', 0),
            # user 1, тариф 2 (Премиум): 2 истёкших → остаётся самый свежий (02.06)
            (4, 1, 2, 'expired', '2026-06-02', 0),
            (5, 1, 2, 'expired', '2026-05-04', 0),
            # user 2, тариф 1: одна active → не трогаем
            (6, 2, 1, 'active', '2026-06-20', 0),
            # user 4, тариф 1: active + disabled (убитый триал, is_trial=0) → остаётся active
            (8, 4, 1, 'active', '2026-06-10', 0),
            (9, 4, 1, 'disabled', '2026-05-01', 0),
            # user 5, тариф 1: expired + pending → pending НЕ под удаление, обе остаются
            (10, 5, 1, 'expired', '2026-05-30', 0),
            (11, 5, 1, 'pending', None, 0),
            # триал того же тарифа (is_trial=1) — вне дедупа вообще
            (12, 1, 1, 'expired', '2026-03-01', 1),
        ],
    )

    conn.execute(_load_dedupe_sql())
    conn.commit()

    survived = _ids(conn)
    # удалены ровно лишние expired/disabled дубли
    assert survived == {1, 4, 6, 8, 10, 11, 12}
    # ни одна active не удалена
    assert {1, 6, 8} <= survived
    # триал не тронут
    assert 12 in survived
    # pending не тронут
    assert 11 in survived


def test_dedupe_never_deletes_alive_even_if_outranked(conn):
    # Вырожденный случай: active с более ранней датой, чем истёкшая (не должен удаляться).
    _insert(
        conn,
        [
            (1, 1, 1, 'expired', '2026-06-30', 0),  # самая свежая дата, но истёкшая
            (2, 1, 1, 'active', '2026-06-01', 0),  # активная, дата раньше
        ],
    )
    conn.execute(_load_dedupe_sql())
    conn.commit()
    survived = _ids(conn)
    # active (rn=1 по приоритету статуса) остаётся; expired-дубль удаляется
    assert 2 in survived
    assert survived == {2}


def test_dedupe_is_idempotent(conn):
    _insert(
        conn,
        [
            (1, 1, 1, 'active', '2026-06-16', 0),
            (2, 1, 1, 'expired', '2026-05-18', 0),
        ],
    )
    conn.execute(_load_dedupe_sql())
    conn.commit()
    first = _ids(conn)
    conn.execute(_load_dedupe_sql())
    conn.commit()
    assert _ids(conn) == first == {1}


def test_dedupe_keeps_single_rows_untouched(conn):
    # Никаких дублей — ничего не удаляем.
    _insert(
        conn,
        [
            (1, 1, 1, 'expired', '2026-05-18', 0),
            (2, 1, 2, 'active', '2026-06-16', 0),
            (3, 2, 1, 'disabled', '2026-04-01', 0),
        ],
    )
    conn.execute(_load_dedupe_sql())
    conn.commit()
    assert _ids(conn) == {1, 2, 3}
