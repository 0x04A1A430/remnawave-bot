"""Интеграционные проверки обработки вебхуков PaymentService."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace, ModuleType
from typing import Any, Dict
import sys

import pytest
from unittest.mock import AsyncMock

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.services.payment_service as payment_service_module  # noqa: E402
from app.services.payment_service import PaymentService  # noqa: E402
from app.config import settings  # noqa: E402


class DummyBot:
    def __init__(self) -> None:
        self.sent_messages: list[Dict[str, Any]] = []

    async def send_message(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - бизнес-логика тестируется через вызов
        self.sent_messages.append({"args": args, "kwargs": kwargs})


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.refreshed: list[Any] = []
        self.added: list[Any] = []

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:  # pragma: no cover
        return None

    async def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)

    def add(self, obj: Any) -> None:  # pragma: no cover - используется при создании транзакций
        self.added.append(obj)


def _make_service(bot: DummyBot) -> PaymentService:
    service = PaymentService.__new__(PaymentService)  # type: ignore[call-arg]
    service.bot = bot
    service.yookassa_service = None
    service.stars_service = None
    service.mulenpay_service = None
    service.pal24_service = None
    service.cryptobot_service = None
    return service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_process_mulenpay_callback_success(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = DummyBot()
    service = _make_service(bot)
    fake_session = FakeSession()
    payment = SimpleNamespace(
        uuid="mulen_uuid",
        mulen_payment_id=123,
        amount_kopeks=5000,
        user_id=42,
        transaction_id=None,
        is_paid=False,
    )

    async def fake_get_by_uuid(db, uuid):
        return payment

    async def fake_get_by_id(db, mid):
        return None

    monkeypatch.setattr(payment_service_module, "get_mulenpay_payment_by_uuid", fake_get_by_uuid)
    monkeypatch.setattr(payment_service_module, "get_mulenpay_payment_by_mulen_id", fake_get_by_id)

    transactions: list[Dict[str, Any]] = []

    async def fake_create_transaction(db, **kwargs):
        transactions.append(kwargs)
        return SimpleNamespace(id=777, **kwargs)

    monkeypatch.setattr(payment_service_module, "create_transaction", fake_create_transaction)

    updated_status: dict[str, Any] = {}

    async def fake_update_status(db, payment=None, status=None, **kwargs):
        payment.status = status
        payment.is_paid = kwargs.get("is_paid", payment.is_paid)
        updated_status.update({"status": status, "kwargs": kwargs})

    monkeypatch.setattr(payment_service_module, "update_mulenpay_payment_status", fake_update_status)

    async def fake_link(db, payment=None, transaction_id=None):
        payment.transaction_id = transaction_id

    monkeypatch.setattr(payment_service_module, "link_mulenpay_payment_to_transaction", fake_link)

    user = SimpleNamespace(
        id=42,
        telegram_id=100500,
        balance_kopeks=0,
        has_made_first_topup=False,
        promo_group=None,
        subscription=None,
        referred_by_id=None,
        referrer=None,
    )

    async def fake_get_user(db, user_id):
        return user

    monkeypatch.setattr(payment_service_module, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(type(settings), "format_price", lambda self, amount: f"{amount / 100:.2f}₽", raising=False)

    referral_mock = SimpleNamespace(process_referral_topup=AsyncMock())
    monkeypatch.setitem(sys.modules, "app.services.referral_service", referral_mock)

    class DummyAdminService:
        def __init__(self, bot):
            self.bot = bot
            self.calls: list[Any] = []

        async def send_balance_topup_notification(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    admin_service = DummyAdminService(bot)
    monkeypatch.setitem(sys.modules, "app.services.admin_notification_service", SimpleNamespace(AdminNotificationService=lambda bot: admin_service))

    service.build_topup_success_keyboard = AsyncMock(return_value=None)

    payload = {
        "uuid": "mulen_uuid",
        "payment_status": "success",
        "id": 123,
        "amount": "50.00",
    }

    result = await service.process_mulenpay_callback(fake_session, payload)

    assert result is True
    assert transactions and transactions[0]["user_id"] == 42
    assert payment.transaction_id == 777
    assert updated_status["status"] == "success"
    assert updated_status["kwargs"].get("is_paid") is True
    assert updated_status["kwargs"].get("paid_at") is not None
    assert user.balance_kopeks == 5000
    assert fake_session.commits >= 1
    assert bot.sent_messages  # сообщение пользователю отправлено


@pytest.mark.anyio("asyncio")
async def test_process_cryptobot_webhook_success(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = DummyBot()
    service = _make_service(bot)
    fake_session = FakeSession()
    payment = SimpleNamespace(
        invoice_id="inv_1",
        user_id=7,
        status="pending",
        transaction_id=None,
        amount="12.50",
        asset="USDT",
        amount_float=12.5,
    )

    async def fake_get_crypto(db, invoice_id):
        return payment

    async def fake_update_status(db, invoice_id, status, paid_at):
        payment.status = status
        payment.paid_at = paid_at
        return payment

    async def fake_link(db, invoice_id, transaction_id):
        payment.transaction_id = transaction_id

    fake_cryptobot_module = ModuleType("app.database.crud.cryptobot")
    fake_cryptobot_module.get_cryptobot_payment_by_invoice_id = fake_get_crypto
    fake_cryptobot_module.update_cryptobot_payment_status = fake_update_status
    fake_cryptobot_module.link_cryptobot_payment_to_transaction = fake_link
    monkeypatch.setitem(sys.modules, "app.database.crud.cryptobot", fake_cryptobot_module)

    transactions: list[Dict[str, Any]] = []

    async def fake_create_transaction(db, **kwargs):
        transactions.append(kwargs)
        return SimpleNamespace(id=888, **kwargs)

    fake_transaction_module = ModuleType("app.database.crud.transaction")
    fake_transaction_module.create_transaction = fake_create_transaction
    monkeypatch.setitem(sys.modules, "app.database.crud.transaction", fake_transaction_module)
    monkeypatch.setattr(payment_service_module, "create_transaction", fake_create_transaction)

    user = SimpleNamespace(
        id=7,
        telegram_id=700,
        balance_kopeks=0,
        has_made_first_topup=False,
        promo_group=None,
        subscription=None,
        referred_by_id=None,
        referrer=None,
    )

    async def fake_get_user_crypto(db, user_id):
        return user

    monkeypatch.setattr(payment_service_module, "get_user_by_id", fake_get_user_crypto)

    referral_crypto = SimpleNamespace(process_referral_topup=AsyncMock())
    monkeypatch.setitem(sys.modules, "app.services.referral_service", referral_crypto)

    admin_calls: list[Any] = []

    class DummyAdminService2:
        def __init__(self, bot):
            self.bot = bot

        async def send_balance_topup_notification(self, *args, **kwargs):
            admin_calls.append((args, kwargs))

    monkeypatch.setitem(sys.modules, "app.services.admin_notification_service", SimpleNamespace(AdminNotificationService=lambda bot: DummyAdminService2(bot)))
    monkeypatch.setattr(payment_service_module.currency_converter, "usd_to_rub", AsyncMock(return_value=140.0))
    monkeypatch.setattr(type(settings), "format_price", lambda self, amount: f"{amount / 100:.2f}₽", raising=False)
    service.build_topup_success_keyboard = AsyncMock(return_value=None)

    payload = {
        "update_type": "invoice_paid",
        "payload": {
            "invoice_id": "inv_1",
            "paid_at": "2024-01-01T12:00:00Z",
        },
    }

    result = await service.process_cryptobot_webhook(fake_session, payload)

    assert result is True
    assert transactions and transactions[0]["amount_kopeks"] == 14000
    assert user.balance_kopeks == 14000
    assert payment.transaction_id == 888
    assert bot.sent_messages
    assert admin_calls


@pytest.mark.anyio("asyncio")
async def test_process_yookassa_webhook_success(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = DummyBot()
    service = _make_service(bot)
    fake_session = FakeSession()
    payment = SimpleNamespace(
        yookassa_payment_id="yk_123",
        user_id=21,
        amount_kopeks=10000,
        transaction_id=None,
        status="pending",
        is_paid=False,
    )

    async def fake_get_payment(db, payment_id):
        return payment

    async def fake_update(db, payment_id, status, is_paid, is_captured, captured_at, payment_method_type):
        payment.status = status
        payment.is_paid = is_paid
        payment.captured_at = captured_at
        return payment

    async def fake_link(db, payment_id, transaction_id):
        payment.transaction_id = transaction_id

    yk_module = ModuleType("app.database.crud.yookassa")
    yk_module.get_yookassa_payment_by_id = fake_get_payment
    yk_module.update_yookassa_payment_status = fake_update
    yk_module.link_yookassa_payment_to_transaction = fake_link
    monkeypatch.setitem(sys.modules, "app.database.crud.yookassa", yk_module)

    transactions: list[Dict[str, Any]] = []

    async def fake_create_transaction(db, **kwargs):
        transactions.append(kwargs)
        return SimpleNamespace(id=999, **kwargs)

    trx_module = ModuleType("app.database.crud.transaction")
    trx_module.create_transaction = fake_create_transaction
    monkeypatch.setitem(sys.modules, "app.database.crud.transaction", trx_module)
    monkeypatch.setattr(payment_service_module, "create_transaction", fake_create_transaction)
    monkeypatch.setattr(payment_service_module, "create_transaction", fake_create_transaction)
    monkeypatch.setattr(payment_service_module, "create_transaction", fake_create_transaction)

    user = SimpleNamespace(
        id=21,
        telegram_id=2100,
        balance_kopeks=0,
        has_made_first_topup=False,
        promo_group=None,
        subscription=None,
        referred_by_id=None,
        referrer=None,
    )

    async def fake_get_user(db, user_id):
        return user

    monkeypatch.setattr(payment_service_module, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(type(settings), "format_price", lambda self, amount: f"{amount / 100:.2f}₽", raising=False)

    referral_mock = SimpleNamespace(process_referral_topup=AsyncMock())
    monkeypatch.setitem(sys.modules, "app.services.referral_service", referral_mock)

    admin_calls: list[Any] = []

    class DummyAdminService:
        def __init__(self, bot):
            self.bot = bot

        async def send_balance_topup_notification(self, *args, **kwargs):
            admin_calls.append((args, kwargs))

    monkeypatch.setitem(sys.modules, "app.services.admin_notification_service", SimpleNamespace(AdminNotificationService=lambda bot: DummyAdminService(bot)))
    service.build_topup_success_keyboard = AsyncMock(return_value=None)

    payload = {
        "object": {
            "id": "yk_123",
            "status": "succeeded",
            "paid": True,
            "payment_method": {"type": "bank_card"},
        }
    }

    result = await service.process_yookassa_webhook(fake_session, payload)

    assert result is True
    assert transactions and transactions[0]["amount_kopeks"] == 10000
    assert payment.transaction_id == 999
    assert user.balance_kopeks == 10000
    assert bot.sent_messages
    assert admin_calls


@pytest.mark.anyio("asyncio")
async def test_process_yookassa_webhook_missing_id(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = DummyBot()
    service = _make_service(bot)
    db = FakeSession()

    result = await service.process_yookassa_webhook(db, {"object": {}})
    assert result is False


@pytest.mark.anyio("asyncio")
async def test_process_pal24_postback_success(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = DummyBot()
    service = _make_service(bot)
    service.pal24_service = SimpleNamespace(is_configured=True)
    fake_session = FakeSession()
    payment = SimpleNamespace(
        bill_id="BILL-1",
        order_id="order-1",
        amount_kopeks=5000,
        user_id=33,
        transaction_id=None,
        is_paid=False,
        status="NEW",
        metadata_json={},
        payment_method=None,
        paid_at=None,
    )

    async def fake_get_by_order(db, order_id):
        return payment

    async def fake_get_by_bill(db, bill_id):
        return payment

    async def fake_update(db, payment_obj, **kwargs):
        payment.status = kwargs.get("status", payment.status)
        payment.is_paid = kwargs.get("is_paid", payment.is_paid)
        payment.payment_status = kwargs.get("payment_status", payment.status)
        payment.callback_payload = kwargs.get("callback_payload")
        return payment

    async def fake_link(db, payment_obj, transaction_id):
        payment.transaction_id = transaction_id

    pal_module = ModuleType("app.database.crud.pal24")
    pal_module.get_pal24_payment_by_order_id = fake_get_by_order
    pal_module.get_pal24_payment_by_bill_id = fake_get_by_bill
    pal_module.update_pal24_payment_status = fake_update
    pal_module.link_pal24_payment_to_transaction = fake_link
    monkeypatch.setitem(sys.modules, "app.database.crud.pal24", pal_module)
    monkeypatch.setattr(payment_service_module, "get_pal24_payment_by_order_id", fake_get_by_order)
    monkeypatch.setattr(payment_service_module, "get_pal24_payment_by_bill_id", fake_get_by_bill)
    monkeypatch.setattr(payment_service_module, "update_pal24_payment_status", fake_update)
    monkeypatch.setattr(payment_service_module, "link_pal24_payment_to_transaction", fake_link)

    async def fake_create_transaction(db, **kwargs):
        payment.transaction_id = 654
        return SimpleNamespace(id=654, **kwargs)

    trx_module = ModuleType("app.database.crud.transaction")
    trx_module.create_transaction = fake_create_transaction
    monkeypatch.setitem(sys.modules, "app.database.crud.transaction", trx_module)
    monkeypatch.setattr(payment_service_module, "create_transaction", fake_create_transaction)

    user = SimpleNamespace(
        id=33,
        telegram_id=3300,
        balance_kopeks=0,
        has_made_first_topup=False,
        promo_group=None,
        subscription=None,
        referred_by_id=None,
        referrer=None,
        language="ru",
    )

    async def fake_get_user(db, user_id):
        return user

    monkeypatch.setattr(payment_service_module, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(type(settings), "format_price", lambda self, amount: f"{amount / 100:.2f}₽", raising=False)

    referral_pal = SimpleNamespace(process_referral_topup=AsyncMock())
    monkeypatch.setitem(sys.modules, "app.services.referral_service", referral_pal)

    admin_calls: list[Any] = []

    class DummyAdminServicePal:
        def __init__(self, bot):
            self.bot = bot

        async def send_balance_topup_notification(self, *args, **kwargs):
            admin_calls.append((args, kwargs))

    monkeypatch.setitem(
        sys.modules,
        "app.services.admin_notification_service",
        SimpleNamespace(AdminNotificationService=lambda bot: DummyAdminServicePal(bot)),
    )

    user_cart_stub = SimpleNamespace(
        user_cart_service=SimpleNamespace(has_user_cart=AsyncMock(return_value=True))
    )
    monkeypatch.setitem(sys.modules, "app.services.user_cart_service", user_cart_stub)

    class DummyTypes:
        class InlineKeyboardMarkup:
            def __init__(self, inline_keyboard=None, **kwargs):
                self.inline_keyboard = inline_keyboard or []
                self.kwargs = kwargs

        class InlineKeyboardButton:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

    monkeypatch.setitem(sys.modules, "aiogram", SimpleNamespace(types=DummyTypes))
    monkeypatch.setitem(
        sys.modules,
        "app.localization.texts",
        SimpleNamespace(get_texts=lambda language: SimpleNamespace(t=lambda key, default=None: default)),
    )

    service.build_topup_success_keyboard = AsyncMock(return_value=None)

    payload = {
        "InvId": "order-1",
        "OutSum": "50.00",
        "Status": "SUCCESS",
        "TrsId": "trs-1",
    }

    result = await service.process_pal24_postback(fake_session, payload)

    assert result is True
    assert payment.transaction_id == 654
    assert user.balance_kopeks == 5000
    assert bot.sent_messages
    saved_cart_message = bot.sent_messages[-1]
    reply_markup = saved_cart_message["kwargs"].get("reply_markup")
    assert reply_markup is not None
    assert reply_markup.inline_keyboard[0][0].kwargs["callback_data"] == "subscription_resume_checkout"
    assert admin_calls


@pytest.mark.anyio("asyncio")
async def test_get_pal24_payment_status_auto_finalize(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = DummyBot()
    service = _make_service(bot)

    class DummyPal24Service:
        BILL_SUCCESS_STATES = {"SUCCESS", "OVERPAID"}
        BILL_FAILED_STATES = {"FAIL"}
        BILL_PENDING_STATES = {"NEW", "PROCESS", "UNDERPAID"}

        async def get_bill_status(self, bill_id: str) -> Dict[str, Any]:
            return {
                "status": "SUCCESS",
                "bill": {
                    "status": "SUCCESS",
                    "payments": [
                        {
                            "id": "trs-auto-1",
                            "status": "SUCCESS",
                            "method": "SBP",
                            "balance_amount": "50.00",
                            "balance_currency": "RUB",
                        }
                    ],
                },
            }

    service.pal24_service = DummyPal24Service()

    fake_session = FakeSession()
    payment = SimpleNamespace(
        id=77,
        bill_id="BILL-AUTO",
        order_id="order-auto",
        amount_kopeks=5000,
        user_id=91,
        transaction_id=None,
        is_paid=False,
        status="NEW",
        metadata_json={},
        payment_id=None,
        payment_method=None,
        paid_at=None,
    )

    async def fake_get_payment_by_id(db, local_id):
        return payment

    async def fake_update_payment(db, payment_obj, **kwargs):
        for key, value in kwargs.items():
            setattr(payment, key, value)
        return payment

    async def fake_link_payment(db, payment_obj, transaction_id):
        payment.transaction_id = transaction_id
        return payment

    monkeypatch.setattr(payment_service_module, "get_pal24_payment_by_id", fake_get_payment_by_id)
    monkeypatch.setattr(payment_service_module, "update_pal24_payment_status", fake_update_payment)
    monkeypatch.setattr(payment_service_module, "link_pal24_payment_to_transaction", fake_link_payment)

    transactions: list[Dict[str, Any]] = []

    async def fake_create_transaction(db, **kwargs):
        transactions.append(kwargs)
        payment.transaction_id = 999
        return SimpleNamespace(id=999, **kwargs)

    monkeypatch.setattr(payment_service_module, "create_transaction", fake_create_transaction)

    user = SimpleNamespace(
        id=91,
        telegram_id=9100,
        balance_kopeks=0,
        has_made_first_topup=False,
        promo_group=None,
        subscription=None,
        referred_by_id=None,
        referrer=None,
        language="ru",
    )

    async def fake_get_user(db, user_id):
        return user

    monkeypatch.setattr(payment_service_module, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(type(settings), "format_price", lambda self, amount: f"{amount / 100:.2f}₽", raising=False)

    referral_stub = SimpleNamespace(process_referral_topup=AsyncMock())
    monkeypatch.setitem(sys.modules, "app.services.referral_service", referral_stub)

    admin_notifications: list[Any] = []

    class DummyAdminService:
        def __init__(self, bot):
            self.bot = bot

        async def send_balance_topup_notification(self, *args, **kwargs):
            admin_notifications.append((args, kwargs))

    monkeypatch.setitem(
        sys.modules,
        "app.services.admin_notification_service",
        SimpleNamespace(AdminNotificationService=lambda bot: DummyAdminService(bot)),
    )

    user_cart_stub = SimpleNamespace(
        user_cart_service=SimpleNamespace(has_user_cart=AsyncMock(return_value=False))
    )
    monkeypatch.setitem(sys.modules, "app.services.user_cart_service", user_cart_stub)

    class DummyTypes:
        class InlineKeyboardMarkup:
            def __init__(self, inline_keyboard=None, **kwargs):
                self.inline_keyboard = inline_keyboard or []
                self.kwargs = kwargs

        class InlineKeyboardButton:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

    monkeypatch.setitem(sys.modules, "aiogram", SimpleNamespace(types=DummyTypes))

    service.build_topup_success_keyboard = AsyncMock(return_value=None)

    result = await service.get_pal24_payment_status(fake_session, payment.id)

    assert result is not None
    assert payment.transaction_id == 999
    assert user.balance_kopeks == 5000
    assert bot.sent_messages
    assert admin_notifications
    assert transactions and transactions[0]["user_id"] == 91

@pytest.mark.anyio("asyncio")
async def test_process_pal24_postback_payment_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = DummyBot()
    service = _make_service(bot)
    service.pal24_service = SimpleNamespace(is_configured=True)
    db = FakeSession()

    async def fake_get_by_order(db, order_id):
        return None

    async def fake_get_by_bill(db, bill_id):
        return None

    pal_module = ModuleType("app.database.crud.pal24")
    pal_module.get_pal24_payment_by_order_id = fake_get_by_order
    pal_module.get_pal24_payment_by_bill_id = fake_get_by_bill
    pal_module.update_pal24_payment_status = AsyncMock()
    pal_module.link_pal24_payment_to_transaction = AsyncMock()
    monkeypatch.setitem(sys.modules, "app.database.crud.pal24", pal_module)
    monkeypatch.setattr(payment_service_module, "get_pal24_payment_by_order_id", fake_get_by_order)
    monkeypatch.setattr(payment_service_module, "get_pal24_payment_by_bill_id", fake_get_by_bill)

    payload = {
        "InvId": "order-unknown",
        "OutSum": "10.00",
        "Status": "SUCCESS",
    }

    result = await service.process_pal24_postback(db, payload)
    assert result is False
