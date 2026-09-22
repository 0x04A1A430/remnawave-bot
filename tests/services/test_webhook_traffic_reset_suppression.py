"""`user.traffic_reset`: bot-initiated resets must not notify the user.

When the bot resets panel traffic itself (purchase, renewal, charge, manual
reset), the panel fires ``user.traffic_reset`` back. The initiating flow already
reported the result, so the standalone "Трафик сброшен" message is noise and
has to be suppressed. A panel-side (scheduled) reset must still notify.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.remnawave_webhook_service import RemnaWaveWebhookService


@pytest.fixture(autouse=True)
def _clear_guards() -> None:
    RemnaWaveWebhookService._intentional_traffic_resets_by_id.clear()
    RemnaWaveWebhookService._intentional_traffic_resets_by_telegram_id.clear()


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        'app.services.remnawave_webhook_service.update_subscription_usage',
        AsyncMock(),
    )
    monkeypatch.setattr(
        'app.services.remnawave_webhook_service.reactivate_subscription',
        AsyncMock(),
    )


def _service() -> RemnaWaveWebhookService:
    svc = RemnaWaveWebhookService(MagicMock())
    svc._notify_user = AsyncMock()
    svc._get_subscription_keyboard = MagicMock(return_value=None)
    return svc


def _subscription() -> MagicMock:
    subscription = MagicMock()
    subscription.id = 7
    subscription.status = 'ACTIVE'
    return subscription


async def test_bot_initiated_reset_is_silent() -> None:
    RemnaWaveWebhookService.mark_intentional_traffic_reset(panel_user_ids=[42], telegram_id=999)
    svc = _service()

    await svc._handle_user_traffic_reset(None, MagicMock(id=1), _subscription(), {'id': 42, 'telegramId': 999})

    svc._notify_user.assert_not_awaited()


async def test_scheduled_reset_still_notifies() -> None:
    svc = _service()

    await svc._handle_user_traffic_reset(None, MagicMock(id=1), _subscription(), {'id': 42, 'telegramId': 999})

    svc._notify_user.assert_awaited_once()
    assert svc._notify_user.await_args.args[1] == 'WEBHOOK_SUB_TRAFFIC_RESET'


async def test_marker_is_consumed_only_once() -> None:
    RemnaWaveWebhookService.mark_intentional_traffic_reset(panel_user_ids=[42])
    svc = _service()

    await svc._handle_user_traffic_reset(None, MagicMock(id=1), _subscription(), {'id': 42})
    await svc._handle_user_traffic_reset(None, MagicMock(id=1), _subscription(), {'id': 42})

    svc._notify_user.assert_awaited_once()


async def test_telegram_id_marker_matches_nested_user() -> None:
    RemnaWaveWebhookService.mark_intentional_traffic_reset(telegram_id=999)
    svc = _service()

    await svc._handle_user_traffic_reset(None, MagicMock(id=1), _subscription(), {'user': {'telegramId': 999}})

    svc._notify_user.assert_not_awaited()
