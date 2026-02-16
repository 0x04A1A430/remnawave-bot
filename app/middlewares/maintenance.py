from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User as TgUser

from app.config import settings
from app.services.maintenance_service import maintenance_service


logger = structlog.get_logger(__name__)


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: TgUser = None
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user

        if not user or user.is_bot:
            return await handler(event, data)

        if not maintenance_service.is_maintenance_active():
            return await handler(event, data)

        if settings.is_admin(user.id):
            return await handler(event, data)

        maintenance_message = maintenance_service.get_maintenance_message()

        try:
            if isinstance(event, Message):
                await event.answer(maintenance_message, parse_mode='HTML')
            elif isinstance(event, CallbackQuery):
                await event.answer(maintenance_message, show_alert=True)
        except Exception as e:
            logger.error('Ошибка отправки сообщения о техработах пользователю', user_id=user.id, error=e)

        logger.info('🔧 Пользователь заблокирован во время техработ', user_id=user.id)
        return None
