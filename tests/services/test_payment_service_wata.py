from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional

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
    async def commit(self) -> None:  # pragma: no cover
        return None


class DummyPayment:
    def __init__(self, payment_id: int = 101) -> None:
        self.id = payment_id


class DummyWataPayment:
    def __init__(self) -> None:
        self.id = 11
        self.user_id = 77
        self.amount_kopeks = 50000
        self.order_id = "wata_77_test"
        self.status = "Opened"
        self.transaction_status = None
        self.is_paid = False
        self.wata_link_id = "link"
        self.payment_url = "https://pay"
        self.created_at = datetime(2024, 1, 1, 12, 0, 0)
        self.user = type("U", (), {"language": "ru", "telegram_id": 123})()


class StubWataService:
    def __init__(self, response: Optional[Dict[str, Any]]) -> None:
        self.response = response
        self.calls: list[Dict[str, Any]] = []
        self.is_configured = True

    async def create_payment_link(self, **payload: Any) -> Optional[Dict[str, Any]]:
        self.calls.append(payload)
        return self.response


def _make_service(stub: Optional[StubWataService]) -> PaymentService:
    service = PaymentService.__new__(PaymentService)  # type: ignore[call-arg]
    service.bot = None
    service.yookassa_service = None
    service.stars_service = None
    service.cryptobot_service = None
    service.mulenpay_service = None
    service.pal24_service = None
    service.wata_service = stub
    return service


@pytest.mark.anyio("asyncio")
async def test_create_wata_payment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubWataService({"id": "link", "url": "https://pay"})
    service = _make_service(stub)
    db = DummySession()

    captured_kwargs: Dict[str, Any] = {}

    async def fake_create_wata_payment(*args: Any, **kwargs: Any) -> DummyPayment:
        captured_kwargs.update(kwargs)
        return DummyPayment(payment_id=555)

    monkeypatch.setattr(
        payment_service_module,
        "create_wata_payment",
        fake_create_wata_payment,
        raising=False,
    )

    monkeypatch.setattr(settings, "WATA_MIN_AMOUNT_KOPEKS", 1_000, raising=False)
    monkeypatch.setattr(settings, "WATA_MAX_AMOUNT_KOPEKS", 1_000_000_00, raising=False)
    monkeypatch.setattr(settings, "WATA_DEFAULT_CURRENCY", "RUB", raising=False)

    result = await service.create_wata_payment(
        db=db,
        user_id=7,
        amount_kopeks=25000,
        description="Пополнение",
        language="ru",
    )

    assert result is not None
    assert result["local_payment_id"] == 555
    assert result["payment_url"] == "https://pay"
    assert stub.calls and stub.calls[0]["amount"] == "250.00"
    assert captured_kwargs["order_id"].startswith("wata_7_")


@pytest.mark.anyio("asyncio")
async def test_create_wata_payment_amount_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubWataService({"id": "link", "url": "https://pay"})
    service = _make_service(stub)
    db = DummySession()

    monkeypatch.setattr(settings, "WATA_MIN_AMOUNT_KOPEKS", 5000, raising=False)
    monkeypatch.setattr(settings, "WATA_MAX_AMOUNT_KOPEKS", 10_000, raising=False)

    low_result = await service.create_wata_payment(
        db=db,
        user_id=1,
        amount_kopeks=1000,
        description="Пополнение",
    )
    assert low_result is None

    high_result = await service.create_wata_payment(
        db=db,
        user_id=1,
        amount_kopeks=20_000,
        description="Пополнение",
    )
    assert high_result is None
    assert not stub.calls


@pytest.mark.anyio("asyncio")
async def test_create_wata_payment_without_service() -> None:
    service = _make_service(None)
    db = DummySession()

    result = await service.create_wata_payment(
        db=db,
        user_id=1,
        amount_kopeks=10_000,
        description="Пополнение",
    )

    assert result is None


@pytest.mark.anyio("asyncio")
async def test_process_wata_webhook_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(None)
    db = DummySession()
    payment = DummyWataPayment()

    async def fake_get_by_order_id(db_session: Any, order_id: str) -> DummyWataPayment | None:
        assert order_id == payment.order_id
        return payment

    async def fake_get_by_link_id(db_session: Any, link_id: str) -> DummyWataPayment | None:
        return None

    async def fake_get_by_local_id(db_session: Any, payment_id: int) -> DummyWataPayment | None:
        assert payment_id == payment.id
        return payment

    async def fake_update_status(
        db_session: Any,
        *,
        payment: DummyWataPayment,
        status: str | None = None,
        transaction_status: str | None = None,
        is_paid: bool | None = None,
        paid_at: datetime | None = None,
        callback_payload: dict | None = None,
        external_transaction_id: str | None = None,
        payment_url: str | None = None,
        last_status_payload: dict | None = None,
    ) -> DummyWataPayment:
        if status is not None:
            payment.status = status
        if transaction_status is not None:
            payment.transaction_status = transaction_status
        if is_paid is not None:
            payment.is_paid = is_paid
        if payment_url is not None:
            payment.payment_url = payment_url
        if callback_payload is not None:
            payment.callback_payload = callback_payload
        if external_transaction_id is not None:
            payment.external_transaction_id = external_transaction_id
        if last_status_payload is not None:
            payment.last_status_payload = last_status_payload
        return payment

    finalize_calls: Dict[str, Any] = {}

    async def fake_finalize(
        self: PaymentService,
        db_session: Any,
        payment: DummyWataPayment,
        *,
        amount_kopeks: int,
        transaction_id: str,
        transaction_payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        finalize_calls["amount"] = amount_kopeks
        finalize_calls["transaction_id"] = transaction_id
        payment.is_paid = True
        return True

    monkeypatch.setattr(
        payment_service_module,
        "get_wata_payment_by_order_id",
        fake_get_by_order_id,
        raising=False,
    )
    monkeypatch.setattr(
        payment_service_module,
        "get_wata_payment_by_link_id",
        fake_get_by_link_id,
        raising=False,
    )
    monkeypatch.setattr(
        payment_service_module,
        "get_wata_payment_by_local_id",
        fake_get_by_local_id,
        raising=False,
    )
    monkeypatch.setattr(
        payment_service_module,
        "update_wata_payment_status",
        fake_update_status,
        raising=False,
    )
    monkeypatch.setattr(
        PaymentService,
        "_finalize_wata_payment",
        fake_finalize,
        raising=False,
    )

    payload = {
        "orderId": payment.order_id,
        "transactionStatus": "Paid",
        "transactionId": "tx-123",
        "amount": "500.00",
    }

    processed = await service.process_wata_webhook(db, payload)

    assert processed is True
    assert finalize_calls["amount"] == 50_000
    assert finalize_calls["transaction_id"] == "tx-123"
    assert payment.is_paid is True


@pytest.mark.anyio("asyncio")
async def test_process_wata_webhook_missing_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(None)
    db = DummySession()

    async def fake_get_by_order_id(db_session: Any, order_id: str) -> None:
        return None

    async def fake_get_by_link_id(db_session: Any, link_id: str) -> None:
        return None

    monkeypatch.setattr(
        payment_service_module,
        "get_wata_payment_by_order_id",
        fake_get_by_order_id,
        raising=False,
    )
    monkeypatch.setattr(
        payment_service_module,
        "get_wata_payment_by_link_id",
        fake_get_by_link_id,
        raising=False,
    )

    payload = {"orderId": "missing", "transactionStatus": "Paid"}

    processed = await service.process_wata_webhook(db, payload)

    assert processed is False
