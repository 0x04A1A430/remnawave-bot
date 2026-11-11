"""Fallback-поведение rich-транспорта на уровне сессии.

Проверяем, что при отказе сервера уходит классический метод, а при недоступном
логотипе rich-экран остаётся rich, но без картинки.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage, SendPhoto, SendRichMessage
from aiogram.types import Chat, FSInputFile, Message

from app.config import settings
from app.utils import rich_transport as rt
from app.utils.message_patch import LOGO_PATH


def _message() -> Message:
    return Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=1, type='private'))


@pytest.fixture(autouse=True)
def _enable_transport(monkeypatch):
    monkeypatch.setattr(settings, 'RICH_MESSAGES_ENABLED', True, raising=False)
    monkeypatch.setattr(rt, '_rich_unavailable', False)
    monkeypatch.setattr(rt, '_warned_unavailable', False)
    monkeypatch.setattr(rt, '_logo_unavailable', False)
    monkeypatch.setattr(rt, '_warned_logo_unavailable', False)
    monkeypatch.setattr(rt, '_resolve_logo_url', lambda: 'https://example.com/logo.png')


@pytest.mark.asyncio
async def test_unknown_rich_method_falls_back_to_classic(monkeypatch):
    calls: list[type] = []

    async def fake_request(self, bot, method, timeout=None):
        calls.append(type(method))
        if isinstance(method, SendRichMessage):
            raise TelegramBadRequest(method, 'Bad Request: unknown method')
        return _message()

    monkeypatch.setattr(AiohttpSession, 'make_request', fake_request)
    session = rt.RichAiohttpSession()
    try:
        result = await session.make_request(MagicMock(), SendMessage(chat_id=1, text='hi'), None)
    finally:
        await session.close()

    assert isinstance(result, Message)
    assert calls == [SendRichMessage, SendMessage]
    assert rt._rich_unavailable is True


@pytest.mark.asyncio
async def test_broken_logo_url_retries_rich_without_image(monkeypatch):
    monkeypatch.setattr('app.utils.message_patch._logo_send_path', LOGO_PATH)
    calls: list[str] = []

    async def fake_request(self, bot, method, timeout=None):
        if isinstance(method, SendRichMessage):
            html = method.rich_message.html or ''
            calls.append('with-image' if '<img' in html else 'text-only')
            if '<img' in html:
                raise TelegramBadRequest(method, 'Bad Request: wrong type of the web page')
            return _message()
        calls.append('classic')
        return _message()

    monkeypatch.setattr(AiohttpSession, 'make_request', fake_request)
    session = rt.RichAiohttpSession()
    try:
        result = await session.make_request(
            MagicMock(),
            SendPhoto(chat_id=1, photo=FSInputFile(LOGO_PATH), caption='Экран'),
            None,
        )
    finally:
        await session.close()

    assert isinstance(result, Message)
    assert calls == ['with-image', 'text-only']
    assert rt._logo_unavailable is True
