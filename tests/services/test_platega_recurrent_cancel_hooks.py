"""Тесты отмены СБП-подписки Platega через миксин: идемпотентная точечная
отмена (``cancel_platega_sbp_subscription``) и best-effort хелпер по
``subscription_id`` (``cancel_platega_recurring_for_subscription``), который
будет вызываться из путей удаления подписки (Task 11).
"""

import contextlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.crud import platega_subscription as sub_crud
from app.database.models import Base, PlategaSubscription


def _ensure_real_aiosqlite(monkeypatch) -> None:
    """Снять фиктивный ``sys.modules['aiosqlite']`` перед созданием реального engine.

    См. одноимённую функцию в ``test_platega_subscription_callbacks.py``:
    ``tests/conftest.py`` подставляет нерабочую заглушку модуля через
    ``sys.modules.setdefault(...)``, которая перекрывает физически
    установленный пакет и валит ``create_async_engine('sqlite+aiosqlite:///:memory:')``.
    """
    stub = sys.modules.get('aiosqlite')
    if stub is not None and not hasattr(stub, 'connect'):
        monkeypatch.delitem(sys.modules, 'aiosqlite', raising=False)


@contextlib.asynccontextmanager
async def _memory_session(monkeypatch):
    """Реальная in-memory SQLite сессия с таблицей ``platega_subscriptions``.

    Методы отмены не трогают ``Subscription``/``Transaction``, поэтому в
    отличие от ``test_platega_subscription_callbacks.py`` этих таблиц здесь
    не нужно — хватает одной ``PlategaSubscription.__table__``.
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


async def _create_recurring_record(db, *, platega_subscription_id, status='ACTIVE'):
    return await sub_crud.create_platega_subscription(
        db,
        user_id=1,
        subscription_id=1,
        tariff_id=None,
        interval=3,
        charge_days=30,
        amount_kopeks=19900,
        redirect_url=None,
        platega_subscription_id=platega_subscription_id,
        status=status,
    )


async def test_cancel_by_subscription_calls_platega_and_marks_cancelled(monkeypatch):
    """cancel_platega_recurring_for_subscription: находит активную запись по
    subscription_id, отменяет её на стороне Platega и локально. Повторный
    вызов, когда активной записи уже нет, — no-op без исключения
    (идемпотентность на уровне subscription_id).
    """
    from app.services.payment.platega import PlategaPaymentMixin

    async with _memory_session(monkeypatch) as db:
        rec = await _create_recurring_record(db, platega_subscription_id='ps-2')

        class Svc(PlategaPaymentMixin):
            def __init__(self):
                self.platega_service = SimpleNamespace(
                    cancel_subscription=AsyncMock(return_value={'status': 'cancelled'})
                )

        svc = Svc()
        await svc.cancel_platega_recurring_for_subscription(db, rec.subscription_id)

        svc.platega_service.cancel_subscription.assert_awaited_once_with('ps-2')
        await db.refresh(rec)
        assert rec.status == 'CANCELLED'

        # Идемпотентность второго вызова: активной записи больше нет.
        await svc.cancel_platega_recurring_for_subscription(db, rec.subscription_id)
        assert svc.platega_service.cancel_subscription.await_count == 1


async def test_cancel_best_effort_swallows_platega_error(monkeypatch):
    """Ошибка Platega API при отмене (сеть/5xx) не должна пробрасываться
    наружу — вызывающие пути удаления подписки не должны блокироваться
    недоступностью Platega. Локальный статус CANCELLED всё равно применяется.
    """
    from app.services.payment.platega import PlategaPaymentMixin

    async with _memory_session(monkeypatch) as db:
        rec = await _create_recurring_record(db, platega_subscription_id='ps-3')

        class Svc(PlategaPaymentMixin):
            def __init__(self):
                self.platega_service = SimpleNamespace(
                    cancel_subscription=AsyncMock(side_effect=RuntimeError('platega down'))
                )

        await Svc().cancel_platega_recurring_for_subscription(db, rec.subscription_id)  # не должен бросить

        await db.refresh(rec)
        assert rec.status == 'CANCELLED'  # локальная отмена применена несмотря на ошибку Platega


async def test_cancel_pending_without_platega_id_skips_api(monkeypatch):
    """PENDING-запись, которая не успела получить platega_subscription_id
    (сбой между вызовом Platega и записью id) — отмена должна просто
    пометить запись CANCELLED локально, не дёргая API без id.
    """
    from app.services.payment.platega import PlategaPaymentMixin

    async with _memory_session(monkeypatch) as db:
        rec = await sub_crud.create_platega_subscription(
            db,
            user_id=1,
            subscription_id=1,
            tariff_id=None,
            interval=3,
            charge_days=30,
            amount_kopeks=19900,
            redirect_url=None,
            platega_subscription_id=None,
            status='PENDING',
        )

        class Svc(PlategaPaymentMixin):
            def __init__(self):
                self.platega_service = SimpleNamespace(cancel_subscription=AsyncMock())

        svc = Svc()
        result = await svc.cancel_platega_sbp_subscription(db, local_id=rec.id)

        assert result is True
        await db.refresh(rec)
        assert rec.status == 'CANCELLED'
        svc.platega_service.cancel_subscription.assert_not_awaited()
