import os
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

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
    MiniAppPaymentStatusQuery,
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
