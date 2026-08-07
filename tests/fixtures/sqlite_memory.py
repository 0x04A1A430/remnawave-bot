"""In-memory SQLite fixture for tests that exercise models/CRUD against a real DB.

Ports the upstream ``memory_session`` test helper. Runs ``create_all`` on the
subset of tables the test declares, because PostgreSQL ``JSONB`` columns (e.g.
User.notification_settings) are not natively supported on SQLite.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import AsyncIterator, Sequence

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.database.models import Base


@compiles(JSONB, 'sqlite')
def _compile_jsonb_on_sqlite(type_, compiler, **kw) -> str:
    """SQLite cannot compile JSONB, emit plain JSON instead."""
    return 'JSON'


def ensure_real_aiosqlite(monkeypatch) -> None:
    """Remove a fake aiosqlite stubbed into sys.modules by the session conftest."""
    stub = sys.modules.get('aiosqlite')
    if stub is not None and not hasattr(stub, 'connect'):
        monkeypatch.delitem(sys.modules, 'aiosqlite', raising=False)


@contextlib.asynccontextmanager
async def memory_session(monkeypatch, tables: Sequence[Table]) -> AsyncIterator[AsyncSession]:
    """Yield an in-memory SQLite session with only the given tables created."""
    ensure_real_aiosqlite(monkeypatch)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=list(tables)))
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await engine.dispose()
