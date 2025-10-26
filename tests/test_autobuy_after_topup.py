import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.handlers.subscription.purchase import (  # noqa: E402
    attempt_auto_purchase_after_topup,
    PurchaseExecutionResult,
)
from app.services.payment.common import PaymentCommonMixin  # noqa: E402
from app.config import settings  # noqa: E402


@pytest.mark.asyncio
async def test_attempt_auto_purchase_after_topup_disabled(monkeypatch):
    monkeypatch.setattr(settings, "AUTOBUY_AFTER_TOPUP_ENABLED", False)

    with patch("app.handlers.subscription.purchase.user_cart_service") as cart_service:
        cart_service.get_user_cart = AsyncMock()

        result = await attempt_auto_purchase_after_topup(
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
        )

        assert result is False
        cart_service.get_user_cart.assert_not_awaited()


@pytest.mark.asyncio
async def test_attempt_auto_purchase_after_topup_success(monkeypatch):
    monkeypatch.setattr(settings, "AUTOBUY_AFTER_TOPUP_ENABLED", True)

    user = AsyncMock()
    user.language = "ru"
    user.telegram_id = 123
    user.id = 123
    user.balance_kopeks = 20000

    prepared_data = {
        "total_price": 10000,
        "period_days": 30,
        "months_in_period": 1,
        "final_traffic_gb": 0,
        "server_prices_for_period": [],
    }

    bot = AsyncMock()

    with patch("app.handlers.subscription.purchase.user_cart_service") as cart_service, \
            patch("app.handlers.subscription.purchase._prepare_subscription_summary") as prepare_summary, \
            patch("app.handlers.subscription.purchase._complete_purchase_workflow") as complete_purchase, \
            patch("app.handlers.subscription.purchase.clear_subscription_checkout_draft") as clear_draft:

        cart_service.get_user_cart = AsyncMock(return_value={"period_days": 30, "total_price": 10000})
        prepare_summary.return_value = (None, prepared_data)
        complete_purchase.return_value = PurchaseExecutionResult(
            success=True,
            message="done",
            keyboard=None,
            purchase_completed=True,
        )

        result = await attempt_auto_purchase_after_topup(
            AsyncMock(),
            user,
            bot,
        )

        assert result is True
        bot.send_message.assert_awaited()
        clear_draft.assert_awaited_with(user.id)


class _DummyPayment(PaymentCommonMixin):
    def __init__(self):
        self.bot = AsyncMock()

    async def build_topup_success_keyboard(self, user):
        return AsyncMock()


@pytest.mark.asyncio
async def test_send_payment_notification_triggers_autobuy(monkeypatch):
    monkeypatch.setattr(settings, "AUTOBUY_AFTER_TOPUP_ENABLED", True)

    dummy = _DummyPayment()

    user = AsyncMock()
    user.telegram_id = 42

    with patch("app.handlers.subscription.purchase.attempt_auto_purchase_after_topup", new=AsyncMock(return_value=True)) as autopurchase:

        await dummy._send_payment_success_notification(
            telegram_id=user.telegram_id,
            amount_kopeks=1000,
            user=user,
            db=AsyncMock(),
            payment_method_title="Test",
        )

        autopurchase.assert_awaited()
