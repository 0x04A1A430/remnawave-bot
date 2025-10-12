"""Юнит-тесты MulenPayService."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
import sys

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings  # noqa: E402
from app.services.mulenpay_service import MulenPayService  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _enable_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type(settings), "is_mulenpay_enabled", lambda self: True, raising=False)
    monkeypatch.setattr(settings, "MULENPAY_API_KEY", "api", raising=False)
    monkeypatch.setattr(settings, "MULENPAY_SHOP_ID", "shop", raising=False)
    monkeypatch.setattr(settings, "MULENPAY_SECRET_KEY", "secret", raising=False)
    monkeypatch.setattr(settings, "MULENPAY_BASE_URL", "https://mulenpay.test", raising=False)


def test_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MulenPayService()
    assert service.is_configured is False

    _enable_service(monkeypatch)
    service = MulenPayService()
    assert service.is_configured is True


def test_format_and_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_service(monkeypatch)
    service = MulenPayService()
    assert service._format_amount(12345) == "123.45"
    signature = service._build_signature("rub", "100.00")
    assert isinstance(signature, str) and len(signature) == 40


@pytest.mark.anyio("asyncio")
async def test_create_payment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_service(monkeypatch)

    captured_payload: Dict[str, Any] = {}

    async def fake_request(method: str, endpoint: str, **kwargs: Any) -> Dict[str, Any]:
        captured_payload.update({"method": method, "endpoint": endpoint, **kwargs})
        return {"success": True, "id": 101, "paymentUrl": "https://mulenpay/pay"}

    service = MulenPayService()
    monkeypatch.setattr(service, "_request", fake_request, raising=False)

    result = await service.create_payment(
        amount_kopeks=25000,
        description="Пополнение",
        uuid="uuid-1",
        items=[{"description": "item", "quantity": 1, "price": 250.0}],
        language="ru",
        website_url="https://example.com",
    )

    assert result is not None
    assert result["id"] == 101
    assert captured_payload["method"] == "POST"
    assert captured_payload["endpoint"] == "/v2/payments"
    assert captured_payload["json_data"]["language"] == "ru"


@pytest.mark.anyio("asyncio")
async def test_create_payment_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_service(monkeypatch)
    service = MulenPayService()

    async def fake_request(*args: Any, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return None

    monkeypatch.setattr(service, "_request", fake_request, raising=False)

    result = await service.create_payment(
        amount_kopeks=1000,
        description="desc",
        uuid="uuid",
        items=[],
    )
    assert result is None


@pytest.mark.anyio("asyncio")
async def test_get_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_service(monkeypatch)
    service = MulenPayService()

    async def fake_request(method: str, endpoint: str, **kwargs: Any) -> Dict[str, Any]:
        return {"id": 123, "status": "paid"}

    monkeypatch.setattr(service, "_request", fake_request, raising=False)
    result = await service.get_payment(123)
    assert result == {"id": 123, "status": "paid"}
