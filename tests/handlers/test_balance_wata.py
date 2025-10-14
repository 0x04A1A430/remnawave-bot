from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.handlers.balance import check_wata_payment_status  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class DummyMessage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.calls.append((text, kwargs))


class DummyCallback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = DummyMessage()
        self.bot = object()
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str = "", show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class DummyPayment:
    def __init__(self) -> None:
        self.order_id = "WATA-TEST"
        self.amount_kopeks = 2_500
        self.status = "Opened"
        self.transaction_status: Optional[str] = None
        self.created_at = datetime(2024, 1, 5, 12, 30)
        self.is_paid = False
        self.payment_url = "https://pay.example"
        self.user = type("U", (), {"language": "en"})()


@pytest.mark.anyio("asyncio")
async def test_check_wata_payment_status_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payment = DummyPayment()
    remote_transaction = {"status": "Paid"}

    class FakePaymentService:
        def __init__(self, bot: Any) -> None:
            self.bot = bot

        async def get_wata_payment_status(
            self,
            db: Any,
            local_payment_id: int,
        ) -> Dict[str, Any] | None:
            assert local_payment_id == 5
            payment.transaction_status = "Paid"
            payment.is_paid = True
            return {
                "payment": payment,
                "remote_transaction": remote_transaction,
            }

    monkeypatch.setattr(
        "app.handlers.balance.PaymentService",
        FakePaymentService,
        raising=False,
    )

    callback = DummyCallback("check_wata_5")

    await check_wata_payment_status(callback, db=None)

    assert callback.answers[0] == ("", False)
    assert callback.message.calls, "expected message to be sent"
    message_text, kwargs = callback.message.calls[0]
    assert "Wata Pay payment status" in message_text
    assert "Order ID: WATA-TEST" in message_text
    assert "https://pay.example" not in message_text
    assert kwargs["disable_web_page_preview"] is True


@pytest.mark.anyio("asyncio")
async def test_check_wata_payment_status_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePaymentService:
        def __init__(self, bot: Any) -> None:
            self.bot = bot

        async def get_wata_payment_status(
            self,
            db: Any,
            local_payment_id: int,
        ) -> Dict[str, Any] | None:
            return None

    monkeypatch.setattr(
        "app.handlers.balance.PaymentService",
        FakePaymentService,
        raising=False,
    )

    callback = DummyCallback("check_wata_77")

    await check_wata_payment_status(callback, db=None)

    assert callback.answers
    not_found_text, alert = callback.answers[0]
    assert alert is True
    assert "Платеж не найден" in not_found_text
