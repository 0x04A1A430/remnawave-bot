"""Тесты Pal24 сценариев PaymentService."""

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
from app.services.pal24_service import Pal24APIError  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class DummySession:
    async def commit(self) -> None:  # pragma: no cover
        return None


class DummyLocalPayment:
    def __init__(self, payment_id: int = 404) -> None:
        self.id = payment_id
        self.created_at = datetime(2024, 1, 2, 10, 0, 0)


class StubPal24Service:
    def __init__(self, *, configured: bool = True, response: Optional[Dict[str, Any]] = None) -> None:
        self.is_configured = configured
        self.response = response or {
            "success": True,
            "bill_id": "BILL-1",
            "transfer_url": "https://pal24/sbp",
            "link_url": "https://pal24/card",
            "status": "NEW",
        }
        self.calls: list[Dict[str, Any]] = []
        self.raise_error: Optional[Exception] = None

    async def create_bill(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        if self.raise_error:
            raise self.raise_error
        return self.response


def _make_service(stub: Optional[StubPal24Service]) -> PaymentService:
    service = PaymentService.__new__(PaymentService)  # type: ignore[call-arg]
    service.bot = None
    service.pal24_service = stub
    service.mulenpay_service = None
    service.yookassa_service = None
    service.cryptobot_service = None
    service.stars_service = None
    return service


@pytest.mark.anyio("asyncio")
async def test_create_pal24_payment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubPal24Service()
    service = _make_service(stub)
    db = DummySession()

    captured_args: Dict[str, Any] = {}

    async def fake_create_pal24_payment(*args: Any, **kwargs: Any) -> DummyLocalPayment:
        captured_args.update(kwargs)
        if args:
            captured_args["db_arg"] = args[0]
        return DummyLocalPayment(payment_id=321)

    monkeypatch.setattr(
        payment_service_module,
        "create_pal24_payment",
        fake_create_pal24_payment,
        raising=False,
    )
    monkeypatch.setattr(settings, "PAL24_MIN_AMOUNT_KOPEKS", 1000, raising=False)
    monkeypatch.setattr(settings, "PAL24_MAX_AMOUNT_KOPEKS", 1_000_000, raising=False)

    result = await service.create_pal24_payment(
        db=db,
        user_id=15,
        amount_kopeks=50000,
        description="Оплата подписки",
        language="ru",
        ttl_seconds=600,
        payer_email="user@example.com",
        payment_method="card",
    )

    assert result is not None
    assert result["local_payment_id"] == 321
    assert result["bill_id"] == "BILL-1"
    assert result["payment_method"] == "card"
    assert result["link_url"] == "https://pal24/sbp"
    assert result["card_url"] == "https://pal24/card"
    assert stub.calls and stub.calls[0]["amount_kopeks"] == 50000
    assert stub.calls[0]["payment_method"] == "bank_card"
    assert "links" in captured_args["metadata"]


@pytest.mark.anyio("asyncio")
async def test_create_pal24_payment_default_method(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubPal24Service()
    service = _make_service(stub)
    db = DummySession()

    async def fake_create_pal24_payment(*args: Any, **kwargs: Any) -> DummyLocalPayment:
        return DummyLocalPayment(payment_id=111)

    monkeypatch.setattr(
        payment_service_module,
        "create_pal24_payment",
        fake_create_pal24_payment,
        raising=False,
    )
    monkeypatch.setattr(settings, "PAL24_MIN_AMOUNT_KOPEKS", 1000, raising=False)
    monkeypatch.setattr(settings, "PAL24_MAX_AMOUNT_KOPEKS", 1_000_000, raising=False)

    result = await service.create_pal24_payment(
        db=db,
        user_id=42,
        amount_kopeks=150000,
        description="Пополнение",
        language="ru",
    )

    assert result is not None
    assert result["payment_method"] == "sbp"
    assert stub.calls and stub.calls[0]["payment_method"] == "fast_payment"


@pytest.mark.anyio("asyncio")
async def test_create_pal24_payment_limits_and_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubPal24Service()
    service = _make_service(stub)
    db = DummySession()

    monkeypatch.setattr(settings, "PAL24_MIN_AMOUNT_KOPEKS", 5000, raising=False)
    monkeypatch.setattr(settings, "PAL24_MAX_AMOUNT_KOPEKS", 20_000, raising=False)

    result_low = await service.create_pal24_payment(
        db=db,
        user_id=1,
        amount_kopeks=1000,
        description="Пополнение",
        language="ru",
    )
    assert result_low is None

    result_high = await service.create_pal24_payment(
        db=db,
        user_id=1,
        amount_kopeks=50_000,
        description="Пополнение",
        language="ru",
    )
    assert result_high is None

    service_not_configured = _make_service(StubPal24Service(configured=False))
    result_config = await service_not_configured.create_pal24_payment(
        db=db,
        user_id=1,
        amount_kopeks=10_000,
        description="Пополнение",
        language="ru",
    )
    assert result_config is None


@pytest.mark.anyio("asyncio")
async def test_create_pal24_payment_handles_api_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubPal24Service()
    stub.raise_error = Pal24APIError("api failed")
    service = _make_service(stub)
    db = DummySession()

    monkeypatch.setattr(settings, "PAL24_MIN_AMOUNT_KOPEKS", 1000, raising=False)
    monkeypatch.setattr(settings, "PAL24_MAX_AMOUNT_KOPEKS", 10_000, raising=False)

    result = await service.create_pal24_payment(
        db=db,
        user_id=5,
        amount_kopeks=2000,
        description="Пополнение",
        language="ru",
    )
    assert result is None
