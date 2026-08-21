"""Unit and contract tests for cabinet gift routes and branding feature toggle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.cabinet.routes import branding as branding_routes, gift as gift_routes
from app.cabinet.routes.branding import GiftEnabledUpdate
from app.cabinet.schemas.gift import GiftPurchaseRequest
from app.database.models import (
    DiscountOffer,
    GuestPurchase,
    GuestPurchaseStatus,
    PaymentMethod,
    PaymentMethodConfig,
    PromoGroup,
    PromoOfferLog,
    Subscription,
    SystemSetting,
    Tariff,
    Transaction,
    TransactionType,
    User,
    UserPromoGroup,
    Webhook,
    tariff_promo_groups,
)
from app.services.gift_purchase_service import GIFT_ENABLED_KEY, is_gift_enabled
from tests.fixtures.sqlite_memory import memory_session


_TABLES = [
    SystemSetting.__table__,
    Tariff.__table__,
    PromoGroup.__table__,
    tariff_promo_groups,
    UserPromoGroup.__table__,
    Subscription.__table__,
    User.__table__,
    GuestPurchase.__table__,
    Transaction.__table__,
    DiscountOffer.__table__,
    PromoOfferLog.__table__,
    PaymentMethodConfig.__table__,
    Webhook.__table__,
]


@pytest.fixture(autouse=True)
def bypass_rate_limit(monkeypatch):
    """Disable rate limiting for route tests."""
    from app.utils.cache import RateLimitCache

    monkeypatch.setattr(RateLimitCache, 'is_rate_limited', AsyncMock(return_value=False))


# ── Step 1: Feature Switch and Branding Routes ─────────────────────────────


@pytest.mark.asyncio
async def test_branding_gift_enabled_routes(monkeypatch):
    """get_gift_enabled and update_gift_enabled in branding read/write the shared setting."""
    async with memory_session(monkeypatch, _TABLES) as db:
        # Default: absent setting -> disabled
        res1 = await branding_routes.get_gift_enabled(db=db)
        assert res1.enabled is False
        assert await is_gift_enabled(db) is False

        # Admin enables gift feature
        admin = User(id=1, telegram_id=12345, username='admin')
        res2 = await branding_routes.update_gift_enabled(
            payload=GiftEnabledUpdate(enabled=True),
            admin=admin,
            db=db,
        )
        assert res2.enabled is True
        assert await is_gift_enabled(db) is True

        # Public get returns enabled
        res3 = await branding_routes.get_gift_enabled(db=db)
        assert res3.enabled is True

        # Admin disables gift feature
        res4 = await branding_routes.update_gift_enabled(
            payload=GiftEnabledUpdate(enabled=False),
            admin=admin,
            db=db,
        )
        assert res4.enabled is False
        assert await is_gift_enabled(db) is False


# ── Step 2: /gift/config Route Tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_gift_config_when_disabled(monkeypatch):
    """When gift feature is disabled, /gift/config returns is_enabled=False and user balance."""
    async with memory_session(monkeypatch, _TABLES) as db:
        user = User(id=10, balance_kopeks=50000, username='buyer')
        db.add(user)
        await db.commit()

        config = await gift_routes.get_gift_config(user=user, db=db)
        assert config.is_enabled is False
        assert config.balance_kopeks == 50000
        assert config.tariffs == []


@pytest.mark.asyncio
async def test_gift_config_filters_tariffs_and_orders(monkeypatch):
    """Only active tariffs with show_in_gift=True are returned, ordered by display_order then id."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=50000, username='buyer')
        db.add(user)

        # 1. Eligible, display_order 2
        t1 = Tariff(
            id=1,
            name='Tariff Beta',
            is_active=True,
            show_in_gift=True,
            display_order=2,
            period_prices={'30': 30000},
            device_limit=2,
            traffic_limit_gb=50,
        )
        # 2. Eligible, display_order 1 (should appear first)
        t2 = Tariff(
            id=2,
            name='Tariff Alpha',
            is_active=True,
            show_in_gift=True,
            display_order=1,
            period_prices={'30': 20000, '90': 50000},
            device_limit=1,
            traffic_limit_gb=20,
        )
        # 3. Inactive (should be excluded)
        t3 = Tariff(
            id=3,
            name='Tariff Inactive',
            is_active=False,
            show_in_gift=True,
            display_order=0,
            period_prices={'30': 10000},
        )
        # 4. show_in_gift=False (should be excluded)
        t4 = Tariff(
            id=4,
            name='Tariff No Gift',
            is_active=True,
            show_in_gift=False,
            display_order=0,
            period_prices={'30': 10000},
        )
        db.add_all([t1, t2, t3, t4])
        await db.commit()

        config = await gift_routes.get_gift_config(user=user, db=db)
        assert config.is_enabled is True
        assert [t.id for t in config.tariffs] == [2, 1]
        assert config.tariffs[0].name == 'Tariff Alpha'
        assert len(config.tariffs[0].periods) == 2
        assert config.tariffs[0].periods[0].days == 30
        assert config.tariffs[0].periods[0].price_kopeks == 20000
        assert config.tariffs[0].periods[1].days == 90
        assert config.tariffs[0].periods[1].price_kopeks == 50000


