"""Тесты СБП-подписки Platega через миксин: создание (персист + отключение
balance-autopay) и обработка коллбеков (продление, идемпотентность, статусы).
"""

import contextlib
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.crud import platega_subscription as sub_crud
from app.database.models import Base, PlategaSubscription, Subscription, Transaction


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
    """Реальная in-memory SQLite сессия с таблицами platega_subscriptions,
    subscriptions и transactions (последние две нужны коллбек-тестам — продление
    через ``Subscription.extend_subscription`` и аудит через ``create_transaction``).

    Полный create_all не годится (другие таблицы используют JSONB, SQLite не
    компилирует), а FK в SQLite по умолчанию не форсятся — поэтому user_id/
    subscription_id можно ставить произвольными; реальная строка Subscription
    нужна только тестам, которые проверяют фактическое продление end_date.
    """
    _ensure_real_aiosqlite(monkeypatch)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[PlategaSubscription.__table__, Subscription.__table__, Transaction.__table__]
            )
        )
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


# --- process_platega_subscription_callback ---
#
# Во всех тестах ниже Svc — миксин без атрибута ``bot``. ``_notify_sbp_recurring``
# сначала best-effort шлёт WS-событие в кабинет (без зарегистрированных
# подключений ``cabinet_ws_manager.send_to_user`` — no-op, БД не трогает), а
# затем возвращается на bot/telegram_id early-return
# (``getattr(self, 'bot', None)`` -> None) и не требует таблицы users.


async def _create_recurring_record(db, *, platega_subscription_id: str, status: str = 'ACTIVE'):
    """Создаёт запись Platega-подписки без реальной привязанной Subscription —
    для тестов статусных переходов, которым продление не нужно.
    """
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


async def test_confirmed_charge_extends_subscription_and_is_idempotent(monkeypatch):
    """CONFIRMED: продлевает Subscription.end_date на charge_days, пишет аудитную
    транзакцию SUBSCRIPTION_PAYMENT, инкрементит charges_success и сохраняет
    next_charge_at. Повтор того же charge Id (ретрай доставки коллбека от
    Platega) не должен продлевать подписку повторно.
    """
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    async with _memory_session(monkeypatch) as db:
        end0 = datetime.now(UTC) + timedelta(days=2)
        subscription = Subscription(id=1, user_id=1, status='active', end_date=end0)
        db.add(subscription)
        await db.commit()

        rec = await sub_crud.create_platega_subscription(
            db,
            user_id=1,
            subscription_id=1,
            tariff_id=None,
            interval=3,
            charge_days=30,
            amount_kopeks=19900,
            redirect_url=None,
            platega_subscription_id='ps-1',
            status='ACTIVE',
        )

        svc = Svc()
        payload = {
            'Status': 'CONFIRMED',
            'Id': 'charge-1',
            'Amount': 199,
            'Currency': 'RUB',
            'PaymentMethod': 6,
            'SubscriptionId': 'ps-1',
            'NextChargeAt': '2026-09-01T00:00:00Z',
        }
        await svc.process_platega_subscription_callback(db, payload)

        await db.refresh(subscription)
        await db.refresh(rec)
        assert subscription.end_date >= end0 + timedelta(days=29)
        assert rec.charges_success == 1
        assert rec.next_charge_at is not None
        assert rec.status == 'ACTIVE'
        assert rec.last_charge_external_id == 'charge-1'

        transactions = (await db.execute(select(Transaction))).scalars().all()
        assert len(transactions) >= 1

        end_after_first_charge = subscription.end_date

        # Реплей того же коллбека — идемпотентно, без повторного продления.
        await svc.process_platega_subscription_callback(db, payload)
        await db.refresh(rec)
        await db.refresh(subscription)
        assert rec.charges_success == 1
        assert subscription.end_date == end_after_first_charge


