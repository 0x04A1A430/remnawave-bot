"""Глобальные фикстуры и настройки окружения для тестов."""

import asyncio
import inspect
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Add project root to Python path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Подменяем параметры подключения к БД, чтобы SQLAlchemy не требовал aiosqlite.
os.environ.setdefault("DATABASE_MODE", "postgresql")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/test_db")
os.environ.setdefault("BOT_TOKEN", "test-token")

# Создаём заглушки для драйверов, которых может не быть в окружении тестов.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))
sys.modules.setdefault("aiosqlite", types.ModuleType("aiosqlite"))

# Эмуляция redis.asyncio, чтобы модуль кеша мог импортироваться.
if "redis.asyncio" not in sys.modules:
    redis_module = types.ModuleType("redis")
    redis_async_module = types.ModuleType("redis.asyncio")

    class _FakeRedisClient:
        async def ping(self):
            """Имитируем успешный ответ ping."""
            return True

        async def close(self):
            """Закрытие соединения ничего не делает."""

        async def get(self, key):  # noqa: ANN001
            return None

        async def set(self, key, value, ex=None):  # noqa: ANN001
            return True

        async def delete(self, *keys):  # noqa: ANN001
            return 0

        async def keys(self, pattern="*"):  # noqa: ANN001
            return []

        async def exists(self, key):  # noqa: ANN001
            return False

        async def expire(self, key, seconds):  # noqa: ANN001
            return True

        async def incr(self, key):  # noqa: ANN001
            return 1

    def _from_url(url):  # noqa: ANN001
        return _FakeRedisClient()

    redis_async_module.from_url = _from_url
    redis_async_module.Redis = _FakeRedisClient
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_async_module

# Минимальная реализация SDK YooKassa, чтобы импорт сервисов не падал.
if "yookassa" not in sys.modules:
    fake_yookassa = types.ModuleType("yookassa")

    class _FakeConfiguration:
        @staticmethod
        def configure(*args, **kwargs):
            """Конфигурация заглушки ничего не делает."""

    class _FakePayment:
        @staticmethod
        def create(*args, **kwargs):
            """Возвращает объект с минимально необходимыми атрибутами."""

            class _Response:
                id = "yk_fake"
                status = "pending"
                paid = False
                refundable = False
                metadata = {}
                amount = types.SimpleNamespace(value="0.00", currency="RUB")
                confirmation = types.SimpleNamespace(confirmation_url="https://example.com")
                created_at = datetime.utcnow()
                description = ""
                test = False

            return _Response()

    fake_yookassa.Configuration = _FakeConfiguration
    fake_yookassa.Payment = _FakePayment
    sys.modules["yookassa"] = fake_yookassa

    # Подготавливаем вложенные пакеты, используемые сервисом.
    domain_module = types.ModuleType("yookassa.domain")
    request_module = types.ModuleType("yookassa.domain.request")
    payment_builder_module = types.ModuleType("yookassa.domain.request.payment_request_builder")
    common_module = types.ModuleType("yookassa.domain.common")
    confirmation_module = types.ModuleType("yookassa.domain.common.confirmation_type")

    class _FakePaymentRequestBuilder:
        def __init__(self):
            self.data: dict = {}

        def set_amount(self, value):  # noqa: ANN001 - упрощённая заглушка
            self.data["amount"] = value
            return self

        def set_capture(self, value):  # noqa: ANN001
            self.data["capture"] = value
            return self

        def set_confirmation(self, value):  # noqa: ANN001
            self.data["confirmation"] = value
            return self

        def set_description(self, value):  # noqa: ANN001
            self.data["description"] = value
            return self

        def set_metadata(self, value):  # noqa: ANN001
            self.data["metadata"] = value
            return self

        def set_receipt(self, value):  # noqa: ANN001
            self.data["receipt"] = value
            return self

        def set_payment_method_data(self, value):  # noqa: ANN001
            self.data["payment_method_data"] = value
            return self

        def build(self):
            return self.data

    class _FakeConfirmationType:
        REDIRECT = "redirect"

    payment_builder_module.PaymentRequestBuilder = _FakePaymentRequestBuilder
    confirmation_module.ConfirmationType = _FakeConfirmationType

    sys.modules["yookassa.domain"] = domain_module
    sys.modules["yookassa.domain.request"] = request_module
    sys.modules["yookassa.domain.request.payment_request_builder"] = payment_builder_module
    sys.modules["yookassa.domain.common"] = common_module
    sys.modules["yookassa.domain.common.confirmation_type"] = confirmation_module


@pytest.fixture
def fixed_datetime() -> datetime:
    """Возвращает фиксированную отметку времени для воспроизводимых проверок."""
    return datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def pytest_configure(config: pytest.Config) -> None:
    """Регистрируем маркеры для асинхронных тестов."""

    config.addinivalue_line(
        "markers",
        "asyncio: запуск асинхронного теста через встроенный цикл событий",
    )
    config.addinivalue_line(
        "markers",
        "anyio: запуск асинхронного теста через встроенный цикл событий",
    )


def _unwrap_test(obj):  # noqa: ANN001 - вспомогательная функция для определения coroutine
    """Возвращает исходную функцию, снимая обёртки pytest и декораторов."""

    unwrapped = obj
    while hasattr(unwrapped, "__wrapped__"):
        unwrapped = unwrapped.__wrapped__
    return unwrapped


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Позволяет запускать async def тесты без дополнительных плагинов."""

    # Пропускаем если pytest-asyncio уже обработал этот тест
    if hasattr(pyfuncitem, "_request") and hasattr(pyfuncitem._request, "_pyfuncitem"):
        markers = list(pyfuncitem.iter_markers())
        for marker in markers:
            if marker.name in ("asyncio", "anyio"):
                # pytest-asyncio обработает этот тест
                return None

    test_func = _unwrap_test(pyfuncitem.obj)
    if not inspect.iscoroutinefunction(test_func):
        return None

    # Проверяем, не обработан ли уже тест плагином pytest-asyncio
    # Если pyfuncitem.obj не возвращает корутину - пропускаем
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        signature = inspect.signature(test_func)
        call_kwargs = {
            name: value
            for name, value in pyfuncitem.funcargs.items()
            if name in signature.parameters
        }
        coro = pyfuncitem.obj(**call_kwargs)
        if coro is None:
            # Уже обработано другим плагином
            return None
        loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    return True
