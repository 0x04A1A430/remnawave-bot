import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.payment_service import PaymentService  # noqa: E402
from app.database.crud import heleket as heleket_crud  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class DummySession:
    def __init__(self) -> None:
        self.added_objects: list[Any] = []

    async def commit(self) -> None:  # pragma: no cover - behaviour is mocked in tests
        return None

    async def refresh(self, obj: Any) -> None:  # pragma: no cover
        return None

    def add(self, obj: Any) -> None:  # pragma: no cover
        self.added_objects.append(obj)


class DummyLocalPayment:
    def __init__(self, payment_id: int = 123) -> None:
        self.id = payment_id
        self.created_at = datetime(2024, 1, 1, 12, 0, 0)


class StubHeleketService:
    def __init__(self, response: Optional[Dict[str, Any]]) -> None:
        self.response = response
        self.calls: list[Dict[str, Any]] = []

    async def create_payment(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.calls.append(payload)
        return self.response


def _make_service(stub: Optional[StubHeleketService]) -> PaymentService:
    service = PaymentService.__new__(PaymentService)  # type: ignore[call-arg]
    service.bot = None
    service.heleket_service = stub
    service.yookassa_service = None
    service.stars_service = None
    service.cryptobot_service = None
    service.mulenpay_service = None
    service.pal24_service = None
    service.wata_service = None
    return service


@pytest.mark.anyio("asyncio")
async def test_create_heleket_payment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {
        "state": 0,
        "result": {
            "uuid": "heleket-uuid",
            "order_id": "order-123",
            "url": "https://heleket/pay",
            "status": "check",
            "payer_amount": "12.50",
            "payer_currency": "USDT",
            "discount_percent": -5,
            "payer_amount_exchange_rate": "0.0125",
            "expired_at": 1750000000,
        },
    }
    stub = StubHeleketService(response)
    service = _make_service(stub)
    db = DummySession()

    captured_args: Dict[str, Any] = {}

    async def fake_create_heleket_payment(**kwargs: Any) -> DummyLocalPayment:
        captured_args.update(kwargs)
        return DummyLocalPayment(payment_id=555)

    monkeypatch.setattr(
        heleket_crud,
        "create_heleket_payment",
        fake_create_heleket_payment,
        raising=False,
    )

    result = await service.create_heleket_payment(
        db=db,
        user_id=42,
        amount_kopeks=15000,
        description="Пополнение",
        language="ru",
    )

    assert result is not None
    assert result["local_payment_id"] == 555
    assert result["uuid"] == "heleket-uuid"
    assert result["order_id"] == "order-123"
    assert result["payment_url"] == "https://heleket/pay"
    assert stub.calls and stub.calls[0]["amount"] == "150.00"
    assert captured_args["uuid"] == "heleket-uuid"
    assert captured_args["user_id"] == 42


@pytest.mark.anyio("asyncio")
async def test_create_heleket_payment_returns_none_without_service() -> None:
    service = _make_service(None)
    db = DummySession()

    result = await service.create_heleket_payment(
        db=db,
        user_id=1,
        amount_kopeks=10000,
        description="Пополнение",
    )

    assert result is None


@pytest.mark.anyio("asyncio")
async def test_create_heleket_payment_handles_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubHeleketService(response=None)
    service = _make_service(stub)
    db = DummySession()

    called = False

    async def fake_create_heleket_payment(**kwargs: Any) -> DummyLocalPayment:
        nonlocal called
        called = True
        return DummyLocalPayment()

    monkeypatch.setattr(
        heleket_crud,
        "create_heleket_payment",
        fake_create_heleket_payment,
        raising=False,
    )

    result = await service.create_heleket_payment(
        db=db,
        user_id=1,
        amount_kopeks=20000,
        description="Пополнение",
    )

    assert result is None
    assert called is False
