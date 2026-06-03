from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.database.crud.subscription import create_trial_subscription


async def test_create_trial_subscription_uses_all_available_squads_by_default(monkeypatch):
    db = Mock()
    db.add = Mock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    monkeypatch.setattr('app.database.crud.subscription.get_subscription_by_user_id', AsyncMock(return_value=None))
    monkeypatch.setattr('app.database.crud.subscription.generate_unique_short_id', AsyncMock(return_value='abc123'))
    monkeypatch.setattr(
        'app.database.crud.server_squad.get_available_server_squads',
        AsyncMock(
            return_value=[
                SimpleNamespace(squad_uuid='fi-uuid'),
                SimpleNamespace(squad_uuid='ru-uuid'),
            ]
        ),
    )
    get_server_ids_mock = AsyncMock(return_value=[11, 12])
    add_user_to_servers_mock = AsyncMock()
    monkeypatch.setattr('app.database.crud.server_squad.get_server_ids_by_uuids', get_server_ids_mock)
    monkeypatch.setattr('app.database.crud.server_squad.add_user_to_servers', add_user_to_servers_mock)

    subscription = await create_trial_subscription(
        db,
        user_id=1,
        duration_days=14,
        traffic_limit_gb=100,
        device_limit=5,
    )

    assert subscription.connected_squads == ['fi-uuid', 'ru-uuid']
    db.add.assert_called_once_with(subscription)
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(subscription)
    get_server_ids_mock.assert_awaited_once_with(db, ['fi-uuid', 'ru-uuid'])
    add_user_to_servers_mock.assert_awaited_once_with(db, [11, 12])


async def test_extend_subscription_convert_trial_false_keeps_trial(monkeypatch):
    """Bug #629889 guardrail: extend_subscription(tariff_id=..., convert_trial=False)
    must NOT clear is_trial. A free relabel keeps the sub a trial so it stays gated
    out of try_auto_extend_expired_after_topup and never self-renews to a full period.
    """
    from datetime import UTC, datetime, timedelta

    from app.database.crud.subscription import extend_subscription

    monkeypatch.setattr('app.database.crud.subscription._lock_subscription_row', AsyncMock())
    monkeypatch.setattr('app.database.crud.subscription._housekeep_expired_purchases', AsyncMock())
    monkeypatch.setattr('app.database.crud.subscription.clear_notifications', AsyncMock())
    monkeypatch.setattr(
        'app.database.crud.tariff.get_tariff_by_id', AsyncMock(return_value=SimpleNamespace(is_daily=False))
    )
    deactivate_mock = AsyncMock(return_value=[])
    monkeypatch.setattr('app.database.crud.subscription.deactivate_user_trial_subscriptions', deactivate_mock)

    db = AsyncMock()
    db.flush = AsyncMock()

    now = datetime.now(UTC)
    sub = SimpleNamespace(
        id=1,
        user_id=7,
        status='trial',
        is_trial=True,
        start_date=now,
        end_date=now + timedelta(days=1),
        tariff_id=1,
        traffic_limit_gb=10,
        traffic_used_gb=0.0,
        device_limit=1,
        connected_squads=[],
        purchased_traffic_gb=0,
        updated_at=now,
    )

    result = await extend_subscription(db, sub, 14, tariff_id=2, convert_trial=False, commit=False)

    assert result.is_trial is True  # NOT converted on a free relabel
    assert result.tariff_id == 2  # the relabel still applied
    deactivate_mock.assert_not_awaited()  # other trials not killed


async def test_extend_subscription_default_converts_trial_on_purchase(monkeypatch):
    """Default convert_trial=True (a real tariff purchase) still clears is_trial."""
    from datetime import UTC, datetime, timedelta

    from app.database.crud.subscription import extend_subscription

    monkeypatch.setattr('app.database.crud.subscription._lock_subscription_row', AsyncMock())
    monkeypatch.setattr('app.database.crud.subscription._housekeep_expired_purchases', AsyncMock())
    monkeypatch.setattr('app.database.crud.subscription.clear_notifications', AsyncMock())
    monkeypatch.setattr(
        'app.database.crud.tariff.get_tariff_by_id', AsyncMock(return_value=SimpleNamespace(is_daily=False))
    )
    monkeypatch.setattr(
        'app.database.crud.subscription.deactivate_user_trial_subscriptions', AsyncMock(return_value=[])
    )

    db = AsyncMock()
    db.flush = AsyncMock()

    now = datetime.now(UTC)
    sub = SimpleNamespace(
        id=1,
        user_id=7,
        status='trial',
        is_trial=True,
        start_date=now,
        end_date=now + timedelta(days=1),
        tariff_id=1,
        traffic_limit_gb=10,
        traffic_used_gb=0.0,
        device_limit=1,
        connected_squads=[],
        purchased_traffic_gb=0,
        updated_at=now,
    )

    result = await extend_subscription(db, sub, 14, tariff_id=2, commit=False)

    assert result.is_trial is False  # genuine purchase converts the trial
