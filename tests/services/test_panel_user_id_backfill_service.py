"""
Тест бэкфилла panel_user_id из remnawave_uuid (подготовка к RemnaWave 3.0.0).

Сервис заполняет числовой id у строк User/Subscription, у которых есть
remnawave_uuid, но нет panel_user_id, пока панель ещё в v2-режиме. В v3-режиме
(REMNAWAVE_USE_USER_ID) — no-op.
"""

from unittest.mock import AsyncMock, MagicMock

from app.config import settings
from app.services import panel_user_id_backfill_service as backfill


def _patch_targets(monkeypatch, targets):
    monkeypatch.setattr(backfill, '_collect_targets', AsyncMock(return_value=targets))


def _patch_resolver(monkeypatch, mapping):
    monkeypatch.setattr(backfill, '_resolve_panel_user_ids', AsyncMock(return_value=mapping))


def _patch_db(monkeypatch):
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    acm = MagicMock()
    acm.__aenter__ = AsyncMock(return_value=db)
    acm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(backfill, 'AsyncSessionLocal', MagicMock(return_value=acm))
    return db


def _patch_v2(monkeypatch):
    monkeypatch.setattr(settings, 'REMNAWAVE_USE_USER_ID', False)


async def test_v3_mode_is_noop(monkeypatch):
    monkeypatch.setattr(settings, 'REMNAWAVE_USE_USER_ID', True)
    monkeypatch.setattr(backfill, '_collect_targets', AsyncMock(side_effect=AssertionError))

    stats = await backfill.backfill_panel_user_ids()

    assert stats == {'checked': 0, 'backfilled': 0, 'not_found': 0, 'errors': 0}
    backfill._collect_targets.assert_not_awaited()


async def test_no_targets_is_noop(monkeypatch):
    _patch_v2(monkeypatch)
    _patch_targets(monkeypatch, [])
    db = _patch_db(monkeypatch)

    stats = await backfill.backfill_panel_user_ids()

    assert stats['checked'] == 0
    db.commit.assert_not_awaited()


async def test_backfills_user_and_subscription_rows(monkeypatch):
    _patch_v2(monkeypatch)
    _patch_targets(
        monkeypatch,
        [
            (10, 'uuid-user', backfill._USER_ROW),
            (20, 'uuid-sub', backfill._SUBSCRIPTION_ROW),
        ],
    )
    _patch_resolver(monkeypatch, {'uuid-user': 1001, 'uuid-sub': 2002})
    db = _patch_db(monkeypatch)

    stats = await backfill.backfill_panel_user_ids()

    assert stats == {'checked': 2, 'backfilled': 2, 'not_found': 0, 'errors': 0}
    db.commit.assert_awaited_once()
    assert db.execute.await_count == 2
    tables = sorted(str(call.args[0].table) for call in db.execute.await_args_list)
    assert tables == ['subscriptions', 'users']


async def test_dedupe_shared_uuid_resolves_once(monkeypatch):
    _patch_v2(monkeypatch)
    _patch_targets(
        monkeypatch,
        [
            (1, 'shared-uuid', backfill._USER_ROW),
            (2, 'shared-uuid', backfill._SUBSCRIPTION_ROW),
            (3, 'shared-uuid', backfill._SUBSCRIPTION_ROW),
        ],
    )
    _patch_resolver(monkeypatch, {'shared-uuid': 1001})
    db = _patch_db(monkeypatch)

    stats = await backfill.backfill_panel_user_ids()

    backfill._resolve_panel_user_ids.assert_awaited_once_with(['shared-uuid'])
    assert stats == {'checked': 3, 'backfilled': 3, 'not_found': 0, 'errors': 0}
    assert db.execute.await_count == 3


