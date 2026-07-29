"""Ownership boundaries for subscription-scoped admin user-detail routes.

The regression these tests guard against is treating a subscription id as an
authorization token.  Every multi-tariff operation must resolve the row with
both the requested subscription id and the route's user id before doing any
read or mutation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.cabinet.routes import admin_users
from app.cabinet.schemas.users import UpdateSubscriptionRequest
from app.config import Settings


OWNER_ID = 10
OWNED_ID = 101
FOREIGN_ID = 202
MISSING_ID = 303


def _subscription(subscription_id: int, user_id: int, *, uuid: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=subscription_id,
        user_id=user_id,
        remnawave_uuid=uuid,
        is_active=True,
        status='active',
        end_date=datetime.now(UTC) + timedelta(days=10),
        traffic_limit_gb=100,
        traffic_used_gb=1.5,
        device_limit=2,
    )


def _user(*subscriptions: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        id=OWNER_ID,
        telegram_id=1000,
        email=None,
        remnawave_uuid=None,
        subscriptions=list(subscriptions),
    )


@pytest.fixture
def owned_subscription() -> SimpleNamespace:
    return _subscription(OWNED_ID, OWNER_ID, uuid='owned-panel-uuid')


@pytest.fixture
def foreign_subscription() -> SimpleNamespace:
    return _subscription(FOREIGN_ID, 20, uuid='foreign-panel-uuid')


@pytest.fixture
def ownership_boundary(monkeypatch, owned_subscription, foreign_subscription):
    """Make the authoritative lookup return only the subscription owned by OWNER_ID."""
    monkeypatch.setattr(Settings, 'is_multi_tariff_enabled', lambda self: True)
    user = _user(owned_subscription, foreign_subscription)
    monkeypatch.setattr(admin_users, 'get_user_by_id', AsyncMock(return_value=user))

    async def get_owned_subscription(_db, subscription_id, user_id):
        if (subscription_id, user_id) == (OWNED_ID, OWNER_ID):
            return owned_subscription
        raise HTTPException(status_code=404, detail='Subscription not found for this user')

    monkeypatch.setattr(
        admin_users,
        '_get_owned_subscription_or_404',
        get_owned_subscription,
    )
    monkeypatch.setattr('app.database.crud.user.get_user_by_id', AsyncMock(return_value=user))
    return user


class _Api:
    async def get_user_by_uuid(self, uuid):
        return SimpleNamespace(
            uuid=uuid,
            trojan_password='owned-secret',
            vless_uuid='owned-vless',
            ss_password='owned-ss',
            subscription_url='https://owned.example/sub',
            happ_link=None,
            used_traffic_bytes=1,
            lifetime_used_traffic_bytes=2,
            traffic_limit_bytes=3,
            first_connected_at=None,
            online_at=None,
            user_traffic=None,
        )

    async def get_user_devices_all(self, _uuid):
        return {'devices': [{'hwid': 'owned-hwid', 'platform': 'ios'}], 'total': 1}

    async def get_subscription_request_history(self, _uuid, *, offset, limit):
        return {'total': 1, 'records': [{'ip': '192.0.2.1'}]}

    async def remove_device(self, _uuid, _hwid):
        return True


class _Service:
    is_configured = True

    def get_api_client(self):
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=_Api())
        context.__aexit__ = AsyncMock(return_value=None)
        return context


@pytest.fixture
def panel_service(monkeypatch):
    monkeypatch.setattr('app.services.remnawave_service.RemnaWaveService', _Service)
    monkeypatch.setattr(admin_users, 'get_aliases_for_user', AsyncMock(return_value={}))


async def test_authoritative_subscription_lookup_constrains_id_and_user_id(owned_subscription):
    """Would fail if a route reverted to an id-only subscription lookup."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = owned_subscription
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    assert await admin_users._get_owned_subscription_or_404(db, OWNED_ID, OWNER_ID) is owned_subscription

    query = str(db.execute.await_args.args[0])
    assert 'subscriptions.id' in query
    assert 'subscriptions.user_id' in query