async def test_confirmed_charge_syncs_remnawave_panel_after_extension(monkeypatch):
    """CONFIRMED: после коммита продления должен best-effort синкнуться в панель
    RemnaWave через ``SubscriptionService.update_remnawave_user`` — иначе панель
    продолжает отдавать пользователю старый end_date, и оплативший продление
    через СБП пользователь всё равно отваливается по старой дате истечения,
    хотя бот репортит "продлено". Зеркалит balance-autopay
    (``monitoring_service._process_autopayments``, синк после extend_subscription).
    """
    from app.services.payment.platega import PlategaPaymentMixin
    from app.services.subscription_service import SubscriptionService

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    mock_sync = AsyncMock(return_value=None)
    monkeypatch.setattr(SubscriptionService, 'update_remnawave_user', mock_sync)

    async with _memory_session(monkeypatch) as db:
        end0 = datetime.now(UTC) + timedelta(days=2)
        subscription = Subscription(id=1, user_id=1, status='active', end_date=end0)
        db.add(subscription)
        await db.commit()

        await sub_crud.create_platega_subscription(
            db,
            user_id=1,
            subscription_id=1,
            tariff_id=None,
            interval=3,
            charge_days=30,
            amount_kopeks=19900,
            redirect_url=None,
            platega_subscription_id='ps-sync',
            status='ACTIVE',
        )

        payload = {
            'Status': 'CONFIRMED',
            'Id': 'charge-sync-1',
            'SubscriptionId': 'ps-sync',
        }
        await Svc().process_platega_subscription_callback(db, payload)

        mock_sync.assert_awaited_once()
        call = mock_sync.await_args
        assert call.args[0] is db
        assert call.args[1] is subscription
        assert call.kwargs.get('reset_reason') == 'СБП-автопродление'


async def test_confirmed_charge_panel_sync_failure_is_best_effort(monkeypatch):
    """Сбой синка панели (Remnawave недоступна) — best-effort: не должен
    пробрасывать исключение наружу (коллбек обязан вернуть 200 OK), а
    продление в БД (end_date, счётчики, last_charge_external_id) уже
    закоммичено и не откатывается синком, упавшим ПОСЛЕ commit.
    """
    from app.services.payment.platega import PlategaPaymentMixin
    from app.services.subscription_service import SubscriptionService

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    monkeypatch.setattr(SubscriptionService, 'update_remnawave_user', AsyncMock(side_effect=RuntimeError('panel down')))

    async with _memory_session(monkeypatch) as db:
        end0 = datetime.now(UTC) + timedelta(days=2)
        subscription = Subscription(id=1, user_id=1, status='active', end_date=end0)
        db.add(subscription)
        await db.commit()

        rec = await sub_crud.create_platega_subscription(
            db,
            user_id=1,
            subscription_id=1,
            tariff_id=None,
            interval=3,
            charge_days=30,
            amount_kopeks=19900,
            redirect_url=None,
            platega_subscription_id='ps-sync-fail',
            status='ACTIVE',
        )

        payload = {
            'Status': 'CONFIRMED',
            'Id': 'charge-sync-fail-1',
            'SubscriptionId': 'ps-sync-fail',
        }
        await Svc().process_platega_subscription_callback(db, payload)  # не должен бросить

        await db.refresh(subscription)
        await db.refresh(rec)
        assert subscription.end_date >= end0 + timedelta(days=29)
        assert rec.charges_success == 1
        assert rec.last_charge_external_id == 'charge-sync-fail-1'

        transactions = (await db.execute(select(Transaction))).scalars().all()
        assert len(transactions) >= 1


async def test_confirmed_charge_with_empty_id_does_not_extend(monkeypatch):
    """CONFIRMED без Id (или с пустым Id) — недоверенный коллбек: без id
    идемпотентность по ``last_charge_external_id`` не сработает, поэтому
    каждый повтор продлевал бы подписку заново. Такой коллбек не должен
    продлевать подписку и не должен засчитываться как успешное списание.
    """
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    async with _memory_session(monkeypatch) as db:
        end0 = datetime.now(UTC) + timedelta(days=2)
        subscription = Subscription(id=1, user_id=1, status='active', end_date=end0)
        db.add(subscription)
        await db.commit()

        rec = await sub_crud.create_platega_subscription(
            db,
            user_id=1,
            subscription_id=1,
            tariff_id=None,
            interval=3,
            charge_days=30,
            amount_kopeks=19900,
            redirect_url=None,
            platega_subscription_id='ps-empty-id',
            status='ACTIVE',
        )

        svc = Svc()
        for missing_id in (None, ''):
            payload = {'Status': 'CONFIRMED', 'Id': missing_id, 'SubscriptionId': 'ps-empty-id'}
            await svc.process_platega_subscription_callback(db, payload)

        await db.refresh(subscription)
        await db.refresh(rec)
        assert subscription.end_date == end0
        assert rec.charges_success == 0
        assert rec.last_charge_external_id is None

        transactions = (await db.execute(select(Transaction))).scalars().all()
        assert len(transactions) == 0


