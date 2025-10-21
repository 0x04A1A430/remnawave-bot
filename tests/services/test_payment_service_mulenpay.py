"""Тесты для сценариев MulenPay в PaymentService."""

from pathlib import Path
from typing import Any, Dict, Optional
import sys
from datetime import datetime

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.services.payment_service as payment_service_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.payment_service import PaymentService  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class DummySession:
    async def commit(self) -> None:  # pragma: no cover - метод вызывается, но без логики
        return None


class DummyLocalPayment:
    def __init__(self, payment_id: int = 501) -> None:
        self.id = payment_id
        self.created_at = datetime(2024, 1, 1, 12, 0, 0)


class StubMulenPayService:
    def __init__(self, response: Optional[Dict[str, Any]]) -> None:
        self.response = response
        self.calls: list[Dict[str, Any]] = []

    async def create_payment(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        self.calls.append(kwargs)
        return self.response


def _make_service(stub: Optional[StubMulenPayService]) -> PaymentService:
    service = PaymentService.__new__(PaymentService)  # type: ignore[call-arg]
    service.bot = None
    service.mulenpay_service = stub
    service.pal24_service = None
    service.yookassa_service = None
    service.stars_service = None
    service.cryptobot_service = None
    service.heleket_service = None
    return service


@pytest.mark.anyio("asyncio")
async def test_create_mulenpay_payment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {"id": 123, "paymentUrl": "https://mulenpay/pay"}
    stub = StubMulenPayService(response)
    service = _make_service(stub)
    db = DummySession()

    captured_args: Dict[str, Any] = {}

    async def fake_create_mulenpay_payment(**kwargs: Any) -> DummyLocalPayment:
        captured_args.update(kwargs)
        return DummyLocalPayment(payment_id=999)

    monkeypatch.setattr(
        payment_service_module,
        "create_mulenpay_payment",
        fake_create_mulenpay_payment,
        raising=False,
    )
    monkeypatch.setattr(settings, "MULENPAY_MIN_AMOUNT_KOPEKS", 1000, raising=False)
    monkeypatch.setattr(settings, "MULENPAY_MAX_AMOUNT_KOPEKS", 1_000_000, raising=False)
    monkeypatch.setattr(settings, "MULENPAY_VAT_CODE", 1, raising=False)
    monkeypatch.setattr(settings, "MULENPAY_PAYMENT_SUBJECT", "service", raising=False)
    monkeypatch.setattr(settings, "MULENPAY_PAYMENT_MODE", "full_payment", raising=False)
    monkeypatch.setattr(settings, "MULENPAY_LANGUAGE", "ru", raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_URL", "https://example.com", raising=False)

    result = await service.create_mulenpay_payment(
        db=db,
        user_id=77,
        amount_kopeks=25000,
        description="Пополнение",
        language="en",
    )

    assert result is not None
    assert result["local_payment_id"] == 999
    assert result["mulen_payment_id"] == 123
    assert result["payment_url"] == "https://mulenpay/pay"
    assert result["status"] == "created"
    assert stub.calls and stub.calls[0]["language"] == "en"
    assert captured_args["user_id"] == 77
    assert captured_args["amount_kopeks"] == 25000
    assert captured_args["uuid"].startswith("mulen_77_")


@pytest.mark.anyio("asyncio")
async def test_create_mulenpay_payment_respects_amount_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubMulenPayService({"id": 1})
    service = _make_service(stub)
    db = DummySession()

    monkeypatch.setattr(settings, "MULENPAY_MIN_AMOUNT_KOPEKS", 5000, raising=False)
    monkeypatch.setattr(settings, "MULENPAY_MAX_AMOUNT_KOPEKS", 10_000, raising=False)

    result_low = await service.create_mulenpay_payment(
        db=db,
        user_id=1,
        amount_kopeks=1000,
        description="Пополнение",
    )
    assert result_low is None

    result_high = await service.create_mulenpay_payment(
        db=db,
        user_id=1,
        amount_kopeks=20_000,
        description="Пополнение",
    )
    assert result_high is None
    assert not stub.calls


@pytest.mark.anyio("asyncio")
async def test_create_mulenpay_payment_returns_none_without_service() -> None:
    service = _make_service(None)
    db = DummySession()

    result = await service.create_mulenpay_payment(
        db=db,
        user_id=1,
        amount_kopeks=5000,
        description="Пополнение",
    )
    assert result is None