@pytest.mark.parametrize(
    ('route_name', 'kwargs', 'foreign_marker'),
    [
        ('get_user_panel_info', {}, 'owned-secret'),
        ('get_user_devices', {}, 'owned-hwid'),
        ('get_subscription_request_history', {}, '192.0.2.1'),
        ('delete_user_device', {'hwid': 'owned-hwid'}, 'Device deleted'),
    ],
    ids=['panel-info', 'devices', 'request-history', 'delete-device'],
)
async def test_subscription_reads_and_device_delete_are_ownership_isolated(
    ownership_boundary, panel_service, route_name, kwargs, foreign_marker
):
    """A foreign id must not disclose panel data or invoke the device mutation."""
    route = getattr(admin_users, route_name)
    db = AsyncMock()
    admin = SimpleNamespace(id=1)

    owned_result = await route(OWNER_ID, admin=admin, db=db, subscription_id=OWNED_ID, **kwargs)
    assert foreign_marker in str(owned_result)

    with pytest.raises(HTTPException) as foreign:
        await route(OWNER_ID, admin=admin, db=db, subscription_id=FOREIGN_ID, **kwargs)
    assert foreign.value.status_code == 404

    with pytest.raises(HTTPException) as missing:
        await route(OWNER_ID, admin=admin, db=db, subscription_id=MISSING_ID, **kwargs)
    assert missing.value.status_code == 404


@pytest.mark.parametrize(
    ('action', 'request_kwargs', 'state_field'),
    [
        ('set_end_date', {'end_date': datetime.now(UTC) + timedelta(days=30)}, 'end_date'),
        ('set_traffic', {'traffic_limit_gb': 999}, 'traffic_limit_gb'),
        ('set_device_limit', {'device_limit': 99}, 'device_limit'),
        ('reset', {}, 'status'),
    ],
    ids=['set-expiry', 'set-traffic', 'set-device-limit', 'reset-one-subscription'],
)
async def test_subscription_actions_accept_an_owned_subscription(
    monkeypatch, ownership_boundary, owned_subscription, action, request_kwargs, state_field
):
    """An ownership guard must not reject the requested user's own subscription."""
    sync = AsyncMock()
    reset = AsyncMock(return_value={'panel_disabled': True})
    monkeypatch.setattr(admin_users, '_sync_subscription_to_panel', sync)
    monkeypatch.setattr(admin_users, '_build_subscription_info_async', AsyncMock(return_value=None))
    monkeypatch.setattr('app.services.subscription_service.reset_subscription_with_panel', reset)
    db = AsyncMock()
    request = UpdateSubscriptionRequest(action=action, subscription_id=OWNED_ID, **request_kwargs)

    result = await admin_users.update_user_subscription(OWNER_ID, request, admin=SimpleNamespace(id=1), db=db)

    assert result.success is True
    if action == 'reset':
        reset.assert_awaited_once_with(db, ownership_boundary, owned_subscription)
    else:
        sync.assert_awaited_once_with(db, ownership_boundary, owned_subscription)
        assert db.commit.await_count == 1


@pytest.mark.parametrize(
    ('action', 'request_kwargs', 'state_field'),
    [
        ('set_end_date', {'end_date': datetime.now(UTC) + timedelta(days=30)}, 'end_date'),
        ('set_traffic', {'traffic_limit_gb': 999}, 'traffic_limit_gb'),
        ('set_device_limit', {'device_limit': 99}, 'device_limit'),
        ('reset', {}, 'status'),
    ],
    ids=['set-expiry', 'set-traffic', 'set-device-limit', 'reset-one-subscription'],
)
async def test_subscription_actions_reject_foreign_or_absent_ids_before_mutation(
    monkeypatch, ownership_boundary, foreign_subscription, action, request_kwargs, state_field
):
    """Would fail if action selection trusted an eagerly loaded subscription list."""
    sync = AsyncMock()
    reset = AsyncMock(return_value={'panel_disabled': True})
    monkeypatch.setattr(admin_users, '_sync_subscription_to_panel', sync)
    monkeypatch.setattr(admin_users, '_build_subscription_info_async', AsyncMock(return_value=None))
    monkeypatch.setattr('app.services.subscription_service.reset_subscription_with_panel', reset)
    db = AsyncMock()
    before = getattr(foreign_subscription, state_field)
    request = UpdateSubscriptionRequest(action=action, subscription_id=FOREIGN_ID, **request_kwargs)

    with pytest.raises(HTTPException) as foreign:
        await admin_users.update_user_subscription(OWNER_ID, request, admin=SimpleNamespace(id=1), db=db)
    assert foreign.value.status_code == 404
    assert getattr(foreign_subscription, state_field) == before
    sync.assert_not_awaited()
    reset.assert_not_awaited()
    db.commit.assert_not_awaited()

    missing_request = UpdateSubscriptionRequest(action=action, subscription_id=MISSING_ID, **request_kwargs)
    with pytest.raises(HTTPException) as missing:
        await admin_users.update_user_subscription(OWNER_ID, missing_request, admin=SimpleNamespace(id=1), db=db)
    assert missing.value.status_code == 404
    assert getattr(foreign_subscription, state_field) == before
