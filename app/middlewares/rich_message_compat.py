"""Makes rich messages behave like text messages for legacy handler checks.

Most handlers decide between ``edit_text`` and ``answer`` by looking at
``message.text`` / ``message.caption``. Rich messages have neither field, so the
middleware fills a readable ``.text`` before the handler runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from app.utils.rich_transport import patch_rich_message_compat


class RichMessageCompatMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        message: Message | InaccessibleMessage | None = None
        if isinstance(event, Message):
            message = event
        elif isinstance(event, CallbackQuery):
            message = event.message
        if message is not None and not isinstance(message, InaccessibleMessage):
            patch_rich_message_compat(message)
        return await handler(event, data)
