"""Тесты Pal24Service и вспомогательных функций."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional
import sys

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings  # noqa: E402
from app.external.pal24_client import Pal24Client, Pal24APIError  # noqa: E402
from app.services.pal24_service import Pal24Service  # noqa: E402


class StubPal24Client:
    def __init__(self, configured: bool = True, response: Optional[Dict[str, Any]] = None) -> None:
        self.is_configured = configured
        self.response = response or {
            "success": True,
            "bill_id": "BILL42",
            "status": "NEW",
            "transfer_url": "https://pal24/sbp",
            "link_url": "https://pal24/card",
            "currency": "RUB",
        }
        self.calls: list[Dict[str, Any]] = []

    async def create_bill(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        return self.response

    async def get_bill_status(self, bill_id: str) -> Dict[str, Any]:
        return {"id": bill_id, "status": "NEW"}

    async def get_payment_status(self, payment_id: str) -> Dict[str, Any]:
        return {"id": payment_id, "status": "SUCCESS"}

    async def get_bill_payments(self, bill_id: str) -> Dict[str, Any]:
        return {"id": bill_id, "payments": [{"id": "PAY-1"}]}


def _enable_pal24(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type(settings), "is_pal24_enabled", lambda self: True, raising=False)
    monkeypatch.setattr(settings, "PAL24_SHOP_ID", "shop42", raising=False)
    monkeypatch.setattr(settings, "PAL24_SIGNATURE_TOKEN", "sigsecret", raising=False)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_create_bill_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_pal24(monkeypatch)
    client = StubPal24Client()
    service = Pal24Service(client)

    monkeypatch.setattr(Pal24Client, "normalize_amount", staticmethod(lambda amount: Decimal("500.00")), raising=False)

    result = await service.create_bill(
        amount_kopeks=50000,
        user_id=7,
        order_id="order-7",
        description="Пополнение",
        ttl_seconds=600,
        custom_payload={"extra": "value"},
        payer_email="user@example.com",
        payment_method="BANK_CARD",
    )

    assert result["bill_id"] == "BILL42"
    assert client.calls and client.calls[0]["amount"] == Decimal("500.00")
    assert client.calls[0]["shop_id"] == "shop42"
    assert client.calls[0]["description"] == "Пополнение"
    assert client.calls[0]["custom"] == {"extra": "value"}
    assert client.calls[0]["payment_method"] == "BANK_CARD"


@pytest.mark.anyio("asyncio")
async def test_create_bill_requires_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_pal24(monkeypatch)
    client = StubPal24Client(configured=False)
    service = Pal24Service(client)

    with pytest.raises(Pal24APIError):
        await service.create_bill(
            amount_kopeks=1000,
            user_id=1,
            order_id="order",
            description="desc",
        )


@pytest.mark.anyio("asyncio")
async def test_get_bill_payments(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_pal24(monkeypatch)
    client = StubPal24Client()
    service = Pal24Service(client)

    result = await service.get_bill_payments("BILL42")

    assert result == {"id": "BILL42", "payments": [{"id": "PAY-1"}]}


def test_parse_callback_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_pal24(monkeypatch)
    sig = Pal24Client.calculate_signature("100.00", "INV1", api_token="sigsecret")
    payload = {
        "InvId": "INV1",
        "OutSum": "100.00",
        "Status": "SUCCESS",
        "SignatureValue": sig,
    }
    result = Pal24Service.parse_callback(payload)
    assert result["InvId"] == "INV1"


def test_parse_callback_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_pal24(monkeypatch)
    with pytest.raises(Pal24APIError):
        Pal24Service.parse_callback({"InvId": "1"})


def test_convert_to_kopeks_and_expiration() -> None:
    assert Pal24Service.convert_to_kopeks("10.50") == 1050
    expiration = Pal24Service.get_expiration(60)
    assert isinstance(expiration, datetime)
    assert expiration - datetime.utcnow() <= timedelta(seconds=61)
