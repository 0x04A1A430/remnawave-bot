"""Тесты подписочных методов PlategaService (SBP recurring, задача 4)."""

import pytest

from app.config import settings
from app.services.platega_service import PlategaService


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides) -> None:
    values = {
        'PLATEGA_ENABLED': True,
        'PLATEGA_MERCHANT_ID': 'm',
        'PLATEGA_SECRET': 's',
        'PLATEGA_BASE_URL': 'https://app.platega.io',
        'PLATEGA_API_VERSION': 'v1',
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value, raising=False)


# --- create_subscription ---


@pytest.mark.asyncio
async def test_create_subscription_posts_method_6(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    service = PlategaService()
    captured = {}

    async def fake_request(method, endpoint, *, json_data=None, params=None):
        captured.update(method=method, endpoint=endpoint, json_data=json_data)
        return {'transactionId': 'tx-1', 'redirect': 'https://pay/x', 'status': 'PENDING'}

    monkeypatch.setattr(service, '_request', fake_request)
    res = await service.create_subscription(amount=199.0, currency='RUB', interval=3, description='Тариф')

    assert captured['method'] == 'POST'
    assert captured['endpoint'] == '/transaction/process'
    assert captured['json_data']['paymentMethod'] == 6
    assert captured['json_data']['paymentDetails'] == {'amount': 199, 'currency': 'RUB', 'interval': 3}
    assert captured['json_data']['description'] == 'Тариф'
    assert res['transactionId'] == 'tx-1'


@pytest.mark.asyncio
async def test_create_subscription_uses_v2_endpoint_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, PLATEGA_API_VERSION='v2')
    service = PlategaService()
    captured = {}

    async def fake_request(method, endpoint, *, json_data=None, params=None):
        captured.update(method=method, endpoint=endpoint)
        return {'transactionId': 'tx-2', 'status': 'PENDING'}

    monkeypatch.setattr(service, '_request', fake_request)
    await service.create_subscription(amount=149.5, currency='RUB', interval=1)

    assert service.api_version == 'v2'
    assert captured['endpoint'] == '/v2/transaction/process'


@pytest.mark.asyncio
async def test_create_subscription_omits_description_when_not_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    service = PlategaService()
    captured = {}

    async def fake_request(method, endpoint, *, json_data=None, params=None):
        captured.update(json_data=json_data)
        return {'transactionId': 'tx-3', 'status': 'PENDING'}

    monkeypatch.setattr(service, '_request', fake_request)
    await service.create_subscription(amount=100.0, currency='RUB', interval=2)

    assert 'description' not in captured['json_data']


@pytest.mark.asyncio
async def test_create_subscription_truncates_long_cyrillic_description(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    service = PlategaService()
    captured = {}

    async def fake_request(method, endpoint, *, json_data=None, params=None):
        captured.update(json_data=json_data)
        return {'transactionId': 'tx-4', 'status': 'PENDING'}

    monkeypatch.setattr(service, '_request', fake_request)
    long_description = 'Премиум тариф на 3 месяца безлимит и ещё немного текста'
    await service.create_subscription(amount=199.0, currency='RUB', interval=3, description=long_description)

    description_in_body = captured['json_data']['description']
    # Verify truncated to 64 bytes
    assert len(description_in_body.encode('utf-8')) <= 64
    # Verify it's a valid UTF-8 string and a prefix of the original
    assert long_description.startswith(description_in_body)
    # Verify it's not empty
    assert description_in_body


# --- get_subscription ---


@pytest.mark.asyncio
async def test_get_subscription_is_unversioned(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, PLATEGA_API_VERSION='v2')
    service = PlategaService()
    captured = {}

    async def fake_request(method, endpoint, *, json_data=None, params=None):
        captured.update(method=method, endpoint=endpoint)
        return {'subscriptionId': 'sub-1', 'status': 'active'}

    monkeypatch.setattr(service, '_request', fake_request)
    res = await service.get_subscription('sub-1')

    assert captured == {'method': 'GET', 'endpoint': '/subscription/sub-1'}
    assert res['subscriptionId'] == 'sub-1'


# --- list_subscriptions ---


@pytest.mark.asyncio
async def test_list_subscriptions_builds_query_params(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    service = PlategaService()
    captured = {}

    async def fake_request(method, endpoint, *, json_data=None, params=None):
        captured.update(method=method, endpoint=endpoint, params=params)
        return {'items': [], 'total': 0}

    monkeypatch.setattr(service, '_request', fake_request)
    await service.list_subscriptions(status='active', date_from='2026-01-01', date_to='2026-02-01', page=2, size=10)

    assert captured['method'] == 'GET'
    assert captured['endpoint'] == '/subscription'
    assert captured['params'] == {
        'status': 'active',
        'from': '2026-01-01',
        'to': '2026-02-01',
        'page': 2,
        'size': 10,
    }


@pytest.mark.asyncio
async def test_list_subscriptions_omits_none_params(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    service = PlategaService()
    captured = {}

    async def fake_request(method, endpoint, *, json_data=None, params=None):
        captured.update(endpoint=endpoint, params=params)
        return {'items': []}

    monkeypatch.setattr(service, '_request', fake_request)
    await service.list_subscriptions()

    assert captured['endpoint'] == '/subscription'
    assert captured['params'] == {}


# --- cancel_subscription ---


@pytest.mark.asyncio
async def test_cancel_subscription_posts_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    service = PlategaService()
    captured = {}

    async def fake_request(method, endpoint, *, json_data=None, params=None):
        captured.update(method=method, endpoint=endpoint, json_data=json_data)
        return {'subscriptionId': 'sub-1', 'status': 'cancelled'}

    monkeypatch.setattr(service, '_request', fake_request)
    res = await service.cancel_subscription('sub-1')

    assert captured['method'] == 'POST'
    assert captured['endpoint'] == '/subscription/sub-1/cancel'
    assert res['status'] == 'cancelled'


# --- _format_amount ---


def test_format_amount_integer_and_decimal() -> None:
    assert PlategaService._format_amount(199.0) == 199
    assert PlategaService._format_amount(149.5) == 149.5
    assert isinstance(PlategaService._format_amount(199.0), int)
    assert isinstance(PlategaService._format_amount(149.5), float)
