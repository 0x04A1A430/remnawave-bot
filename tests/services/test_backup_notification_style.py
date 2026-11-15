"""Уведомления о бекапах: premium-стиль, без legacy-символов, одна кнопка."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import admin_notification_service
from app.services.backup_service import BackupService


class _FakeAdminNotificationService:
    last: dict = {}

    def __init__(self, bot):
        self.bot = bot

    async def send_admin_notification(self, text, reply_markup=None, *, category=None):
        type(self).last = {'text': text, 'reply_markup': reply_markup, 'category': category}
        return True


@pytest.mark.asyncio
async def test_backup_notification_is_branded_and_has_one_button(monkeypatch, tmp_path):
    monkeypatch.setattr(type(settings), 'is_admin_notifications_enabled', lambda self: True)
    monkeypatch.setattr(admin_notification_service, 'AdminNotificationService', _FakeAdminNotificationService)

    service = BackupService(bot=SimpleNamespace())
    await service._send_backup_notification('success', 'Бекап готов', str(tmp_path / 'backup.tar.gz'))

    payload = _FakeAdminNotificationService.last
    text = payload['text']
    assert '<tg-emoji' in text
    assert '<blockquote>' in text
    assert '▣' not in text
    assert '≡' not in text
    assert '↑' not in text
    buttons = [button for row in payload['reply_markup'].inline_keyboard for button in row]
    assert len(buttons) == 1
    assert buttons[0].callback_data == 'backup_panel'
