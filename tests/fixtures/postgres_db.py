"""Сессии к настоящему PostgreSQL для тестов, которым SQLite не годится.

SQLite молча игнорирует ``FOR UPDATE``, не проверяет длину ``VARCHAR(n)`` и
иначе хранит время и JSON. Всё, что зависит от этих свойств — блокировки строк,
конкурентная обработка уведомлений, ограничения схемы — на SQLite проверяется
только для вида. Прод работает на PostgreSQL, поэтому такие проверки должны
идти на нём.

Схема создаётся ``Base.metadata.create_all`` — ровно так, как её получает
свежая боевая база: ``app/database/migrations.py`` на пустой БД делает
``create_all`` и ``alembic stamp head``, а не прогон цепочки миграций. Прогнать
цепочку с нуля и нельзя: ревизия ``0001`` сама вызывает ``create_all``, после
чего ревизия ``0021`` падает на ``operator does not exist: json <> unknown`` —
данные уже в новом формате. То есть ``create_all`` здесь не упрощение, а
воспроизведение боевого пути.

База берётся из ``TEST_DATABASE_URL`` (asyncpg-URL). Если переменной нет,
тесты пропускаются — окружение без PostgreSQL не должно ронять прогон. В CI
это опасно: пропуск выглядит как успех. Поэтому там выставляется
``REQUIRE_POSTGRES_TESTS=1``, и отсутствие базы становится падением.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncIterator, Iterator, Sequence

import pytest
from sqlalchemy import Table, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.models import Base


TEST_DATABASE_URL_ENV = 'TEST_DATABASE_URL'
REQUIRE_POSTGRES_ENV = 'REQUIRE_POSTGRES_TESTS'

_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'y', 'on'})

# Схему достаточно создать один раз за прогон: дальше тесты чистят свои таблицы
# через TRUNCATE (9 мс против 0.8 с на пересоздание всех 118 таблиц).
_schema_created_for: str | None = None


def postgres_dsn() -> str | None:
    """URL тестовой базы из окружения или ``None``."""
    return os.environ.get(TEST_DATABASE_URL_ENV, '').strip() or None


def postgres_is_required() -> bool:
    """Требует ли окружение, чтобы тесты на PostgreSQL действительно шли."""
    return os.environ.get(REQUIRE_POSTGRES_ENV, '').strip().lower() in _TRUE_VALUES


def require_postgres_dsn() -> str:
    """URL живого PostgreSQL, иначе пропуск теста (или падение, если требуется)."""
    dsn = postgres_dsn()
    if dsn:
        return dsn

    reason = f'{TEST_DATABASE_URL_ENV} не задан — тесты на настоящем PostgreSQL пропущены'
    if postgres_is_required():
        pytest.fail(f'{reason}, но {REQUIRE_POSTGRES_ENV} требует их запуска')
    pytest.skip(reason)
    raise AssertionError('недостижимо')  # pragma: no cover - pytest.skip бросает исключение


@contextlib.contextmanager
def real_asyncpg() -> Iterator[None]:
    """Снимает заглушку ``sys.modules['asyncpg']``, поставленную conftest.

    conftest подставляет пустой модуль для окружений без драйвера, и он
    перекрывает физически установленный пакет — ``create_async_engine`` падает.
    Обратно заглушка возвращается только если настоящий драйвер так и не
    загрузился: подменять уже импортированный диалектом модуль нельзя.
    """
    stub = sys.modules.get('asyncpg')
    if stub is None or hasattr(stub, 'connect'):
        yield
        return

    del sys.modules['asyncpg']
    try:
        yield
    finally:
        sys.modules.setdefault('asyncpg', stub)


async def _recreate_schema(dsn: str) -> None:
    """Сносит содержимое базы и создаёт схему проекта заново."""
    engine = create_async_engine(dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text('DROP SCHEMA IF EXISTS public CASCADE'))
            await conn.execute(text('CREATE SCHEMA public'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope='session')
def postgres_database() -> str:
    """URL тестовой базы, в которой уже создана полная схема проекта.

    Фикстура синхронная намеренно: conftest создаёт отдельный цикл событий на
    каждый тест, поэтому движок нельзя переносить между тестами. Схема строится
    в собственном одноразовом цикле, движок тут же закрывается.
    """
    dsn = require_postgres_dsn()

    global _schema_created_for
    if _schema_created_for != dsn:
        with real_asyncpg():
            asyncio.run(_recreate_schema(dsn))
        _schema_created_for = dsn
    return dsn


async def truncate_tables(engine: AsyncEngine, tables: Sequence[Table]) -> None:
    """Очищает переданные таблицы вместе со счётчиками идентификаторов."""
    if not tables:
        return
    targets = ', '.join(f'"{table.name}"' for table in tables)
    async with engine.begin() as conn:
        await conn.execute(text(f'TRUNCATE {targets} RESTART IDENTITY CASCADE'))


@contextlib.asynccontextmanager
async def postgres_engine(dsn: str, tables: Sequence[Table] = ()) -> AsyncIterator[AsyncEngine]:
    """Движок к тестовой базе; переданные таблицы очищаются до и после теста.

    ``NullPool`` здесь обязателен: каждая сессия получает собственное
    соединение, иначе тесты на блокировки проверяли бы блокировку сессии самой
    себя — а такой запрос не ждёт и проходит насквозь.
    """
    with real_asyncpg():
        engine = create_async_engine(dsn, poolclass=NullPool)
    try:
        await truncate_tables(engine, tables)
        yield engine
    finally:
        with contextlib.suppress(Exception):
            await truncate_tables(engine, tables)
        await engine.dispose()


@contextlib.asynccontextmanager
async def postgres_session(dsn: str, tables: Sequence[Table] = ()) -> AsyncIterator[AsyncSession]:
    """Одна сессия к тестовой базе (зеркало ``memory_session``, но на PostgreSQL)."""
    async with postgres_engine(dsn, tables) as engine:
        # autoflush=False повторяет прод (app/database/database.py).
        maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with maker() as session:
            yield session


@contextlib.asynccontextmanager
async def postgres_sessions(
    dsn: str,
    tables: Sequence[Table] = (),
    count: int = 2,
) -> AsyncIterator[tuple[AsyncSession, ...]]:
    """Несколько независимых сессий, каждая на своём соединении.

    Это рабочий инструмент для проверок конкурентности: две сессии — две
    транзакции, между которыми PostgreSQL действительно расставляет блокировки.
    """
    async with postgres_engine(dsn, tables) as engine:
        maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        sessions = [maker() for _ in range(count)]
        try:
            yield tuple(sessions)
        finally:
            for session in sessions:
                with contextlib.suppress(Exception):
                    await session.rollback()
                await session.close()


async def wait_for_lock_waiter(session: AsyncSession, timeout: float = 5.0, poll: float = 0.02) -> None:
    """Ждёт, пока в тестовой базе появится сессия, стоящая в очереди за блокировкой.

    Без этого тесты на конкурентность пришлось бы синхронизировать ``sleep``:
    держатель блокировки не знает, успел ли соперник дойти до своего запроса.
    Здесь ожидание видно самой базе — ``pg_stat_activity`` показывает бэкенд,
    остановленный на ожидании блокировки.

    Наблюдатель должен быть ТРЕТЬЕЙ сессией: держатель занят, ожидающий стоит.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    query = text(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    while asyncio.get_running_loop().time() < deadline:
        result = await session.execute(query)
        if (result.scalar() or 0) > 0:
            return
        # Наблюдатель не должен держать снимок: иначе он сам мешает уборке.
        await session.rollback()
        await asyncio.sleep(poll)

    raise AssertionError(f'за {timeout} с никто не встал в очередь за блокировкой — конкуренции не возникло')