async def test_confirmed_charge_missing_subscription_does_not_report_false_success(monkeypatch):
    """CONFIRMED, но привязанная Subscription отсутствует (гонка/рассинхрон,
    практически недостижимо благодаря CASCADE FK на subscription_id) — не
    должен инкрементить charges_success, писать аудитную транзакцию или
    помечать charge_id как обработанный: реального продления не произошло,
    репортить успех нельзя.
    """
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    async with _memory_session(monkeypatch) as db:
        # subscription_id=1 указывает на несуществующую Subscription — FK в
        # SQLite не форсится (см. docstring _memory_session).
        rec = await _create_recurring_record(db, platega_subscription_id='ps-9')

        await Svc().process_platega_subscription_callback(
            db, {'Status': 'CONFIRMED', 'Id': 'charge-9', 'SubscriptionId': 'ps-9'}
        )

        await db.refresh(rec)
        assert rec.charges_success == 0
        assert rec.last_charge_external_id is None
        assert rec.status == 'ACTIVE'

        transactions = (await db.execute(select(Transaction))).scalars().all()
        assert len(transactions) == 0


async def test_canceled_charge_marks_past_due_and_counts_failure(monkeypatch):
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    async with _memory_session(monkeypatch) as db:
        rec = await _create_recurring_record(db, platega_subscription_id='ps-2')

        await Svc().process_platega_subscription_callback(
            db, {'Status': 'CANCELED', 'Id': 'charge-fail-1', 'SubscriptionId': 'ps-2'}
        )

        await db.refresh(rec)
        assert rec.status == 'PAST_DUE'
        assert rec.charges_failed == 1


async def test_subscription_past_due_status_transition(monkeypatch):
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    async with _memory_session(monkeypatch) as db:
        rec = await _create_recurring_record(db, platega_subscription_id='ps-3')

        await Svc().process_platega_subscription_callback(
            db, {'Status': 'SUBSCRIPTION_PAST_DUE', 'SubscriptionId': 'ps-3'}
        )

        await db.refresh(rec)
        assert rec.status == 'PAST_DUE'


async def test_subscription_cancelled_status_transition(monkeypatch):
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    async with _memory_session(monkeypatch) as db:
        rec = await _create_recurring_record(db, platega_subscription_id='ps-4')

        await Svc().process_platega_subscription_callback(
            db, {'Status': 'SUBSCRIPTION_CANCELLED', 'SubscriptionId': 'ps-4'}
        )

        await db.refresh(rec)
        assert rec.status == 'CANCELLED'


async def test_subscription_failed_status_transition(monkeypatch):
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    async with _memory_session(monkeypatch) as db:
        rec = await _create_recurring_record(db, platega_subscription_id='ps-5')

        await Svc().process_platega_subscription_callback(
            db, {'Status': 'SUBSCRIPTION_FAILED', 'SubscriptionId': 'ps-5'}
        )

        await db.refresh(rec)
        assert rec.status == 'FAILED'


async def test_subscription_activated_status_transition(monkeypatch):
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    async with _memory_session(monkeypatch) as db:
        rec = await _create_recurring_record(db, platega_subscription_id='ps-6', status='PENDING')

        await Svc().process_platega_subscription_callback(
            db, {'Status': 'SUBSCRIPTION_ACTIVATED', 'SubscriptionId': 'ps-6'}
        )

        await db.refresh(rec)
        assert rec.status == 'ACTIVE'


# --- WS-события sbp_recurring.* для кабинета ---
#
# Кабинет-фронтенд слушает WS-события ``sbp_recurring.{kind}`` — единственный
# канал для email-only пользователей без telegram_id (у которых нет доступа к
# сообщениям бота). Эмиссия должна происходить ДО early-return по
# bot/telegram_id и не должна ломать коллбек при сбое отправки.


