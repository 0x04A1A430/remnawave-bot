from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.database.models import UserStatus
from app.services.notification_delivery_service import NotificationDeliveryService, NotificationType


def _user(**notification_settings):
    return SimpleNamespace(
        id=42,
        status=UserStatus.ACTIVE.value,
        telegram_id=4242,
        email=None,
        email_verified=False,
        notification_settings=notification_settings,
    )


async def _send(service, user, notification_type):
    return await service.send_notification(
        user=user,
        notification_type=notification_type,
        context={},
        bot=SimpleNamespace(),
        telegram_message='test',
    )


async def test_global_switch_blocks_regular_notifications(monkeypatch):
    service = NotificationDeliveryService()
    service._send_telegram_notification = AsyncMock(return_value=True)
    monkeypatch.setattr(settings, 'ENABLE_NOTIFICATIONS', False)

    result = await _send(service, _user(), NotificationType.BALANCE_TOPUP)

    assert result is False
    service._send_telegram_notification.assert_not_awaited()


@pytest.mark.parametrize(
    'notification_type',
    [
        NotificationType.EMAIL_VERIFICATION,
        NotificationType.PASSWORD_RESET,
        NotificationType.GUEST_SUBSCRIPTION_DELIVERED,
    ],
)
async def test_transactional_messages_bypass_global_switch(monkeypatch, notification_type):
    service = NotificationDeliveryService()
    service._send_telegram_notification = AsyncMock(return_value=True)
    monkeypatch.setattr(settings, 'ENABLE_NOTIFICATIONS', False)

    result = await _send(service, _user(), notification_type)

    assert result is True
    service._send_telegram_notification.assert_awaited_once()
