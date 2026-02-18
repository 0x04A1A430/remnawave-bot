"""Programmatic Alembic migration runner for bot startup."""

from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect


logger = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _PROJECT_ROOT / 'alembic.ini'


def _get_alembic_config() -> Config:
    """Build Alembic Config pointing at the project root."""
    from app.config import settings

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option('sqlalchemy.url', settings.get_database_url())
    return cfg


async def _needs_auto_stamp() -> bool:
    """Check if DB has existing tables but no alembic_version (transition from universal_migration)."""
    from app.database.database import engine

    async with engine.connect() as conn:
        has_alembic = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table('alembic_version'))
        if has_alembic:
            return False
        has_users = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table('users'))
        return has_users


_INITIAL_REVISION = '0001'


async def run_alembic_upgrade() -> None:
    """Run ``alembic upgrade head``, auto-stamping existing databases first."""
    import asyncio

    if await _needs_auto_stamp():
        logger.warning(
            'Обнаружена существующая БД без alembic_version — автоматический stamp 0001 (переход с universal_migration)'
        )
        await _stamp_alembic_revision(_INITIAL_REVISION)

    cfg = _get_alembic_config()
    loop = asyncio.get_running_loop()
    # run_in_executor offloads to a thread where env.py can safely
    # call asyncio.run() to create its own event loop.
    await loop.run_in_executor(None, command.upgrade, cfg, 'head')
    logger.info('Alembic миграции применены')


async def stamp_alembic_head() -> None:
    """Stamp the DB as being at head without running migrations (for existing DBs)."""
    await _stamp_alembic_revision('head')


async def _stamp_alembic_revision(revision: str) -> None:
    """Stamp the DB at a specific revision without running migrations."""
    import asyncio

    cfg = _get_alembic_config()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, command.stamp, cfg, revision)
    logger.info('Alembic: база отмечена как актуальная', revision=revision)
