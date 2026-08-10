from app.services.platega_service import PlategaService


def test_sanitize_description_limits_utf8_bytes() -> None:
    original = 'Интернет-сервис - Пополнение баланса на 50 ₽ и ещё чуть-чуть'

    trimmed = PlategaService._sanitize_description(original, 64)

    assert len(trimmed.encode('utf-8')) <= 64
    assert trimmed != original


def test_sanitize_description_returns_clean_value() -> None:
    original = '  Обычное описание  '

    trimmed = PlategaService._sanitize_description(original, 64)

    assert trimmed == 'Обычное описание'
    assert len(trimmed.encode('utf-8')) <= 64


async def test_create_payment_falls_back_from_v1_to_v2(monkeypatch) -> None:
    """Карточные каскады работают только на v2 (#2934): v1 отвечает 400
    «No available card cascades», после чего тот же запрос повторяется на v2."""
    service = PlategaService()
    service.api_version = 'v1'

    calls: list[str] = []

    async def fake_request(method, endpoint, *, json_data=None, params=None, return_status=False):
        calls.append(endpoint)
        if endpoint == '/transaction/process':
            return {'data': [{'key': 'paymentMethod', 'message': 'No available card cascades'}]}, 400
        return {'transactionId': 'tx_v2', 'url': 'https://pay.example/v2'}, 200

    monkeypatch.setattr(service, '_request', fake_request)

    result = await service.create_payment(payment_method=11, amount=100.0, currency='RUB')

    assert calls == ['/transaction/process', '/v2/transaction/process']
    assert result == {'transactionId': 'tx_v2', 'url': 'https://pay.example/v2'}


async def test_create_payment_v2_used_directly(monkeypatch) -> None:
    """При PLATEGA_API_VERSION=v2 фолбэк не нужен — запрос сразу идёт на v2."""
    service = PlategaService()
    service.api_version = 'v2'

    calls: list[str] = []

    async def fake_request(method, endpoint, *, json_data=None, params=None, return_status=False):
        calls.append(endpoint)
        return {'transactionId': 'tx_v2', 'url': 'https://pay.example/v2'}, 200

    monkeypatch.setattr(service, '_request', fake_request)

    result = await service.create_payment(payment_method=11, amount=100.0, currency='RUB')

    assert calls == ['/v2/transaction/process']
    assert result == {'transactionId': 'tx_v2', 'url': 'https://pay.example/v2'}


async def test_create_payment_v1_success_no_fallback(monkeypatch) -> None:
    """Если v1 ответил 200 — на v2 не ходим."""
    service = PlategaService()
    service.api_version = 'v1'

    calls: list[str] = []

    async def fake_request(method, endpoint, *, json_data=None, params=None, return_status=False):
        calls.append(endpoint)
        return {'transactionId': 'tx_v1', 'redirect': 'https://pay.example/v1'}, 200

    monkeypatch.setattr(service, '_request', fake_request)

    result = await service.create_payment(payment_method=2, amount=100.0, currency='RUB')

    assert calls == ['/transaction/process']
    assert result == {'transactionId': 'tx_v1', 'redirect': 'https://pay.example/v1'}
