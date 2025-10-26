import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.config import settings
from app.database.models import User
from app.services.subscription_auto_purchase_service import auto_purchase_saved_cart_after_topup
from app.services.subscription_purchase_service import (
    PurchaseDevicesConfig,
    PurchaseOptionsContext,
    PurchasePeriodConfig,
    PurchasePricingResult,
    PurchaseSelection,
    PurchaseServersConfig,
    PurchaseTrafficConfig,
)
from sqlalchemy.ext.asyncio import AsyncSession


class DummyTexts:
    def t(self, key: str, default: str):
        return default

    def format_price(self, value: int) -> str:
        return f"{value / 100:.0f} ₽"


@pytest.mark.asyncio
async def test_auto_purchase_saved_cart_after_topup_success(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PURCHASE_AFTER_TOPUP_ENABLED", True)

    user = MagicMock(spec=User)
    user.id = 42
    user.telegram_id = 4242
    user.balance_kopeks = 200_000
    user.language = "ru"
    user.subscription = None

    cart_data = {
        "period_days": 30,
        "countries": ["ru"],
        "traffic_gb": 0,
        "devices": 1,
    }

    traffic_config = PurchaseTrafficConfig(
        selectable=False,
        mode="fixed",
        options=[],
        default_value=0,
        current_value=0,
    )
    servers_config = PurchaseServersConfig(
        options=[],
        min_selectable=0,
        max_selectable=0,
        default_selection=["ru"],
    )
    devices_config = PurchaseDevicesConfig(
        minimum=1,
        maximum=5,
        default=1,
        current=1,
        price_per_device=0,
        discounted_price_per_device=0,
        price_label="0 ₽",
    )

    period_config = PurchasePeriodConfig(
        id="days:30",
        days=30,
        months=1,
        label="30 дней",
        base_price=100_000,
        base_price_label="1000 ₽",
        base_price_original=100_000,
        base_price_original_label=None,
        discount_percent=0,
        per_month_price=100_000,
        per_month_price_label="1000 ₽",
        traffic=traffic_config,
        servers=servers_config,
        devices=devices_config,
    )

    context = PurchaseOptionsContext(
        user=user,
        subscription=None,
        currency="RUB",
        balance_kopeks=user.balance_kopeks,
        periods=[period_config],
        default_period=period_config,
        period_map={"days:30": period_config},
        server_uuid_to_id={"ru": 1},
        payload={},
    )

    base_pricing = PurchasePricingResult(
        selection=PurchaseSelection(
            period=period_config,
            traffic_value=0,
            servers=["ru"],
            devices=1,
        ),
        server_ids=[1],
        server_prices_for_period=[100_000],
        base_original_total=100_000,
        discounted_total=100_000,
        promo_discount_value=0,
        promo_discount_percent=0,
        final_total=100_000,
        months=1,
        details={"servers_individual_prices": [100_000]},
    )

    class DummyMiniAppService:
        async def build_options(self, db, user):
            return context

        async def calculate_pricing(self, db, ctx, selection):
            return PurchasePricingResult(
                selection=selection,
                server_ids=base_pricing.server_ids,
                server_prices_for_period=base_pricing.server_prices_for_period,
                base_original_total=base_pricing.base_original_total,
                discounted_total=base_pricing.discounted_total,
                promo_discount_value=base_pricing.promo_discount_value,
                promo_discount_percent=base_pricing.promo_discount_percent,
                final_total=base_pricing.final_total,
                months=base_pricing.months,
                details=base_pricing.details,
            )

        async def submit_purchase(self, db, prepared_context, pricing):
            return {
                "subscription": MagicMock(),
                "transaction": MagicMock(),
                "was_trial_conversion": False,
                "message": "🎉 Subscription purchased",
            }

    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.MiniAppSubscriptionPurchaseService",
        lambda: DummyMiniAppService(),
    )
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.user_cart_service.get_user_cart",
        AsyncMock(return_value=cart_data),
    )
    delete_cart_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.user_cart_service.delete_user_cart",
        delete_cart_mock,
    )
    clear_draft_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.clear_subscription_checkout_draft",
        clear_draft_mock,
    )
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.get_texts",
        lambda lang: DummyTexts(),
    )
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.format_period_description",
        lambda days, lang: f"{days} дней",
    )

    admin_service_mock = MagicMock()
    admin_service_mock.send_subscription_purchase_notification = AsyncMock()
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.AdminNotificationService",
        lambda bot: admin_service_mock,
    )

    bot = AsyncMock()
    db_session = AsyncMock(spec=AsyncSession)

    result = await auto_purchase_saved_cart_after_topup(db_session, user, bot=bot)

    assert result is True
    delete_cart_mock.assert_awaited_once_with(user.id)
    clear_draft_mock.assert_awaited_once_with(user.id)
    bot.send_message.assert_awaited()
    admin_service_mock.send_subscription_purchase_notification.assert_awaited()


