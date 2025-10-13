"""Тесты сценариев CryptoBot в PaymentService."""

from pathlib import Path
from typing import Any, Dict, Optional
import sys
from datetime import datetime

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings  # noqa: E402
from app.database.crud import cryptobot as cryptobot_crud  # noqa: E402
from app.services.payment_service import PaymentService  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class DummySession:
    def __init__(self) -> None:
        self.added_objects: list[Any] = []

    async def commit(self) -> None:  # pragma: no cover
        return None

    def add(self, obj: Any) -> None:  # pragma: no cover
        self.added_objects.append(obj)

    async def flush(self) -> None:  # pragma: no cover
        return None


class DummyLocalPayment:
    def __init__(self, payment_id: int = 888) -> None:
        self.id = payment_id
        self.created_at = datetime(2024, 3, 1, 9, 0, 0)


class StubCryptoBotService:
    def __init__(self, response: Optional[Dict[str, Any]]) -> None:
        self.response = response
        self.calls: list[Dict[str, Any]] = []

    async def create_invoice(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        self.calls.append(kwargs)
        return self.response


def _make_service(stub: Optional[StubCryptoBotService]) -> PaymentService:
    service = PaymentService.__new__(PaymentService)  # type: ignore[call-arg]
    service.bot = None
    service.cryptobot_service = stub
    service.mulenpay_service = None
    service.pal24_service = None
    service.yookassa_service = None
    service.stars_service = None
    return service


@pytest.mark.anyio("asyncio")
async def test_create_cryptobot_payment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {
        "invoice_id": 12345,
        "bot_invoice_url": "https://t.me/invoice",
        "mini_app_invoice_url": "https://mini.app/invoice",
        "web_app_invoice_url": "https://web.app/invoice",
    }
    stub = StubCryptoBotService(response)
    service = _make_service(stub)
    db = DummySession()

    captured_args: Dict[str, Any] = {}

    async def fake_create_cryptobot_payment(**kwargs: Any) -> DummyLocalPayment:
        captured_args.update(kwargs)
        return DummyLocalPayment(payment_id=555)

    monkeypatch.setattr(
        cryptobot_crud,
        "create_cryptobot_payment",
        fake_create_cryptobot_payment,
        raising=False,
    )
    monkeypatch.setattr(
        type(settings),
        "get_cryptobot_invoice_expires_seconds",
        lambda self: 600,
        raising=False,
    )

    result = await service.create_cryptobot_payment(
        db=db,
        user_id=9,
        amount_usd=12.5,
        asset="USDT",
        description="Пополнение",
        payload="custom",
    )

    assert result is not None
    assert result["local_payment_id"] == 555
    assert result["invoice_id"] == "12345"
    assert result["bot_invoice_url"] == "https://t.me/invoice"
    assert stub.calls and stub.calls[0]["expires_in"] == 600
    assert captured_args["invoice_id"] == "12345"
    assert captured_args["amount"] == "12.50"


@pytest.mark.anyio("asyncio")
async def test_create_cryptobot_payment_returns_none_when_service_missing() -> None:
    service = _make_service(None)
    db = DummySession()
    result = await service.create_cryptobot_payment(
        db=db,
        user_id=1,
        amount_usd=10,
    )
    assert result is None


@pytest.mark.anyio("asyncio")
async def test_create_cryptobot_payment_handles_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubCryptoBotService(response=None)
    service = _make_service(stub)
    db = DummySession()

    called = False

    async def fake_create_cryptobot_payment(**kwargs: Any) -> DummyLocalPayment:
        nonlocal called
        called = True
        return DummyLocalPayment()

    monkeypatch.setattr(
        cryptobot_crud,
        "create_cryptobot_payment",
        fake_create_cryptobot_payment,
        raising=False,
    )

    result = await service.create_cryptobot_payment(
        db=db,
        user_id=1,
        amount_usd=5,
    )
    assert result is None
    assert called is False
