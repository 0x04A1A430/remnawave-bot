"""Вспомогательные функции для очистки служебных сообщений Telegram Stars."""

from __future__ import annotations

import asyncio
from typing import Iterable, Set

__all__ = [
    "register_stars_payment_messages",
    "consume_stars_payment_messages",
]


_messages_lock = asyncio.Lock()
_messages_to_cleanup: dict[int, set[int]] = {}


async def register_stars_payment_messages(user_id: int, message_ids: Iterable[int]) -> None:
    """Сохраняет идентификаторы сообщений для последующего удаления.

    Args:
        user_id: Telegram ID пользователя.
        message_ids: Сообщения, которые нужно удалить после успешной оплаты.
    """

    async with _messages_lock:
        existing = _messages_to_cleanup.setdefault(user_id, set())
        existing.update(message_ids)


async def consume_stars_payment_messages(user_id: int) -> Set[int]:
    """Возвращает и удаляет накопленные сообщения для пользователя."""

    async with _messages_lock:
        return _messages_to_cleanup.pop(user_id, set())