@pytest.mark.asyncio
async def test_gift_config_personalized_quote_fields(monkeypatch):
    """Personalized discounts (promo group & active promo offer) populate quote fields."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(
            id=10,
            balance_kopeks=50000,
            username='buyer',
            promo_offer_discount_percent=20,
        )
        db.add(user)

        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=True,
            show_in_gift=True,
            display_order=1,
            period_prices={'30': 10000},
            device_limit=1,
            traffic_limit_gb=30,
        )
        db.add(tariff)
        await db.commit()

        config = await gift_routes.get_gift_config(user=user, db=db)
        assert config.is_enabled is True
        assert config.active_discount_percent == 20
        period = config.tariffs[0].periods[0]
        assert period.days == 30
        assert period.price_kopeks == 8000
        assert period.original_price_kopeks == 10000
        assert period.discount_percent == 20


# ── Step 3: /gift/purchase Balance Mode Tests ──────────────────────────────


@pytest.mark.asyncio
async def test_purchase_gift_balance_success(monkeypatch):
    """Balance checkout creates a paid GuestPurchase with cabinet idempotency and debits balance."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=50000, username='buyer', email='buyer@example.com')
        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=True,
            show_in_gift=True,
            period_prices={'30': 30000},
            device_limit=1,
        )
        db.add_all([user, tariff])
        await db.commit()

        req = GiftPurchaseRequest(
            tariff_id=1,
            period_days=30,
            payment_mode='balance',
            gift_message='Enjoy your subscription!',
        )
        response = await gift_routes.create_gift_purchase(body=req, user=user, db=db)

        assert response.status == 'ok'
        assert len(response.purchase_token) == 12

        # Check DB state
        res = await db.execute(select(GuestPurchase).where(GuestPurchase.buyer_user_id == 10))
        purchase = res.scalars().first()
        assert purchase is not None
        assert purchase.status == GuestPurchaseStatus.PAID.value
        assert purchase.amount_kopeks == 30000
        assert purchase.is_gift is True
        assert purchase.source == 'cabinet'
        assert purchase.idempotency_key is not None
        assert purchase.idempotency_key.startswith('cab_')
        assert purchase.gift_message == 'Enjoy your subscription!'
        assert purchase.gift_recipient_type is None
        assert purchase.gift_recipient_value is None

        # Check user balance
        await db.refresh(user)
        assert user.balance_kopeks == 20000

        # Check transaction
        tx_res = await db.execute(select(Transaction).where(Transaction.user_id == 10))
        tx = tx_res.scalars().first()
        assert tx is not None
        assert tx.type == TransactionType.GIFT_PAYMENT.value
        assert tx.payment_method == PaymentMethod.BALANCE.value
        assert abs(tx.amount_kopeks) == 30000


@pytest.mark.asyncio
async def test_purchase_gift_balance_directed_and_notification(monkeypatch):
    """Directed gift persists recipient details and invokes claim notification."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=50000, username='buyer', email='buyer@example.com')
        tariff = Tariff(
            id=1,
            name='Pro',
            is_active=True,
            show_in_gift=True,
            period_prices={'30': 30000},
        )
        db.add_all([user, tariff])
        await db.commit()

        notify_mock = AsyncMock()
        monkeypatch.setattr('app.cabinet.routes.gift.notify_gift_claim_available', notify_mock)

        req = GiftPurchaseRequest(
            tariff_id=1,
            period_days=30,
            recipient_type='email',
            recipient_value='friend@example.com',
            gift_message='Happy Birthday!',
            payment_mode='balance',
        )
        response = await gift_routes.create_gift_purchase(body=req, user=user, db=db)
        assert response.status == 'ok'

        res = await db.execute(select(GuestPurchase).where(GuestPurchase.buyer_user_id == 10))
        purchase = res.scalars().first()
        assert purchase is not None
        assert purchase.gift_recipient_type == 'email'
        assert purchase.gift_recipient_value == 'friend@example.com'
        assert purchase.gift_message == 'Happy Birthday!'

        notify_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_purchase_gift_balance_insufficient_balance(monkeypatch):
    """When balance is insufficient, raises 400 Insufficient balance."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=5000, username='buyer')
        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=True,
            show_in_gift=True,
            period_prices={'30': 30000},
        )
        db.add_all([user, tariff])
        await db.commit()

        req = GiftPurchaseRequest(
            tariff_id=1,
            period_days=30,
            payment_mode='balance',
        )
        with pytest.raises(HTTPException) as exc_info:
            await gift_routes.create_gift_purchase(body=req, user=user, db=db)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == 'Insufficient balance'

        # User balance unchanged
        await db.refresh(user)
        assert user.balance_kopeks == 5000