async def test_unresolvable_uuid_is_skipped(monkeypatch):
    _patch_v2(monkeypatch)
    _patch_targets(
        monkeypatch,
        [
            (1, 'ok-uuid', backfill._USER_ROW),
            (2, 'gone-uuid', backfill._SUBSCRIPTION_ROW),
        ],
    )
    _patch_resolver(monkeypatch, {'ok-uuid': 77, 'gone-uuid': None})
    db = _patch_db(monkeypatch)

    stats = await backfill.backfill_panel_user_ids()

    assert stats == {'checked': 2, 'backfilled': 1, 'not_found': 1, 'errors': 0}
    db.commit.assert_awaited_once()
    assert db.execute.await_count == 1


async def test_nan_panel_user_id_is_skipped(monkeypatch):
    _patch_v2(monkeypatch)
    _patch_targets(
        monkeypatch,
        [
            (1, 'ok-uuid', backfill._USER_ROW),
            (2, 'nan-uuid', backfill._SUBSCRIPTION_ROW),
        ],
    )
    _patch_resolver(monkeypatch, {'ok-uuid': 77, 'nan-uuid': float('nan')})
    db = _patch_db(monkeypatch)

    stats = await backfill.backfill_panel_user_ids()

    assert stats == {'checked': 2, 'backfilled': 1, 'not_found': 1, 'errors': 0}
    db.commit.assert_awaited_once()
    assert db.execute.await_count == 1


async def test_collect_targets_filters_nulls(monkeypatch):
    _patch_v2(monkeypatch)

    user_rows = [(1, 'u-a'), (2, 'u-b')]
    sub_rows = [(5, 's-a')]
    db = AsyncMock()
    db.commit = AsyncMock()

    user_result = MagicMock()
    user_result.all.return_value = user_rows
    sub_result = MagicMock()
    sub_result.all.return_value = sub_rows
    db.execute = AsyncMock(side_effect=[user_result, sub_result])

    acm = MagicMock()
    acm.__aenter__ = AsyncMock(return_value=db)
    acm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(backfill, 'AsyncSessionLocal', MagicMock(return_value=acm))

    targets = await backfill._collect_targets()

    assert targets == [
        (1, 'u-a', backfill._USER_ROW),
        (2, 'u-b', backfill._USER_ROW),
        (5, 's-a', backfill._SUBSCRIPTION_ROW),
    ]


async def test_resolve_panel_user_ids_uses_get_user_by_uuid(monkeypatch):
    api = AsyncMock()
    api.get_user_by_uuid = AsyncMock(side_effect=lambda uuid: _panel_user(uuid))
    acm = MagicMock()
    acm.__aenter__ = AsyncMock(return_value=api)
    acm.__aexit__ = AsyncMock(return_value=False)
    service = MagicMock()
    service.get_api_client = MagicMock(return_value=acm)
    monkeypatch.setattr(backfill, 'RemnaWaveService', MagicMock(return_value=service))

    resolved = await backfill._resolve_panel_user_ids(['u-a', 'u-gone'])

    assert resolved == {'u-a': 1001, 'u-gone': None}
    assert api.get_user_by_uuid.await_count == 2
    api.get_user_by_uuid.assert_any_await('u-a')
    api.get_user_by_uuid.assert_any_await('u-gone')


async def test_resolve_panel_user_ids_survives_api_error(monkeypatch):
    api = AsyncMock()
    api.get_user_by_uuid = AsyncMock(side_effect=RuntimeError('panel down'))
    acm = MagicMock()
    acm.__aenter__ = AsyncMock(return_value=api)
    acm.__aexit__ = AsyncMock(return_value=False)
    service = MagicMock()
    service.get_api_client = MagicMock(return_value=acm)
    monkeypatch.setattr(backfill, 'RemnaWaveService', MagicMock(return_value=service))

    resolved = await backfill._resolve_panel_user_ids(['u-a'])

    assert resolved == {'u-a': None}


def _panel_user(uuid):
    u = MagicMock()
    u.id = 1001 if uuid == 'u-a' else None
    return u
