"""Рекуррентные подписки Lava: чистая логика, оформление, отмена, списания."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.database.models import (
    LavaSubscription,
    PromoGroup,
    Subscription,
    SubscriptionStatus,
    Tariff,
    Transaction,
    User,
    UserPromoGroup,
    UserStatus,
    tariff_promo_groups,
)
from app.services import lava_recurrent as lr
from tests.fixtures.sqlite_memory import memory_session


TABLES = (
    User.__table__,
    Subscription.__table__,
    Tariff.__table__,
    PromoGroup.__table__,
    UserPromoGroup.__table__,
    tariff_promo_groups,
    LavaSubscription.__table__,
    Transaction.__table__,
)

PRODUCT_ID = '6be21df9-0bcd-44ac-9c2c-3be7bc94decc'


# ---------------------------------------------------------------- pure logic


def test_order_id_roundtrip_marks_recurrent_charges():
    """Вебхук отличает списание по подписке от инвойса пополнения по префиксу."""
    order_id = lr.build_recurrent_order_id(42, 'deadbeef')
    assert lr.is_recurrent_order_id(order_id)
    # Обычный инвойс пополнения (lava{tg}_{uuid}) рекуррентным не считается
    assert not lr.is_recurrent_order_id('lava123456_abcdef')
    assert not lr.is_recurrent_order_id(None)


@pytest.mark.parametrize(
    ('product', 'expected'),
    [
        ({'periodDays': 90}, 90),
        ({'periodDays': 0, 'period': 'one_month'}, 30),
        ({'period': 'three_months'}, 90),
        ({'period': 'year'}, 365),
        # Внутрисуточные периоды схлопываются в 1 день: продлевать на 0 нельзя
        ({'period': 'one_hour'}, 1),
        ({'period': 'ten_minute'}, 1),
        (None, 30),
        ({'periodDays': 'мусор', 'period': 'six_months'}, 180),
    ],
)
def test_resolve_product_charge_days(product, expected):
    assert lr.resolve_product_charge_days(product) == expected


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('ACTIVE', 'active'),
        ('subscribed', 'active'),
        ('Canceled', 'cancelled'),
        ('unsubscribed', 'cancelled'),
        ('past-due', 'past_due'),
        ('failed', 'failed'),
        (None, None),
        ('', None),
        # Неизвестное значение возвращается как есть — reconciler на него не реагирует
        ('какой_то_новый', 'какой_то_новый'),
    ],
)
def test_normalize_remote_status(raw, expected):
    assert lr.normalize_remote_status(raw) == expected


def test_reconcile_transport_failure_does_not_bury_pending():
    """Транспортный сбой (remote_missing=False) откладывает решение."""
    assert lr.lava_reconcile_decision('PENDING', None, 999, remote_missing=False) is None
    # Провайдер достоверно не знает подписку и запись давно висит → FAILED
    assert lr.lava_reconcile_decision('PENDING', None, 31, remote_missing=True) == 'FAILED'
    # Свежий PENDING не хороним
    assert lr.lava_reconcile_decision('PENDING', None, 5, remote_missing=True) is None


def test_reconcile_status_rules():
    assert lr.lava_reconcile_decision('PENDING', 'active', 1) == 'ACTIVE'
    assert lr.lava_reconcile_decision('ACTIVE', 'cancelled', 1) == 'CANCELLED'
    assert lr.lava_reconcile_decision('ACTIVE', 'past_due', 1) == 'PAST_DUE'
    # Отменённую запись не воскрешаем и не переводим в FAILED
    assert lr.lava_reconcile_decision('CANCELLED', 'failed', 1) is None
    # Совпадающий статус — без изменений
    assert lr.lava_reconcile_decision('ACTIVE', 'active', 1) is None


# ------------------------------------------------------------------ фикстуры


async def _seed(db, *, autopay_enabled: bool = True, product_id: str | None = PRODUCT_ID):
    now = datetime.now(UTC)
    user = User(
        telegram_id=555,
        username='user555',
        first_name='User',
        status=UserStatus.ACTIVE.value,
        language='ru',
        balance_kopeks=0,
    )
    db.add(user)
    await db.commit()

    tariff = Tariff(
        name='Базовый',
        is_active=True,
        device_limit=1,
        traffic_limit_gb=0,
        period_prices={'30': 10000},
        lava_product_id=product_id,
    )
    db.add(tariff)
    await db.commit()

    subscription = Subscription(
        user_id=user.id,
        tariff_id=tariff.id,
        status=SubscriptionStatus.ACTIVE.value,
        is_trial=False,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=10),
        autopay_enabled=autopay_enabled,
        remnawave_short_id='shortlava',
    )
    db.add(subscription)
    await db.commit()
    return user, tariff, subscription


def _agent(monkeypatch, *, subscribe_response=None, unsubscribe=None):
    """Агент рекуррента с замоканным сетевым слоем Lava."""
    import app.services.payment.lava as lava_module

    service = SimpleNamespace(
        list_recurrent_products=AsyncMock(return_value=[{'id': PRODUCT_ID, 'periodDays': 30, 'price': 100.0}]),
        create_recurrent_consumer=AsyncMock(return_value={'data': {}}),
        subscribe_recurrent=AsyncMock(
            return_value=subscribe_response
            or {'data': {'subscriptionId': 'lava-sub-1', 'url': 'https://pay.lava/x', 'amount': 100.0}}
        ),
        unsubscribe_recurrent=unsubscribe or AsyncMock(return_value={'data': {'unsubscribed': True}}),
        get_recurrent_subscription_status=AsyncMock(return_value={'data': {'status': 'active'}}),
    )
    monkeypatch.setattr(lava_module, 'lava_service', service)
    return lava_module._LavaRecurrentAgent(), service


# ------------------------------------------------------------------ оформление


async def test_enable_creates_binding_and_disables_balance_autopay(monkeypatch):
    """Рекуррент провайдера и balance-autopay взаимоисключающи."""
    async with memory_session(monkeypatch, TABLES) as db:
        user, tariff, subscription = await _seed(db, autopay_enabled=True)
        agent, service = _agent(monkeypatch)

        result = await agent.create_lava_recurrent_subscription(
            db, user_id=user.id, subscription=subscription, tariff=tariff
        )

        assert result['lava_subscription_id'] == 'lava-sub-1'
        assert result['redirect_url'] == 'https://pay.lava/x'
        assert result['status'] == 'PENDING'
        assert subscription.autopay_enabled is False

        order_id = service.subscribe_recurrent.await_args.kwargs['order_id']
        assert lr.is_recurrent_order_id(order_id)

        from app.database.crud import lava_subscription as sub_crud

        record = await sub_crud.get_active_lava_subscription_by_subscription(db, subscription.id)
        assert record is not None
        assert record.charge_days == 30
        assert record.amount_kopeks == 10000


async def test_enable_is_idempotent_and_restores_mutual_exclusion(monkeypatch):
    async with memory_session(monkeypatch, TABLES) as db:
        user, tariff, subscription = await _seed(db)
        agent, service = _agent(monkeypatch)

        first = await agent.create_lava_recurrent_subscription(
            db, user_id=user.id, subscription=subscription, tariff=tariff
        )
        # Balance-autopay успели включить обратно — повтор обязан снова его снять
        subscription.autopay_enabled = True
        await db.commit()

        second = await agent.create_lava_recurrent_subscription(
            db, user_id=user.id, subscription=subscription, tariff=tariff
        )

        assert second['local_id'] == first['local_id']
        assert subscription.autopay_enabled is False
        # Повторного обращения к Lava не было
        assert service.subscribe_recurrent.await_count == 1


async def test_enable_without_product_id_is_rejected(monkeypatch):
    """У тарифа нет продукта Lava — понятная ошибка, а не молчаливый успех."""
    async with memory_session(monkeypatch, TABLES) as db:
        user, tariff, subscription = await _seed(db, product_id=None)
        agent, service = _agent(monkeypatch)

        with pytest.raises(ValueError, match='продукт Lava'):
            await agent.create_lava_recurrent_subscription(
                db, user_id=user.id, subscription=subscription, tariff=tariff
            )
        service.subscribe_recurrent.assert_not_awaited()


async def test_enable_rejects_zero_price_product(monkeypatch):
    """Продукт без цены дал бы пустые регулярные «списания»."""
    async with memory_session(monkeypatch, TABLES) as db:
        user, tariff, subscription = await _seed(db)
        agent, _ = _agent(
            monkeypatch,
            subscribe_response={'data': {'subscriptionId': 'lava-sub-1', 'url': 'u', 'amount': 0}},
        )
        # и продукт тоже без цены
        import app.services.payment.lava as lava_module

        lava_module.lava_service.list_recurrent_products = AsyncMock(
            return_value=[{'id': PRODUCT_ID, 'periodDays': 30, 'price': 0}]
        )

        with pytest.raises(ValueError, match='цены'):
            await agent.create_lava_recurrent_subscription(
                db, user_id=user.id, subscription=subscription, tariff=tariff
            )


# --------------------------------------------------------------------- отмена


async def test_cancel_marks_local_even_when_provider_fails(monkeypatch):
    """Недоступность Lava не должна блокировать отмену навсегда."""
    async with memory_session(monkeypatch, TABLES) as db:
        user, tariff, subscription = await _seed(db)
        agent, _ = _agent(monkeypatch, unsubscribe=AsyncMock(side_effect=RuntimeError('network')))

        created = await agent.create_lava_recurrent_subscription(
            db, user_id=user.id, subscription=subscription, tariff=tariff
        )
        ok = await agent.cancel_lava_recurrent_subscription(db, local_id=created['local_id'])

        assert ok is True
        from app.database.crud import lava_subscription as sub_crud

        record = await sub_crud.get_lava_subscription_by_id(db, created['local_id'])
        assert record.status == 'CANCELLED'
        # Повторная отмена идемпотентна
        assert await agent.cancel_lava_recurrent_subscription(db, local_id=created['local_id']) is True


# ------------------------------------------------------------------- списания


def _charge(order_id: str, invoice_id: str, status: str = 'success') -> dict:
    return {'order_id': order_id, 'invoice_id': invoice_id, 'status': status}


async def test_charge_extends_subscription_without_touching_balance(monkeypatch):
    async with memory_session(monkeypatch, TABLES) as db:
        user, tariff, subscription = await _seed(db)
        agent, service = _agent(monkeypatch)
        monkeypatch.setattr(settings, 'RESET_TRAFFIC_ON_PAYMENT', False)

        created = await agent.create_lava_recurrent_subscription(
            db, user_id=user.id, subscription=subscription, tariff=tariff
        )
        order_id = service.subscribe_recurrent.await_args.kwargs['order_id']
        end_before = subscription.end_date
        balance_before = user.balance_kopeks

        assert await agent.process_lava_subscription_callback(db, _charge(order_id, 'inv-1')) is True

        await db.refresh(subscription)
        await db.refresh(user)
        assert subscription.end_date > end_before
        # Продление — не начисление на баланс
        assert user.balance_kopeks == balance_before

        from app.database.crud import lava_subscription as sub_crud

        record = await sub_crud.get_lava_subscription_by_id(db, created['local_id'])
        assert record.status == 'ACTIVE'
        assert record.charges_success == 1
        assert record.last_charge_external_id == 'inv-1'


async def test_repeated_charge_callback_does_not_extend_twice(monkeypatch):
    """Ретрай вебхука Lava (до 5 раз) не должен продлевать дважды."""
    async with memory_session(monkeypatch, TABLES) as db:
        user, tariff, subscription = await _seed(db)
        agent, service = _agent(monkeypatch)
        monkeypatch.setattr(settings, 'RESET_TRAFFIC_ON_PAYMENT', False)

        await agent.create_lava_recurrent_subscription(db, user_id=user.id, subscription=subscription, tariff=tariff)
        order_id = service.subscribe_recurrent.await_args.kwargs['order_id']

        await agent.process_lava_subscription_callback(db, _charge(order_id, 'inv-1'))
        await db.refresh(subscription)
        end_after_first = subscription.end_date

        await agent.process_lava_subscription_callback(db, _charge(order_id, 'inv-1'))
        await db.refresh(subscription)

        assert subscription.end_date == end_after_first


async def test_late_redelivery_of_older_charge_is_ignored(monkeypatch):
    """last_charge_external_id хранит только последний id — сверяемся с транзакциями."""
    async with memory_session(monkeypatch, TABLES) as db:
        user, tariff, subscription = await _seed(db)
        agent, service = _agent(monkeypatch)
        monkeypatch.setattr(settings, 'RESET_TRAFFIC_ON_PAYMENT', False)

        await agent.create_lava_recurrent_subscription(db, user_id=user.id, subscription=subscription, tariff=tariff)
        order_id = service.subscribe_recurrent.await_args.kwargs['order_id']

        await agent.process_lava_subscription_callback(db, _charge(order_id, 'inv-1'))
        await agent.process_lava_subscription_callback(db, _charge(order_id, 'inv-2'))
        await db.refresh(subscription)
        end_after_two = subscription.end_date

        # Поздний ределивери ПЕРВОГО списания
        await agent.process_lava_subscription_callback(db, _charge(order_id, 'inv-1'))
        await db.refresh(subscription)

        assert subscription.end_date == end_after_two


async def test_success_without_invoice_id_does_not_extend(monkeypatch):
    """Без invoice_id идемпотентность не работает — продлевать нельзя."""
    async with memory_session(monkeypatch, TABLES) as db:
        user, tariff, subscription = await _seed(db)
        agent, service = _agent(monkeypatch)

        await agent.create_lava_recurrent_subscription(db, user_id=user.id, subscription=subscription, tariff=tariff)
        order_id = service.subscribe_recurrent.await_args.kwargs['order_id']
        end_before = subscription.end_date

        ok = await agent.process_lava_subscription_callback(
            db, {'order_id': order_id, 'status': 'success', 'invoice_id': None}
        )

        await db.refresh(subscription)
        assert ok is False
        assert subscription.end_date == end_before


async def test_charge_on_locally_cancelled_record_extends_but_keeps_cancelled(monkeypatch):
    """Деньги взяты — продлеваем честно, но отмену пользователя не стираем."""
    async with memory_session(monkeypatch, TABLES) as db:
        user, tariff, subscription = await _seed(db)
        unsubscribe = AsyncMock(return_value={'data': {'unsubscribed': True}})
        agent, service = _agent(monkeypatch, unsubscribe=unsubscribe)
        monkeypatch.setattr(settings, 'RESET_TRAFFIC_ON_PAYMENT', False)

        created = await agent.create_lava_recurrent_subscription(
            db, user_id=user.id, subscription=subscription, tariff=tariff
        )
        order_id = service.subscribe_recurrent.await_args.kwargs['order_id']
        await agent.cancel_lava_recurrent_subscription(db, local_id=created['local_id'])
        end_before = subscription.end_date
        unsubscribe.reset_mock()

        await agent.process_lava_subscription_callback(db, _charge(order_id, 'inv-late'))

        await db.refresh(subscription)
        from app.database.crud import lava_subscription as sub_crud

        record = await sub_crud.get_lava_subscription_by_id(db, created['local_id'])
        assert subscription.end_date > end_before  # продлили
        assert record.status == 'CANCELLED'  # но не воскресили
        unsubscribe.assert_awaited()  # и повторили удалённую отмену


async def test_failed_charge_moves_record_to_past_due(monkeypatch):
    async with memory_session(monkeypatch, TABLES) as db:
        user, tariff, subscription = await _seed(db)
        agent, service = _agent(monkeypatch)

        created = await agent.create_lava_recurrent_subscription(
            db, user_id=user.id, subscription=subscription, tariff=tariff
        )
        order_id = service.subscribe_recurrent.await_args.kwargs['order_id']

        assert await agent.process_lava_subscription_callback(db, _charge(order_id, 'inv-x', 'failed')) is True

        from app.database.crud import lava_subscription as sub_crud

        record = await sub_crud.get_lava_subscription_by_id(db, created['local_id'])
        assert record.status == 'PAST_DUE'
        assert record.charges_failed == 1


async def test_callback_for_unknown_order_is_reported(monkeypatch):
    async with memory_session(monkeypatch, TABLES) as db:
        await _seed(db)
        agent, _ = _agent(monkeypatch)

        assert await agent.process_lava_subscription_callback(db, _charge('lavarec999_nope', 'inv-1')) is False