@pytest.mark.asyncio
async def test_purchase_gift_restricted_user(monkeypatch):
    """Restricted buyer receives 403 Forbidden."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=50000, username='buyer', restriction_subscription=True)
        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=True,
            show_in_gift=True,
            period_prices={'30': 30000},
        )
        db.add_all([user, tariff])
        await db.commit()

        req = GiftPurchaseRequest(
            tariff_id=1,
            period_days=30,
            payment_mode='balance',
        )
        with pytest.raises(HTTPException) as exc_info:
            await gift_routes.create_gift_purchase(body=req, user=user, db=db)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == 'Purchases are restricted for this account'


@pytest.mark.asyncio
async def test_purchase_gift_disabled_feature(monkeypatch):
    """When gift feature is disabled, purchase raises 400 Gift feature is not enabled."""
    async with memory_session(monkeypatch, _TABLES) as db:
        user = User(id=10, balance_kopeks=50000, username='buyer')
        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=True,
            show_in_gift=True,
            period_prices={'30': 30000},
        )
        db.add_all([user, tariff])
        await db.commit()

        req = GiftPurchaseRequest(
            tariff_id=1,
            period_days=30,
            payment_mode='balance',
        )
        with pytest.raises(HTTPException) as exc_info:
            await gift_routes.create_gift_purchase(body=req, user=user, db=db)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == 'Gift feature is not enabled'


@pytest.mark.asyncio
async def test_purchase_gift_tariff_not_found_or_inactive(monkeypatch):
    """Inactive or non-gift tariffs raise 404 Tariff not found or inactive."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=50000, username='buyer')
        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=False,
            show_in_gift=True,
            period_prices={'30': 30000},
        )
        db.add_all([user, tariff])
        await db.commit()

        # Inactive tariff
        req = GiftPurchaseRequest(tariff_id=1, period_days=30, payment_mode='balance')
        with pytest.raises(HTTPException) as exc_info:
            await gift_routes.create_gift_purchase(body=req, user=user, db=db)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == 'Tariff not found or inactive'

        # Non-existent tariff
        req_missing = GiftPurchaseRequest(tariff_id=999, period_days=30, payment_mode='balance')
        with pytest.raises(HTTPException) as exc_info2:
            await gift_routes.create_gift_purchase(body=req_missing, user=user, db=db)
        assert exc_info2.value.status_code == 404
        assert exc_info2.value.detail == 'Tariff not found or inactive'


@pytest.mark.asyncio
async def test_purchase_gift_invalid_period(monkeypatch):
    """Requesting unconfigured period raises 400 Price is not configured for this period."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=50000, username='buyer')
        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=True,
            show_in_gift=True,
            period_prices={'30': 30000},
        )
        db.add_all([user, tariff])
        await db.commit()

        req = GiftPurchaseRequest(tariff_id=1, period_days=90, payment_mode='balance')
        with pytest.raises(HTTPException) as exc_info:
            await gift_routes.create_gift_purchase(body=req, user=user, db=db)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == 'Price is not configured for this period'


@pytest.mark.asyncio
async def test_purchase_gift_self_gift_prevention(monkeypatch):
    """Self-gifting by username or email raises 400 Cannot gift to yourself."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=50000, username='myname', email='me@example.com')
        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=True,
            show_in_gift=True,
            period_prices={'30': 30000},
        )
        db.add_all([user, tariff])
        await db.commit()

        # Self-gift via telegram
        req_tg = GiftPurchaseRequest(
            tariff_id=1,
            period_days=30,
            recipient_type='telegram',
            recipient_value='@MYNAME',
            payment_mode='balance',
        )
        with pytest.raises(HTTPException) as exc_tg:
            await gift_routes.create_gift_purchase(body=req_tg, user=user, db=db)
        assert exc_tg.value.status_code == 400
        assert exc_tg.value.detail == 'Cannot gift to yourself'

        # Self-gift via email
        req_em = GiftPurchaseRequest(
            tariff_id=1,
            period_days=30,
            recipient_type='email',
            recipient_value='ME@EXAMPLE.COM',
            payment_mode='balance',
        )
        with pytest.raises(HTTPException) as exc_em:
            await gift_routes.create_gift_purchase(body=req_em, user=user, db=db)
        assert exc_em.value.status_code == 400
        assert exc_em.value.detail == 'Cannot gift to yourself'


