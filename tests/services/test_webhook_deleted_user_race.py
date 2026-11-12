"""user.deleted webhook не должен падать, если строку уже удалили параллельно."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import remnawave_webhook_service as webhook_module
from app.services.remnawave_webhook_service import RemnaWaveWebhookService


@pytest.mark.asyncio
async def test_user_deleted_skips_when_locked_row_already_gone(monkeypatch) -> None:
    service = RemnaWaveWebhookService(bot=None)
    user = SimpleNamespace(id=1)
    monkeypatch.setattr(
        service,
        '_resolve_user_and_subscription',
        AsyncMock(return_value=(user, None)),
    )
    monkeypatch.setattr(
        webhook_module,
        'lock_user_for_update',
        AsyncMock(return_value=None),
    )
    handler = AsyncMock()

    processed = await service._process_user_event(
        AsyncMock(),
        'user.deleted',
        {'id': 1, 'telegramId': 1001},
        handler,
    )

    assert processed is False
    handler.assert_not_awaited()
