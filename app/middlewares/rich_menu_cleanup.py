"""Удаляет прежнее rich-меню главного экрана, если нажатие кнопки открыло
экран отдельным сообщением, а не редактированием самого меню — чтобы чат
не засорялся копиями меню.

Логика:
- rich_menu.py запоминает message_id последнего rich-меню в чате;
- перед обработкой колбэка мидлварь сбрасывает флаг «переиспользовано»;
- если rich-флоу переиспользовал меню (edit / пересоздание) — он ставит флаг;
- после хендлера: флага нет + трекинг есть → меню удаляется.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from app.utils.rich_menu import cleanup_stale_rich_menu, reset_rich_menu_takeover


logger = structlog.get_logger(__name__)


class RichMenuCleanupMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        chat_id = getattr(event.message, 'chat', None)
        chat_id = chat_id.id if chat_id else None
        reset_rich_menu_takeover(chat_id)
        try:
            return await handler(event, data)
        finally:
            if chat_id is not None:
                bot = data.get('bot') or getattr(event, 'bot', None) or getattr(event.message, 'bot', None)
                if bot is not None:
                    await cleanup_stale_rich_menu(bot, chat_id)