# ── Step 4: /gift/purchase Gateway Mode Tests ──────────────────────────────


@pytest.mark.asyncio
async def test_purchase_gift_gateway_success(monkeypatch):
    """Gateway mode creates a payment via PaymentService and does not debit user balance."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=50000, username='buyer', email='buyer@example.com')
        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=True,
            show_in_gift=True,
            period_prices={'30': 30000},
        )
        db.add_all([user, tariff])
        await db.commit()

        fake_payment_service = MagicMock()
        fake_payment_service.create_guest_payment = AsyncMock(
            return_value={'payment_url': 'https://pay.provider.example/checkout/123'}
        )
        monkeypatch.setattr('app.services.payment_service.PaymentService', lambda **kw: fake_payment_service)

        req = GiftPurchaseRequest(
            tariff_id=1,
            period_days=30,
            payment_mode='gateway',
            payment_method='yookassa',
        )
        response = await gift_routes.create_gift_purchase(body=req, user=user, db=db)

        assert response.status == 'created'
        assert response.payment_url == 'https://pay.provider.example/checkout/123'
        assert len(response.purchase_token) == 12

        # Verify GuestPurchase in DB
        res = await db.execute(select(GuestPurchase).where(GuestPurchase.buyer_user_id == 10))
        purchase = res.scalars().first()
        assert purchase is not None
        assert purchase.payment_method == 'yookassa'
        assert purchase.status == GuestPurchaseStatus.PENDING.value
        assert purchase.amount_kopeks == 30000

        # Balance was NOT debited
        await db.refresh(user)
        assert user.balance_kopeks == 50000


@pytest.mark.asyncio
async def test_purchase_gift_gateway_provider_error(monkeypatch):
    """When payment provider returns None, raises 502 Bad Gateway."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=50000, username='buyer')
        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=True,
            show_in_gift=True,
            period_prices={'30': 30000},
        )
        db.add_all([user, tariff])
        await db.commit()

        fake_svc = MagicMock()
        fake_svc.create_guest_payment = AsyncMock(return_value=None)
        monkeypatch.setattr('app.services.payment_service.PaymentService', lambda **kw: fake_svc)

        req = GiftPurchaseRequest(
            tariff_id=1,
            period_days=30,
            payment_mode='gateway',
            payment_method='yookassa',
        )
        with pytest.raises(HTTPException) as exc:
            await gift_routes.create_gift_purchase(body=req, user=user, db=db)
        assert exc.value.status_code == 502
        assert exc.value.detail == 'Payment provider is unavailable, please try again later'


@pytest.mark.asyncio
async def test_purchase_gift_gateway_invalid_response(monkeypatch):
    """When payment provider returns no payment_url, raises 502 Bad Gateway."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=50000, username='buyer')
        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=True,
            show_in_gift=True,
            period_prices={'30': 30000},
        )
        db.add_all([user, tariff])
        await db.commit()

        fake_svc = MagicMock()
        fake_svc.create_guest_payment = AsyncMock(return_value={})
        monkeypatch.setattr('app.services.payment_service.PaymentService', lambda **kw: fake_svc)

        req = GiftPurchaseRequest(
            tariff_id=1,
            period_days=30,
            payment_mode='gateway',
            payment_method='yookassa',
        )
        with pytest.raises(HTTPException) as exc:
            await gift_routes.create_gift_purchase(body=req, user=user, db=db)
        assert exc.value.status_code == 502
        assert exc.value.detail == 'Payment provider returned an invalid response'


@pytest.mark.asyncio
async def test_purchase_gift_telegram_unresolvable_warning(monkeypatch):
    """When a recipient telegram username is not in DB and unresolvable via Bot API, warning is returned."""
    async with memory_session(monkeypatch, _TABLES) as db:
        db.add(SystemSetting(key=GIFT_ENABLED_KEY, value='true'))
        user = User(id=10, balance_kopeks=50000, username='buyer')
        tariff = Tariff(
            id=1,
            name='Standard',
            is_active=True,
            show_in_gift=True,
            period_prices={'30': 30000},
        )
        db.add_all([user, tariff])
        await db.commit()

        req = GiftPurchaseRequest(
            tariff_id=1,
            period_days=30,
            recipient_type='telegram',
            recipient_value='@unknown_recipient_user',
            payment_mode='balance',
        )
        response = await gift_routes.create_gift_purchase(body=req, user=user, db=db)
        assert response.status == 'ok'
        assert response.warning == 'telegram_unresolvable'

