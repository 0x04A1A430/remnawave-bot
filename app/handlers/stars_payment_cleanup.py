"""Вспомогательные функции для удаления служебных сообщений Stars после оплаты."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

_stars_invoice_messages: Dict[str, Tuple[int, int]] = {}


def remember_stars_invoice_message(payload: str, chat_id: int, message_id: int) -> None:
    """Сохраняет информацию о сообщении с инвойсом Stars для последующего удаления."""

    _stars_invoice_messages[payload] = (chat_id, message_id)


def pop_stars_invoice_message(payload: str) -> Optional[Tuple[int, int]]:
    """Возвращает и удаляет сохранённое сообщение инвойса Stars, если оно есть."""

    return _stars_invoice_messages.pop(payload, None)
