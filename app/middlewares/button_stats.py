"""Middleware для автоматического логирования кликов по кнопкам."""

import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from app.config import settings
from app.database.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


class ButtonStatsMiddleware(BaseMiddleware):
    """Middleware для автоматического логирования статистики кликов по кнопкам."""
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """Перехватывает CallbackQuery и логирует клики по кнопкам."""

        # Обрабатываем только CallbackQuery
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        # Пропускаем, если статистика отключена
        if not settings.MENU_LAYOUT_ENABLED:
            return await handler(event, data)

        # Логируем клик асинхронно, не блокируя обработку
        try:
            # Получаем callback_data
            callback_data = event.data
            if not callback_data:
                return await handler(event, data)

            # Получаем user_id
            user_id = event.from_user.id if event.from_user else None

            # Определяем тип кнопки по callback_data
            button_type = self._determine_button_type(callback_data)

            # Получаем текст кнопки, если возможно
            button_text = None
            if event.message and hasattr(event.message, 'reply_markup'):
                button_text = self._extract_button_text(event.message.reply_markup, callback_data)

            logger.info(f"📊 ButtonStats: клик user={user_id}, button={callback_data[:50]}, type={button_type}")

            # Логируем в фоне, не блокируя обработку
            # Используем asyncio.create_task для фоновой задачи
            import asyncio
            asyncio.create_task(
                self._log_button_click_async(
                    button_id=callback_data,
                    user_id=user_id,
                    callback_data=callback_data,
                    button_type=button_type,
                    button_text=button_text
                )
            )
        except Exception as e:
            # Не прерываем обработку при ошибке логирования
            logger.error(f"Ошибка логирования клика по кнопке: {e}", exc_info=True)
        
        # Продолжаем обработку
        return await handler(event, data)
    
    def _determine_button_type(self, callback_data: str) -> str:
        """Определяет тип кнопки по callback_data."""
        if callback_data.startswith("http://") or callback_data.startswith("https://"):
            return "url"
        elif callback_data.startswith("menu_") or callback_data.startswith("admin_"):
            return "builtin"
        else:
            return "callback"
    
    def _extract_button_text(self, reply_markup, callback_data: str) -> str:
        """Извлекает текст кнопки из клавиатуры."""
        try:
            if not reply_markup or not hasattr(reply_markup, 'inline_keyboard'):
                return None
            
            for row in reply_markup.inline_keyboard:
                for button in row:
                    if hasattr(button, 'callback_data') and button.callback_data == callback_data:
                        if hasattr(button, 'text'):
                            return button.text
        except Exception:
            pass
        return None
    
    async def _log_button_click_async(
        self,
        button_id: str,
        user_id: int = None,
        callback_data: str = None,
        button_type: str = None,
        button_text: str = None
    ):
        """Асинхронно логирует клик по кнопке."""
        try:
            async with AsyncSessionLocal() as db:
                try:
                    from app.services.menu_layout_service import MenuLayoutService

                    await MenuLayoutService.log_button_click(
                        db,
                        button_id=button_id,
                        user_id=user_id,
                        callback_data=callback_data,
                        button_type=button_type,
                        button_text=button_text
                    )
                    logger.info(f"✅ ButtonStats: записано в БД button={button_id[:30]}, user={user_id}")
                except Exception as e:
                    logger.error(f"❌ Ошибка записи клика в БД {button_id}: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"❌ Ошибка создания сессии БД для логирования клика: {e}", exc_info=True)

