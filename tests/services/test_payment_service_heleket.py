import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
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
    def __init__(
        self,
        response: Optional[Dict[str, Any]],
        *,
        info_response: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.response = response
        self.info_response = info_response
        self.calls: list[Dict[str, Any]] = []
        self.info_calls: list[Dict[str, Optional[str]]] = []
        self.list_response: Optional[Dict[str, Any]] = None
        self.list_calls: list[Dict[str, Optional[str]]] = []

    async def create_payment(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.calls.append(payload)
        return self.response

    async def get_payment_info(
        self,
        *,
        uuid: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        self.info_calls.append({"uuid": uuid, "order_id": order_id})
        return self.info_response

    async def list_payments(
        self,
        *,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        self.list_calls.append(
            {"date_from": date_from, "date_to": date_to, "cursor": cursor}
        )
        return self.list_response


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


@pytest.mark.anyio("asyncio")
async def test_sync_heleket_payment_status_success(monkeypatch: pytest.MonkeyPatch) -> None:
    info_response = {
        "state": 0,
        "result": {
            "uuid": "heleket-uuid",
            "order_id": "order-123",
            "status": "paid",
            "payment_amount": "100.00",
        },
    }
    stub = StubHeleketService(response=None, info_response=info_response)
    service = _make_service(stub)
    db = DummySession()

    payment = SimpleNamespace(
        id=55,
        uuid="heleket-uuid",
        order_id="order-123",
        status="check",
        user_id=7,
    )

    async def fake_get_by_id(db, payment_id):
        assert payment_id == payment.id
        return payment

    captured: Dict[str, Any] = {}

    async def fake_process(self, db, payload, *, metadata_key):
        captured["payload"] = payload
        captured["metadata_key"] = metadata_key
        return SimpleNamespace(transaction_id=999, **payload)

    monkeypatch.setattr(heleket_crud, "get_heleket_payment_by_id", fake_get_by_id, raising=False)
    monkeypatch.setattr(PaymentService, "_process_heleket_payload", fake_process, raising=False)

    result = await service.sync_heleket_payment_status(db, local_payment_id=payment.id)

    assert result is not None
    assert result.transaction_id == 999
    assert captured["metadata_key"] == "last_status_check"
    assert captured["payload"]["uuid"] == payment.uuid
    assert stub.info_calls == [{"uuid": payment.uuid, "order_id": payment.order_id}]


@pytest.mark.anyio("asyncio")
async def test_sync_heleket_payment_status_without_response(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubHeleketService(response=None, info_response=None)
    service = _make_service(stub)
    db = DummySession()

    payment = SimpleNamespace(
        id=12,
        uuid="heleket-uuid",
        order_id="order-123",
        status="check",
        user_id=5,
    )

    async def fake_get_by_id(db, payment_id):
        assert payment_id == payment.id
        return payment

    async def fake_process(*args, **kwargs):  # pragma: no cover - ensure not called
        raise AssertionError("_process_heleket_payload should not be called")

    monkeypatch.setattr(heleket_crud, "get_heleket_payment_by_id", fake_get_by_id, raising=False)
    monkeypatch.setattr(PaymentService, "_process_heleket_payload", fake_process, raising=False)

    result = await service.sync_heleket_payment_status(db, local_payment_id=payment.id)

    assert result is payment
    assert stub.info_calls == [{"uuid": payment.uuid, "order_id": payment.order_id}]
    assert stub.list_calls  # fallback to history should be attempted


@pytest.mark.anyio("asyncio")
async def test_sync_heleket_payment_status_history_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubHeleketService(response=None, info_response=None)
    stub.list_response = {
        "state": 0,
        "result": {
            "items": [
                {
                    "uuid": "heleket-uuid",
                    "order_id": "order-123",
                    "status": "paid",
                    "payment_amount": "150.00",
                }
            ],
            "paginate": {"nextCursor": None},
        },
    }
    service = _make_service(stub)
    db = DummySession()

    payment = SimpleNamespace(
        id=77,
        uuid="heleket-uuid",
        order_id="order-123",
        status="check",
        user_id=8,
    )

    async def fake_get_by_id(db, payment_id):
        assert payment_id == payment.id
        return payment

    captured: Dict[str, Any] = {}

    async def fake_process(self, db, payload, *, metadata_key):
        captured["payload"] = payload
        captured["metadata_key"] = metadata_key
        return SimpleNamespace(**payload)

    monkeypatch.setattr(heleket_crud, "get_heleket_payment_by_id", fake_get_by_id, raising=False)
    monkeypatch.setattr(PaymentService, "_process_heleket_payload", fake_process, raising=False)

    result = await service.sync_heleket_payment_status(db, local_payment_id=payment.id)

    assert result is not None
    assert captured["payload"]["status"] == "paid"
    assert stub.list_calls
