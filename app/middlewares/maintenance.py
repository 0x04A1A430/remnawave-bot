import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject, User as TgUser

from app.config import settings
from app.services.maintenance_service import maintenance_service

logger = logging.getLogger(__name__)


class MaintenanceMiddleware(BaseMiddleware):
    """
    Middleware для блокировки пользователей во время техработ.
    Админы могут использовать бота в любое время.
    """
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        user: TgUser = None
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
        
        if not user or user.is_bot:
            return await handler(event, data)
        
        # Проверяем, включен ли режим техработ
        if not maintenance_service.is_maintenance_active():
            return await handler(event, data)
        
        # Админы могут работать с ботом всегда
        if settings.is_admin(user.id):
            return await handler(event, data)
        
        # Блокируем обычных пользователей
        maintenance_message = maintenance_service.get_maintenance_message()
        
        try:
            if isinstance(event, Message):
                await event.answer(maintenance_message, parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.answer(maintenance_message, show_alert=True)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения о техработах пользователю {user.id}: {e}")
        
        logger.info(f"🔧 Пользователь {user.id} заблокирован во время техработ")
        return  # Прерываем выполнение хендлера