import os
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException, status

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

BACKUP_DIR = ROOT_DIR / 'data' / 'backups'
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault('BOT_TOKEN', 'test-token')

from app.config import settings
from app.webapi.routes import miniapp
from app.database.models import PaymentMethod
from app.webapi.schemas.miniapp import (
    MiniAppPaymentCreateRequest,
    MiniAppPaymentMethodsRequest,
    MiniAppPaymentStatusQuery,
    MiniAppSubscriptionRequest,
)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def test_compute_cryptobot_limits_scale_with_rate():
    low_rate_min, low_rate_max = miniapp._compute_cryptobot_limits(70.0)
    high_rate_min, high_rate_max = miniapp._compute_cryptobot_limits(120.0)

    assert low_rate_min == 7000
    assert low_rate_max == 7000000
    assert high_rate_min == 12000
    assert high_rate_max == 12000000
    assert high_rate_min > low_rate_min
    assert high_rate_max > low_rate_max


@pytest.mark.anyio("asyncio")
async def test_create_payment_link_pal24_uses_selected_option(monkeypatch):
    monkeypatch.setattr(settings, 'PAL24_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'PAL24_API_TOKEN', 'token', raising=False)
    monkeypatch.setattr(settings, 'PAL24_SHOP_ID', 'shop', raising=False)
    monkeypatch.setattr(settings, 'PAL24_MIN_AMOUNT_KOPEKS', 1000, raising=False)
    monkeypatch.setattr(settings, 'PAL24_MAX_AMOUNT_KOPEKS', 5000000, raising=False)

    captured_calls = []

    class DummyPaymentService:
        def __init__(self, *args, **kwargs):
            pass

        async def create_pal24_payment(self, db, **kwargs):
            captured_calls.append({'db': db, **kwargs})
            return {
                'local_payment_id': 101,
                'bill_id': 'BILL42',
                'order_id': 'ORD42',
                'payment_method': kwargs.get('payment_method'),
                'sbp_url': 'https://sbp',
                'card_url': 'https://card',
                'link_url': 'https://link',
            }

    async def fake_resolve_user(db, init_data):
        return types.SimpleNamespace(id=123, language='ru'), {}

    monkeypatch.setattr(miniapp, 'PaymentService', lambda *args, **kwargs: DummyPaymentService())
    monkeypatch.setattr(miniapp, '_resolve_user_from_init_data', fake_resolve_user)

    payload = MiniAppPaymentCreateRequest(
        initData='test',
        method='pal24',
        amountKopeks=15000,
        option='card',
    )

    response = await miniapp.create_payment_link(payload, db=types.SimpleNamespace())

    assert response.payment_url == 'https://card'
    assert response.extra['selected_option'] == 'card'
    assert response.extra['payment_method'] == 'CARD'
    assert captured_calls and captured_calls[0]['payment_method'] == 'CARD'


@pytest.mark.anyio("asyncio")
async def test_resolve_yookassa_status_includes_identifiers(monkeypatch):
    payment = types.SimpleNamespace(
        id=55,
        user_id=1,
        amount_kopeks=15000,
        currency='RUB',
        status='pending',
        is_paid=False,
        captured_at=None,
        updated_at=None,
        created_at=datetime.utcnow(),
        transaction_id=42,
        yookassa_payment_id='yk_1',
    )

    async def fake_get_by_local_id(db, local_id):
        return payment if local_id == 55 else None

    async def fake_get_by_id(db, payment_id):
        return None

    stub_module = types.SimpleNamespace(
        get_yookassa_payment_by_local_id=fake_get_by_local_id,
        get_yookassa_payment_by_id=fake_get_by_id,
    )
    monkeypatch.setitem(sys.modules, 'app.database.crud.yookassa', stub_module)

    user = types.SimpleNamespace(id=1)
    query = MiniAppPaymentStatusQuery(
        method='yookassa',
        localPaymentId=55,
        paymentId='yk_1',
        amountKopeks=15000,
        startedAt='2024-01-01T00:00:00Z',
        payload='payload123',
    )

    result = await miniapp._resolve_yookassa_payment_status(db=None, user=user, query=query)

    assert result.extra['local_payment_id'] == 55
    assert result.extra['payment_id'] == 'yk_1'
    assert result.extra['invoice_id'] == 'yk_1'
    assert result.extra['payload'] == 'payload123'
    assert result.extra['started_at'] == '2024-01-01T00:00:00Z'


@pytest.mark.anyio("asyncio")
async def test_resolve_pal24_status_includes_identifiers(monkeypatch):
    async def fake_get_pal24_payment_by_bill_id(db, bill_id):
        return None

    stub_module = types.SimpleNamespace(
        get_pal24_payment_by_bill_id=fake_get_pal24_payment_by_bill_id,
    )
    monkeypatch.setitem(sys.modules, 'app.database.crud.pal24', stub_module)

    paid_at = datetime.utcnow()

    payment = types.SimpleNamespace(
        id=321,
        user_id=1,
        amount_kopeks=25000,
        currency='RUB',
        is_paid=True,
        status='PAID',
        paid_at=paid_at,
        updated_at=paid_at,
        created_at=paid_at - timedelta(minutes=1),
        transaction_id=777,
        bill_id='BILL99',
        order_id='ORD99',
        payment_method='SBP',
    )

    class StubPal24Service:
        async def get_pal24_payment_status(self, db, local_id):
            assert local_id == 321
            return {
                'payment': payment,
                'status': 'PAID',
                'remote_status': 'PAID',
            }

    user = types.SimpleNamespace(id=1)
    query = MiniAppPaymentStatusQuery(
        method='pal24',
        localPaymentId=321,
        amountKopeks=25000,
        startedAt='2024-01-01T00:00:00Z',
        payload='pal24_payload',
    )

    result = await miniapp._resolve_pal24_payment_status(
        StubPal24Service(),
        db=None,
        user=user,
        query=query,
    )

    assert result.status == 'paid'
    assert result.extra['local_payment_id'] == 321
    assert result.extra['bill_id'] == 'BILL99'
    assert result.extra['order_id'] == 'ORD99'
    assert result.extra['payment_method'] == 'SBP'
    assert result.extra['payload'] == 'pal24_payload'
    assert result.extra['started_at'] == '2024-01-01T00:00:00Z'
    assert result.extra['remote_status'] == 'PAID'


@pytest.mark.anyio("asyncio")
async def test_get_subscription_details_returns_registration_error(monkeypatch):
    async def fake_get_user_by_telegram_id(db, telegram_id):
        return None

    monkeypatch.setattr(miniapp, 'parse_webapp_init_data', lambda payload, token: {'user': {'id': 123}})
    monkeypatch.setattr(miniapp, 'get_user_by_telegram_id', fake_get_user_by_telegram_id)
    monkeypatch.setattr(miniapp.settings, 'MINIAPP_PURCHASE_URL', 'https://example.com/buy', raising=False)
    monkeypatch.setattr(miniapp.settings, 'BOT_TOKEN', 'token', raising=False)
    monkeypatch.setattr(miniapp.settings, 'BOT_USERNAME', 'awesome_bot', raising=False)

    request = MiniAppSubscriptionRequest(initData='payload')

    with pytest.raises(HTTPException) as excinfo:
        await miniapp.get_subscription_details(request, db=types.SimpleNamespace())

    error = excinfo.value
    assert error.status_code == status.HTTP_404_NOT_FOUND
    assert isinstance(error.detail, dict)
    assert error.detail['code'] == 'user_not_registered'
    assert error.detail['title'] == 'Registration required'
    assert error.detail['purchase_url'] == 'https://example.com/buy'
    assert error.detail['bot_url'] == 'https://t.me/awesome_bot'


@pytest.mark.anyio("asyncio")
async def test_get_subscription_details_without_active_subscription(monkeypatch):
    user = types.SimpleNamespace(
        id=1,
        telegram_id=123,
        username='testuser',
        first_name='Test',
        last_name='User',
        language='ru',
        status='active',
        subscription=None,
        balance_kopeks=0,
        balance_rubles=0.0,
        balance_currency='RUB',
        promo_group=None,
        promo_offer_discount_percent=0,
        promo_offer_discount_expires_at=None,
        promo_offer_discount_source=None,
        lifetime_used_traffic_bytes=0,
    )

    class DummyResult:
        def scalars(self):
            return types.SimpleNamespace(all=lambda: [])

    class DummyDB:
        async def execute(self, query):
            return DummyResult()

    async def fake_get_user_by_telegram_id(db, telegram_id):
        return user

    async def fake_load_devices_info(_: types.SimpleNamespace):
        return 0, []

    async def fake_get_user_total_spent_kopeks(db, user_id):
        return 0

    async def fake_get_auto_assign_groups(db):
        return []

    async def fake_list_active_discount_offers_for_user(db, user_id):
        return []

    async def fake_get_latest_claimed_offer_for_user(db, user_id, source):
        return None

    async def fake_find_active_test_access_offers(db, subscription):
        return []

    async def fake_build_promo_offer_models(db, offers, contexts, user=None):
        return []

    async def fake_build_referral_info(db, user_instance):
        return None

    async def fake_get_faq_pages(db, language, include_inactive=False, fallback=True):
        return []

    async def fake_get_faq_setting(db, language, fallback=True):
        return None

    async def fake_get_public_offer(db, language):
        return None

    async def fake_get_privacy_policy(db, language):
        return None

    async def fake_get_rules_by_language(db, language):
        return None

    monkeypatch.setattr(miniapp, 'parse_webapp_init_data', lambda payload, token: {'user': {'id': 123}})
    monkeypatch.setattr(miniapp, 'get_user_by_telegram_id', fake_get_user_by_telegram_id)
    monkeypatch.setattr(miniapp, '_load_devices_info', fake_load_devices_info)
    monkeypatch.setattr(miniapp, 'get_user_total_spent_kopeks', fake_get_user_total_spent_kopeks)
    monkeypatch.setattr(miniapp, 'get_auto_assign_promo_groups', fake_get_auto_assign_groups)
    monkeypatch.setattr(miniapp, 'list_active_discount_offers_for_user', fake_list_active_discount_offers_for_user)
    monkeypatch.setattr(miniapp, 'get_latest_claimed_offer_for_user', fake_get_latest_claimed_offer_for_user)
    monkeypatch.setattr(miniapp, '_find_active_test_access_offers', fake_find_active_test_access_offers)
    monkeypatch.setattr(miniapp, '_build_promo_offer_models', fake_build_promo_offer_models)
    monkeypatch.setattr(miniapp, '_build_referral_info', fake_build_referral_info)
    monkeypatch.setattr(miniapp.FaqService, 'get_pages', fake_get_faq_pages)
    monkeypatch.setattr(miniapp.FaqService, 'get_setting', fake_get_faq_setting)
    monkeypatch.setattr(miniapp.PublicOfferService, 'get_active_offer', fake_get_public_offer)
    monkeypatch.setattr(miniapp.PrivacyPolicyService, 'get_active_policy', fake_get_privacy_policy)
    monkeypatch.setattr(miniapp, 'get_rules_by_language', fake_get_rules_by_language)
    monkeypatch.setattr(miniapp.settings, 'MINIAPP_PURCHASE_URL', 'https://example.com/buy', raising=False)
    monkeypatch.setattr(miniapp.settings, 'BOT_TOKEN', 'token', raising=False)

    request = MiniAppSubscriptionRequest(initData='payload')
    response = await miniapp.get_subscription_details(request, db=DummyDB())

    assert response.success is True
    assert response.subscription_id == 0
    assert response.subscription_type == 'none'
    assert response.subscription_purchase_url == 'https://example.com/buy'
    assert response.user.has_active_subscription is False
    assert response.user.subscription_actual_status == 'inactive'
    assert response.user.display_name == 'testuser'
    assert response.balance_rubles == 0.0
    assert response.transactions == []
    assert response.promo_offers == []
    assert response.referral is None


@pytest.mark.anyio("asyncio")
async def test_create_payment_link_stars_normalizes_amount(monkeypatch):
    monkeypatch.setattr(settings, 'TELEGRAM_STARS_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'TELEGRAM_STARS_RATE_RUB', 1000.0, raising=False)
    monkeypatch.setattr(settings, 'BOT_TOKEN', 'test-token', raising=False)

    captured = {}

    class DummyPaymentService:
        def __init__(self, bot):
            captured['bot'] = bot

        async def create_stars_invoice(
            self,
            amount_kopeks,
            description,
            payload,
            *,
            stars_amount=None,
        ):
            captured['amount_kopeks'] = amount_kopeks
            captured['description'] = description
            captured['payload'] = payload
            captured['stars_amount'] = stars_amount
            return 'https://invoice.example'

    class DummySession:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True
            captured['session_closed'] = True

    class DummyBot:
        def __init__(self, token):
            captured['bot_token'] = token
            self.session = DummySession()

    async def fake_resolve_user(db, init_data):
        return types.SimpleNamespace(id=7, language='ru'), {}

    monkeypatch.setattr(miniapp, 'PaymentService', lambda bot: DummyPaymentService(bot))
    monkeypatch.setattr(miniapp, 'Bot', DummyBot)
    monkeypatch.setattr(miniapp, '_resolve_user_from_init_data', fake_resolve_user)

    payload = MiniAppPaymentCreateRequest(
        initData='data',
        method='stars',
        amountKopeks=101000,
    )

    response = await miniapp.create_payment_link(payload, db=types.SimpleNamespace())

    assert response.payment_url == 'https://invoice.example'
    assert response.amount_kopeks == 100000
    assert response.extra['stars_amount'] == 1
    assert response.extra['requested_amount_kopeks'] == 101000
    assert captured['amount_kopeks'] == 100000
    assert captured['stars_amount'] == 1
    assert captured['bot_token'] == 'test-token'
    assert captured.get('session_closed') is True


@pytest.mark.anyio("asyncio")
async def test_get_payment_methods_exposes_stars_min_amount(monkeypatch):
    monkeypatch.setattr(settings, 'TELEGRAM_STARS_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'TELEGRAM_STARS_RATE_RUB', 999.99, raising=False)

    async def fake_resolve_user(db, init_data):
        return types.SimpleNamespace(id=1, language='ru'), {}

    monkeypatch.setattr(miniapp, '_resolve_user_from_init_data', fake_resolve_user)

    payload = MiniAppPaymentMethodsRequest(initData='abc')

    response = await miniapp.get_payment_methods(payload, db=types.SimpleNamespace())

    stars_method = next((method for method in response.methods if method.id == 'stars'), None)
    assert stars_method is not None
    assert stars_method.min_amount_kopeks == 99999
    assert stars_method.amount_step_kopeks == 99999
@pytest.mark.anyio("asyncio")
async def test_find_recent_deposit_ignores_transactions_before_attempt():
    started_at = datetime(2024, 5, 1, 12, 0, 0)

    transaction = types.SimpleNamespace(
        id=10,
        amount_kopeks=1000,
        completed_at=None,
        created_at=started_at - timedelta(minutes=1),
    )

    class DummyResult:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class DummySession:
        def __init__(self, value):
            self._value = value

        async def execute(self, query):  # noqa: ARG002
            return DummyResult(self._value)

    result = await miniapp._find_recent_deposit(
        DummySession(transaction),
        user_id=1,
        payment_method=PaymentMethod.TELEGRAM_STARS,
        amount_kopeks=1000,
        started_at=started_at,
    )

    assert result is None


@pytest.mark.anyio("asyncio")
async def test_find_recent_deposit_accepts_recent_transactions():
    started_at = datetime(2024, 5, 1, 12, 0, 0)

    transaction = types.SimpleNamespace(
        id=11,
        amount_kopeks=1000,
        completed_at=started_at + timedelta(seconds=5),
        created_at=started_at + timedelta(seconds=5),
    )

    class DummyResult:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class DummySession:
        def __init__(self, value):
            self._value = value

        async def execute(self, query):  # noqa: ARG002
            return DummyResult(self._value)

    result = await miniapp._find_recent_deposit(
        DummySession(transaction),
        user_id=1,
        payment_method=PaymentMethod.TELEGRAM_STARS,
        amount_kopeks=1000,
        started_at=started_at,
    )

    assert result is transaction
