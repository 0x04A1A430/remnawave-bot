"""Утилиты для синхронизации токена внешней админки."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.models import SystemSetting
from app.services.system_settings_service import (
    ReadOnlySettingError,
    bot_configuration_service,
)


logger = logging.getLogger(__name__)


async def ensure_external_admin_token(bot_username: Optional[str]) -> Optional[str]:
    """Генерирует и сохраняет токен внешней админки, если требуется."""

    username_raw = (bot_username or "").strip()
    if not username_raw:
        logger.warning(
            "⚠️ Не удалось обеспечить токен внешней админки: username бота отсутствует",
        )
        return None

    normalized_username = username_raw.lstrip("@").lower()
    if not normalized_username:
        logger.warning(
            "⚠️ Не удалось обеспечить токен внешней админки: username пустой после нормализации",
        )
        return None

    try:
        token = settings.build_external_admin_token(normalized_username)
    except Exception as error:  # pragma: no cover - защитный блок
        logger.error("❌ Ошибка генерации токена внешней админки: %s", error)
        return None

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SystemSetting.value).where(SystemSetting.key == "EXTERNAL_ADMIN_TOKEN")
            )
            existing = result.scalar_one_or_none()

            if existing == token:
                if settings.get_external_admin_token() != token:
                    settings.EXTERNAL_ADMIN_TOKEN = token
                return token

            try:
                await bot_configuration_service.set_value(
                    session,
                    "EXTERNAL_ADMIN_TOKEN",
                    token,
                    force=True,
                )
                await session.commit()
                logger.info(
                    "✅ Токен внешней админки синхронизирован для @%s",
                    normalized_username,
                )
            except ReadOnlySettingError:  # pragma: no cover - force=True предотвращает исключение
                await session.rollback()
                logger.warning(
                    "⚠️ Не удалось сохранить токен внешней админки из-за ограничения доступа",
                )
                return None

            return token
    except SQLAlchemyError as error:
        logger.error("❌ Ошибка сохранения токена внешней админки: %s", error)
        return None

