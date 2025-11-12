import pytest
from types import SimpleNamespace
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.trial_activation_service import (
    auto_activate_trial_after_topup,
    clear_trial_activation_intent,
    get_trial_activation_intent,
    save_trial_activation_intent,
)


class MockRedis:
    def __init__(self):
        self.storage = {}

    async def setex(self, key, ttl, value):
        self.storage[key] = value
        return True

    async def get(self, key):
        return self.storage.get(key)

    async def delete(self, key):
        return 1 if self.storage.pop(key, None) is not None else 0


@pytest.mark.asyncio
async def test_trial_activation_intent_storage(monkeypatch):
    mock_redis = MockRedis()
    monkeypatch.setattr(
        "app.services.trial_activation_service.user_cart_service",  # type: ignore[attr-defined]
        SimpleNamespace(redis_client=mock_redis),
    )

    await save_trial_activation_intent(
        1,
        required_amount=1500,
        balance_amount=500,
        missing_amount=1000,
    )

    intent = await get_trial_activation_intent(1)
    assert intent is not None
    assert intent["required_amount"] == 1500
    assert intent["missing_amount"] == 1000

    await clear_trial_activation_intent(1)
    assert await get_trial_activation_intent(1) is None


@pytest.mark.asyncio
async def test_auto_activate_trial_after_topup_success(monkeypatch):
    mock_redis = MockRedis()
    monkeypatch.setattr(
        "app.services.trial_activation_service.user_cart_service",  # type: ignore[attr-defined]
        SimpleNamespace(redis_client=mock_redis),
    )

    user = SimpleNamespace(
        id=10,
        telegram_id=12345,
        language="ru",
        balance_kopeks=20000,
        subscription=None,
        has_had_paid_subscription=False,
    )
    db = AsyncMock()
    bot = AsyncMock()

    await save_trial_activation_intent(10, required_amount=1000, balance_amount=0, missing_amount=1000)

    subscription_obj = SimpleNamespace(id=55, user_id=user.id)

    monkeypatch.setattr(
        "app.services.trial_activation_service.preview_trial_activation_charge",
        lambda _user: 1000,
    )
    monkeypatch.setattr(
        "app.services.trial_activation_service.create_trial_subscription",
        AsyncMock(return_value=subscription_obj),
    )
    monkeypatch.setattr(
        "app.services.trial_activation_service.charge_trial_activation_if_required",
        AsyncMock(return_value=1000),
    )

    subscription_service_mock = SimpleNamespace(
        create_remnawave_user=AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        "app.services.trial_activation_service.SubscriptionService",
        lambda: subscription_service_mock,
    )

    admin_notification_mock = SimpleNamespace(
        send_trial_activation_notification=AsyncMock()
    )
    monkeypatch.setattr(
        "app.services.trial_activation_service.AdminNotificationService",
        lambda _bot: admin_notification_mock,
    )

    texts_stub = SimpleNamespace(
        t=lambda key, default, **kwargs: default,
    )
    monkeypatch.setattr(
        "app.services.trial_activation_service.get_texts",
        lambda _lang: texts_stub,
    )

    result = await auto_activate_trial_after_topup(db, user, bot=bot)

    assert result is True
    assert await get_trial_activation_intent(user.id) is None
    bot.send_message.assert_awaited()
    admin_notification_mock.send_trial_activation_notification.assert_awaited()