@pytest.mark.asyncio
async def test_auto_purchase_saved_cart_after_topup_extension(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PURCHASE_AFTER_TOPUP_ENABLED", True)

    subscription = MagicMock()
    subscription.id = 99
    subscription.is_trial = False
    subscription.status = "active"
    subscription.end_date = datetime.utcnow()
    subscription.device_limit = 1
    subscription.traffic_limit_gb = 100
    subscription.connected_squads = ["squad-a"]

    user = MagicMock(spec=User)
    user.id = 7
    user.telegram_id = 7007
    user.balance_kopeks = 200_000
    user.language = "ru"
    user.subscription = subscription

    cart_data = {
        "cart_mode": "extend",
        "subscription_id": subscription.id,
        "period_days": 30,
        "total_price": 31_000,
        "description": "Продление подписки на 30 дней",
        "device_limit": 2,
        "traffic_limit_gb": 500,
        "squad_uuid": "squad-b",
        "consume_promo_offer": True,
    }

    subtract_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.subtract_user_balance",
        subtract_mock,
    )

    async def extend_stub(db, current_subscription, days):
        current_subscription.end_date = current_subscription.end_date + timedelta(days=days)
        return current_subscription

    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.extend_subscription",
        extend_stub,
    )

    create_transaction_mock = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.create_transaction",
        create_transaction_mock,
    )

    service_mock = MagicMock()
    service_mock.update_remnawave_user = AsyncMock()
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.SubscriptionService",
        lambda: service_mock,
    )

    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.user_cart_service.get_user_cart",
        AsyncMock(return_value=cart_data),
    )
    delete_cart_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.user_cart_service.delete_user_cart",
        delete_cart_mock,
    )
    clear_draft_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.clear_subscription_checkout_draft",
        clear_draft_mock,
    )

    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.get_texts",
        lambda lang: DummyTexts(),
    )
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.format_period_description",
        lambda days, lang: f"{days} дней",
    )

    admin_service_mock = MagicMock()
    admin_service_mock.send_subscription_extension_notification = AsyncMock()
    monkeypatch.setattr(
        "app.services.subscription_auto_purchase_service.AdminNotificationService",
        lambda bot: admin_service_mock,
    )

    bot = AsyncMock()
    db_session = AsyncMock(spec=AsyncSession)

    result = await auto_purchase_saved_cart_after_topup(db_session, user, bot=bot)

    assert result is True
    subtract_mock.assert_awaited_once_with(
        db_session,
        user,
        cart_data["total_price"],
        cart_data["description"],
        consume_promo_offer=True,
    )
    assert subscription.device_limit == 2
    assert subscription.traffic_limit_gb == 500
    assert "squad-b" in subscription.connected_squads
    delete_cart_mock.assert_awaited_once_with(user.id)
    clear_draft_mock.assert_awaited_once_with(user.id)
    admin_service_mock.send_subscription_extension_notification.assert_awaited()
    bot.send_message.assert_awaited()
    service_mock.update_remnawave_user.assert_awaited()
    create_transaction_mock.assert_awaited()
