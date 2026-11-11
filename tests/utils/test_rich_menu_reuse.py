"""Rich-меню не должно удаляться, когда хендлер правит его напрямую.

Регрессия: «Моя подписка» (и десятки других экранов) вызывают
``callback.message.edit_text`` вместо ``edit_or_answer_photo``. Rich-меню при
этом переиспользуется на месте, но RichMenuCleanupMiddleware считал его
«открытым заново» и удалял — чат оставался пустым.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, RichBlockParagraph, RichMessage, RichTextBold

from app.utils import message_patch, rich_menu


def _rich_message(chat_id: int, message_id: int) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type='private'),
        rich_message=RichMessage(blocks=[RichBlockParagraph(text=RichTextBold(text='Меню'))]),
    )


@pytest.mark.asyncio
async def test_direct_edit_marks_tracked_rich_menu_as_reused(monkeypatch):
    chat_id, message_id = 42, 700
    rich_menu._tracked_rich_menus[chat_id] = message_id
    rich_menu._taken_over_chats.discard(chat_id)
    message = _rich_message(chat_id, message_id)

    async def fake_text_edit(self, text, **kwargs):
        return None

    monkeypatch.setattr(message_patch, '_text_edit', fake_text_edit)

    await message_patch._edit_with_photo(message, 'Новый экран')

    assert chat_id in rich_menu._taken_over_chats
    rich_menu._tracked_rich_menus.pop(chat_id, None)
    rich_menu._taken_over_chats.discard(chat_id)


@pytest.mark.asyncio
async def test_cleanup_keeps_reused_menu_and_deletes_abandoned_one():
    bot = AsyncMock()

    # Переиспользовано — удалять нельзя.
    rich_menu._tracked_rich_menus[1] = 10
    rich_menu._taken_over_chats.add(1)
    await rich_menu.cleanup_stale_rich_menu(bot, 1)
    bot.delete_message.assert_not_awaited()

    # Открыт новый экран — старое меню удаляется.
    rich_menu._tracked_rich_menus[2] = 20
    rich_menu._taken_over_chats.discard(2)
    await rich_menu.cleanup_stale_rich_menu(bot, 2)
    bot.delete_message.assert_awaited_once_with(chat_id=2, message_id=20)
