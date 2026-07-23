"""Тесты создания СБП-подписки Platega через миксин: персист + отключение balance-autopay."""

import contextlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.crud import platega_subscription as sub_crud
from app.database.models import Base, PlategaSubscription


def _ensure_real_aiosqlite(monkeypatch) -> None:
    """Снять фиктивный sys.modules['aiosqlite'] перед созданием реального engine.

    ``tests/conftest.py`` подставляет пустой ``ModuleType('aiosqlite')`` через
    ``sys.modules.setdefault(...)`` для окружений без реального пакета. Но эта
    строчка выполняется раньше, чем что-либо успевает импортировать настоящий
    aiosqlite, поэтому заглушка перекрывает и физически установленный пакет —
    ``create_async_engine('sqlite+aiosqlite:///:memory:')`` падает с
    ``AttributeError: module 'aiosqlite' has no attribute 'Connection'``.
    Удаляем нерабочую заглушку (нет ``connect``): следующий ``import aiosqlite``
    внутри диалекта SQLAlchemy подтянет настоящий установленный модуль.
    """
    stub = sys.modules.get('aiosqlite')
    if stub is not None and not hasattr(stub, 'connect'):
        monkeypatch.delitem(sys.modules, 'aiosqlite', raising=False)


@contextlib.asynccontextmanager
async def _memory_session(monkeypatch):
    """Реальная in-memory SQLite сессия только с таблицей platega_subscriptions.

    Полный create_all не годится (другие таблицы используют JSONB, SQLite не
    компилирует), а FK в SQLite по умолчанию не форсятся — поэтому user_id/
    subscription_id можно ставить произвольными, реальные строки не нужны.
    """
    _ensure_real_aiosqlite(monkeypatch)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[PlategaSubscription.__table__]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await engine.dispose()


async def test_create_sbp_subscription_persists_and_disables_autopay(monkeypatch):
    """create_platega_sbp_subscription: сохраняет запись, выключает autopay_enabled,
    возвращает redirect/status из ответа Platega.
    """
    from app.services.payment.platega import PlategaPaymentMixin

    subscription = SimpleNamespace(id=1, autopay_enabled=True, autopay_period_days=30)
    tariff = SimpleNamespace(
        id=5,
        is_daily=False,
        name='Стандарт',
        get_available_periods=lambda: [30, 90],
        get_shortest_period=lambda: 30,
        get_purchasable_price_for_period=lambda d: 19900,
    )

    class Svc(PlategaPaymentMixin):
        def __init__(self):
            self.platega_service = SimpleNamespace(
                create_subscription=AsyncMock(
                    return_value={'transactionId': 'tx-9', 'redirect': 'https://pay/9', 'status': 'PENDING'}
                )
            )

    async with _memory_session(monkeypatch) as db:
        result = await Svc().create_platega_sbp_subscription(db, user_id=777, subscription=subscription, tariff=tariff)

        assert result['platega_subscription_id'] == 'tx-9'
        assert result['redirect_url'] == 'https://pay/9'
        assert result['status'] == 'PENDING'
        assert subscription.autopay_enabled is False

        stored = await sub_crud.get_active_platega_subscription_by_subscription(db, subscription.id)
        assert stored is not None
        assert stored.id == result['local_id']
        assert stored.interval == 3
        assert stored.charge_days == 30
        assert stored.amount_kopeks == 19900


async def test_create_sbp_subscription_is_idempotent_on_repeat_call(monkeypatch):
    """Повторный вызов (двойной тап / ретрай клиента) не должен плодить вторую
    Platega-подписку и не должен повторно звать Platega API — иначе пользователя
    спишут дважды за цикл, а первая привязка станет невидимой для отмены
    (get_active_platega_subscription_by_subscription отдаёт только последнюю запись).
    """
    from app.services.payment.platega import PlategaPaymentMixin

    subscription = SimpleNamespace(id=1, autopay_enabled=True, autopay_period_days=30)
    tariff = SimpleNamespace(
        id=5,
        is_daily=False,
        name='Стандарт',
        get_available_periods=lambda: [30, 90],
        get_shortest_period=lambda: 30,
        get_purchasable_price_for_period=lambda d: 19900,
    )

    class Svc(PlategaPaymentMixin):
        def __init__(self):
            self.platega_service = SimpleNamespace(
                create_subscription=AsyncMock(
                    return_value={'transactionId': 'tx-9', 'redirect': 'https://pay/9', 'status': 'PENDING'}
                )
            )

    async with _memory_session(monkeypatch) as db:
        svc = Svc()
        stub = svc.platega_service

        first = await svc.create_platega_sbp_subscription(db, user_id=777, subscription=subscription, tariff=tariff)
        second = await svc.create_platega_sbp_subscription(db, user_id=777, subscription=subscription, tariff=tariff)

        assert stub.create_subscription.await_count == 1
        assert second['platega_subscription_id'] == first['platega_subscription_id']
        assert second['local_id'] == first['local_id']

        rows = await sub_crud.list_platega_subscriptions_by_statuses(db, ['PENDING', 'ACTIVE', 'PAST_DUE'])
        assert len([r for r in rows if r.subscription_id == subscription.id]) == 1
