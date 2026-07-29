"""Медиа стартового меню бота (видео вместо логотипа-картинки).

Хранится Telegram ``file_id`` загруженного через кабинет видео: повторная
отправка по file_id мгновенна и не тратит трафик, а сам файл живёт на стороне
Telegram (тот же приём, что у ``/cabinet/media/upload`` и кеша логотипа).

Пусто/не задано — меню отправляется как раньше: фото-логотип при
``ENABLE_LOGO_MODE`` либо обычный текст.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SystemSetting


logger = structlog.get_logger(__name__)

START_VIDEO_FILE_ID_KEY = 'BOT_START_VIDEO_FILE_ID'

# Кеш значения на процесс: /start дёргается часто, а меняется настройка редко.
# Бот и кабинет живут в одном процессе, поэтому запись из кабинета сразу
# инвалидирует кеш через set_start_video_file_id.
_cached_file_id: str | None = None
_cache_loaded = False


async def get_start_video_file_id(db: AsyncSession) -> str | None:
    """file_id видео для стартового меню либо None."""
    global _cached_file_id, _cache_loaded
    if _cache_loaded:
        return _cached_file_id

    try:
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == START_VIDEO_FILE_ID_KEY))
        setting = result.scalar_one_or_none()
        _cached_file_id = (setting.value or '').strip() or None if setting else None
        _cache_loaded = True
    except Exception as error:  # pragma: no cover - defensive
        # Меню важнее настройки: при сбое чтения отдаём None и уходим на фото/текст.
        logger.warning('Не удалось прочитать видео стартового меню', error=str(error))
        return None
    return _cached_file_id


async def set_start_video_file_id(db: AsyncSession, file_id: str | None) -> None:
    """Сохраняет (или очищает) file_id видео стартового меню."""
    global _cached_file_id, _cache_loaded

    value = (file_id or '').strip()
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == START_VIDEO_FILE_ID_KEY))
    setting = result.scalar_one_or_none()

    if setting:
        setting.value = value
    else:
        setting = SystemSetting(key=START_VIDEO_FILE_ID_KEY, value=value)
        db.add(setting)

    await db.commit()

    _cached_file_id = value or None
    _cache_loaded = True


def reset_start_video_cache() -> None:
    """Сбрасывает кеш (для тестов и ручной инвалидации)."""
    global _cached_file_id, _cache_loaded
    _cached_file_id = None
    _cache_loaded = False
