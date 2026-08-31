"""Сессия к настоящему PostgreSQL для тестов, которым SQLite не годится.

SQLite молча игнорирует ``FOR UPDATE``, не проверяет длину ``VARCHAR(n)`` и
иначе хранит время и JSON. Всё, что зависит от этих свойств — блокировки строк,
конкурентная обработка уведомлений, ограничения схемы, — на SQLite проверяется
только для вида. Прод работает на PostgreSQL, поэтому такие проверки должны
идти на нём.

База берётся из ``TEST_DATABASE_URL`` (asyncpg-URL). Если переменной нет или
сервер недоступен, тесты пропускаются — окружение без Postgres не должно ронять
прогон.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Sequence

import pytest
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models import Base


TEST_DATABASE_URL_ENV = 'TEST_DATABASE_URL'


def postgres_url() -> str | None:
    return os.environ.get(TEST_DATABASE_URL_ENV) or None


def require_postgres() -> str:
    """URL живого PostgreSQL либо пропуск теста."""
    url = postgres_url()
    if not url:
        pytest.skip(f'{TEST_DATABASE_URL_ENV} не задан — тесты на реальном PostgreSQL пропущены')
    return url


def _ensure_real_asyncpg(monkeypatch) -> None:
    """Снять заглушку sys.modules['asyncpg'] из conftest.

    conftest подставляет пустой модуль для окружений без драйвера, и он
    перекрывает физически установленный пакет.
    """
    import sys

    stub = sys.modules.get('asyncpg')
    if stub is not None and not hasattr(stub, 'connect'):
        monkeypatch.delitem(sys.modules, 'asyncpg', raising=False)


@contextlib.asynccontextmanager
async def postgres_engine(monkeypatch, tables: Sequence[Table]):
    """Движок к тестовой базе с пересозданными таблицами из списка."""
    url = require_postgres()
    _ensure_real_asyncpg(monkeypatch)

    engine = create_async_engine(url, poolclass=None)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=list(tables)))
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=list(tables)))
        yield engine
    finally:
        with contextlib.suppress(Exception):
            async with engine.begin() as conn:
                await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=list(tables)))
        await engine.dispose()


@contextlib.asynccontextmanager
async def postgres_session(monkeypatch, tables: Sequence[Table]) -> AsyncIterator[AsyncSession]:
    """Одна сессия к тестовой базе (зеркало memory_session, но на PostgreSQL)."""
    async with postgres_engine(monkeypatch, tables) as engine:
        maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with maker() as session:
            yield session