async def test_confirmed_charge_emits_ws_event_to_cabinet(monkeypatch):
    """CONFIRMED-коллбек должен отправить WS-событие sbp_recurring.confirmed
    с полной формой сообщения — включая next_charge_at из коллбека Platega.
    """
    from app.cabinet.routes.websocket import cabinet_ws_manager
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot — email-only путь."""

    mock_send = AsyncMock(return_value=None)
    monkeypatch.setattr(cabinet_ws_manager, 'send_to_user', mock_send)

    async with _memory_session(monkeypatch) as db:
        end0 = datetime.now(UTC) + timedelta(days=2)
        subscription = Subscription(id=1, user_id=1, status='active', end_date=end0)
        db.add(subscription)
        await db.commit()

        await sub_crud.create_platega_subscription(
            db,
            user_id=1,
            subscription_id=1,
            tariff_id=None,
            interval=3,
            charge_days=30,
            amount_kopeks=19900,
            redirect_url=None,
            platega_subscription_id='ps-ws-1',
            status='ACTIVE',
        )

        payload = {
            'Status': 'CONFIRMED',
            'Id': 'charge-ws-1',
            'SubscriptionId': 'ps-ws-1',
            'NextChargeAt': '2026-09-01T00:00:00Z',
        }
        await Svc().process_platega_subscription_callback(db, payload)

    mock_send.assert_awaited_once()
    call = mock_send.await_args
    assert call.args[0] == 1  # record.user_id
    message = call.args[1]
    assert message == {
        'type': 'sbp_recurring.confirmed',
        'status': 'ACTIVE',
        'amount_kopeks': 19900,
        'amount_rubles': 199.0,
        'next_charge_at': '2026-09-01T00:00:00+00:00',
        'subscription_id': 1,
    }


async def test_ws_emission_failure_does_not_break_callback(monkeypatch):
    """Сбой отправки WS-события (соединения нет / менеджер упал) — best-effort,
    не должен прерывать обработку коллбека: продление подписки и счётчики
    должны примениться как обычно.
    """
    from app.cabinet.routes.websocket import cabinet_ws_manager
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    monkeypatch.setattr(cabinet_ws_manager, 'send_to_user', AsyncMock(side_effect=RuntimeError('ws down')))

    async with _memory_session(monkeypatch) as db:
        end0 = datetime.now(UTC) + timedelta(days=2)
        subscription = Subscription(id=1, user_id=1, status='active', end_date=end0)
        db.add(subscription)
        await db.commit()

        rec = await sub_crud.create_platega_subscription(
            db,
            user_id=1,
            subscription_id=1,
            tariff_id=None,
            interval=3,
            charge_days=30,
            amount_kopeks=19900,
            redirect_url=None,
            platega_subscription_id='ps-ws-2',
            status='ACTIVE',
        )

        payload = {
            'Status': 'CONFIRMED',
            'Id': 'charge-ws-2',
            'SubscriptionId': 'ps-ws-2',
        }
        await Svc().process_platega_subscription_callback(db, payload)  # не должен бросить

        await db.refresh(subscription)
        await db.refresh(rec)
        assert subscription.end_date >= end0 + timedelta(days=29)
        assert rec.charges_success == 1


async def test_ws_emission_fires_without_bot_attribute(monkeypatch):
    """Эмиссия WS-события должна происходить ДО early-return по
    ``bot``/``telegram_id`` в ``_notify_sbp_recurring`` — иначе email-only
    пользователи кабинета (без Telegram) не получают вообще ничего.
    """
    from app.cabinet.routes.websocket import cabinet_ws_manager
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot — воспроизводит email-only сервис-инстанс."""

    mock_send = AsyncMock(return_value=None)
    monkeypatch.setattr(cabinet_ws_manager, 'send_to_user', mock_send)

    async with _memory_session(monkeypatch) as db:
        rec = await _create_recurring_record(db, platega_subscription_id='ps-ws-3')

        svc = Svc()
        assert not hasattr(svc, 'bot')

        await svc.process_platega_subscription_callback(
            db, {'Status': 'SUBSCRIPTION_PAST_DUE', 'SubscriptionId': 'ps-ws-3'}
        )

        await db.refresh(rec)
        assert rec.status == 'PAST_DUE'

    mock_send.assert_awaited_once()
    message = mock_send.await_args.args[1]
    assert message['type'] == 'sbp_recurring.past_due'


async def test_callback_noops_on_missing_or_unknown_subscription_id(monkeypatch):
    """Отсутствующий/неизвестный SubscriptionId и нераспознанный статус — не
    должны бросать исключение (коллбек обязан вернуть 200 OK на любой валидный
    JSON от Platega), просто ничего не меняют.
    """
    from app.services.payment.platega import PlategaPaymentMixin

    class Svc(PlategaPaymentMixin):
        """Без атрибута bot."""

    async with _memory_session(monkeypatch) as db:
        rec = await _create_recurring_record(db, platega_subscription_id='ps-7')
        svc = Svc()

        # Без SubscriptionId — просто лог + выход.
        await svc.process_platega_subscription_callback(db, {'Status': 'CONFIRMED', 'Id': 'x'})

        # Неизвестный SubscriptionId — запись не найдена.
        await svc.process_platega_subscription_callback(
            db, {'Status': 'CONFIRMED', 'Id': 'x', 'SubscriptionId': 'ps-missing'}
        )

        # Нераспознанный статус для существующей записи.
        await svc.process_platega_subscription_callback(db, {'Status': 'SOMETHING_ELSE', 'SubscriptionId': 'ps-7'})

        await db.refresh(rec)
        assert rec.status == 'ACTIVE'
        assert rec.charges_success == 0
        assert rec.charges_failed == 0
