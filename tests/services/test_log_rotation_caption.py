"""Подпись к архиву логов оформлена как остальные уведомления бота."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services.log_rotation_service import LogRotationService


@pytest.mark.asyncio
async def test_log_archive_caption_uses_branded_card(monkeypatch, tmp_path) -> None:
    bot = SimpleNamespace(send_document=AsyncMock())
    service = LogRotationService(bot=bot)
    monkeypatch.setattr(type(settings), 'get_log_rotation_chat_id', lambda self: 123)
    monkeypatch.setattr(type(settings), 'get_log_rotation_topic_id', lambda self: 456)

    archive = tmp_path / 'logs_2026-11-12.tar.gz'
    archive.write_bytes(b'x' * 2048)

    await service._send_logs_to_telegram(archive, '2026-11-12')

    call = bot.send_document.await_args
    caption = call.kwargs['caption']
    assert '@x1lagent · логи' in caption
    assert '<tg-emoji' in caption
    assert '<blockquote>' in caption
    assert 'logs_2026-11-12.tar.gz' in caption
    assert call.kwargs['chat_id'] == 123
    assert call.kwargs['message_thread_id'] == 456
