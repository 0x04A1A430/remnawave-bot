"""Tests for WATA payment mixin."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
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
    async def commit(self) -> None:  # pragma: no cover - no logic required
        return None

    async def refresh(self, *_: Any) -> None:  # pragma: no cover - no logic required
        return None


class DummyLocalPayment:
    def __init__(self, payment_id: int = 42) -> None:
        self.id = payment_id
        self.created_at = datetime.utcnow()


class StubWataService:
    def __init__(self, response: Optional[Dict[str, Any]]) -> None:
        self.response = response
        self.calls: list[Dict[str, Any]] = []

    async def create_payment_link(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        self.calls.append(kwargs)
        return self.response


def _make_service(stub: Optional[StubWataService]) -> PaymentService:
    service = PaymentService.__new__(PaymentService)  # type: ignore[call-arg]
    service.bot = None
    service.wata_service = stub
    service.mulenpay_service = None
    service.pal24_service = None
    service.yookassa_service = None
    service.stars_service = None
    service.cryptobot_service = None
    return service


@pytest.mark.anyio("asyncio")
async def test_create_wata_payment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {
        "id": "123e4567-e89b-12d3-a456-426614174000",
        "url": "https://wata.example/link",
        "status": "Opened",
        "type": "OneTime",
        "terminalPublicId": "terminal-id",
        "successRedirectUrl": "https://example.com/success",
        "failRedirectUrl": "https://example.com/fail",
        "expirationDateTime": "2030-01-01T00:00:00Z",
    }
    stub = StubWataService(response)
    service = _make_service(stub)
    db = DummySession()

    captured_args: Dict[str, Any] = {}

    async def fake_create_wata_payment(**kwargs: Any) -> DummyLocalPayment:
        captured_args.update(kwargs)
        return DummyLocalPayment(payment_id=777)

    monkeypatch.setattr(payment_service_module, "create_wata_payment", fake_create_wata_payment, raising=False)
    monkeypatch.setattr(settings, "WATA_MIN_AMOUNT_KOPEKS", 5000, raising=False)
    monkeypatch.setattr(settings, "WATA_MAX_AMOUNT_KOPEKS", 500_000, raising=False)

    result = await service.create_wata_payment(
        db=db,
        user_id=101,
        amount_kopeks=15000,
        description="Пополнение",
        language="ru",
    )

    assert result is not None
    assert result["local_payment_id"] == 777
    assert result["payment_link_id"] == response["id"]
    assert result["payment_url"] == response["url"]
    assert captured_args["user_id"] == 101
    assert captured_args["amount_kopeks"] == 15000
    assert captured_args["payment_link_id"] == response["id"]
    assert stub.calls and stub.calls[0]["amount_kopeks"] == 15000


@pytest.mark.anyio("asyncio")
async def test_create_wata_payment_respects_amount_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubWataService({"id": "link"})
    service = _make_service(stub)
    db = DummySession()

    monkeypatch.setattr(settings, "WATA_MIN_AMOUNT_KOPEKS", 10_000, raising=False)
    monkeypatch.setattr(settings, "WATA_MAX_AMOUNT_KOPEKS", 20_000, raising=False)

    too_low = await service.create_wata_payment(
        db=db,
        user_id=1,
        amount_kopeks=5_000,
        description="Пополнение",
    )
    assert too_low is None

    too_high = await service.create_wata_payment(
        db=db,
        user_id=1,
        amount_kopeks=25_000,
        description="Пополнение",
    )
    assert too_high is None
    assert not stub.calls


@pytest.mark.anyio("asyncio")
async def test_create_wata_payment_returns_none_without_service() -> None:
    service = _make_service(None)
    db = DummySession()

    result = await service.create_wata_payment(
        db=db,
        user_id=5,
        amount_kopeks=10_000,
        description="Пополнение",
    )
    assert result is None
