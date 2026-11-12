"""Очистка неактивных не трогает подписки и активных в панели пользователей."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import user_service as user_service_module
from app.services.user_service import DeleteUserResult, UserService


def _user(user_id: int, *, subscriptions=None):
    return SimpleNamespace(id=user_id, subscriptions=subscriptions or [])


@pytest.mark.asyncio
async def test_cleanup_skips_subscriptions_panel_active_and_unknown(monkeypatch) -> None:
    users = [
        _user(1, subscriptions=[SimpleNamespace(id=10)]),
        _user(2),
        _user(3),
        _user(4),
    ]
    monkeypatch.setattr(
        user_service_module,
        'get_inactive_users',
        AsyncMock(return_value=users),
    )

    service = UserService()
    monkeypatch.setattr(
        service,
        '_panel_user_is_active',
        AsyncMock(side_effect=[True, None, False]),
    )
    delete = AsyncMock(return_value=DeleteUserResult(bot_deleted=True))
    monkeypatch.setattr(service, 'delete_user_account', delete)

    deleted, skipped = await service.cleanup_inactive_users(AsyncMock(), months=1)

    assert (deleted, skipped) == (1, 3)
    delete.assert_awaited_once()
    assert delete.await_args.args[1] == 4


@pytest.mark.asyncio
async def test_cleanup_never_deletes_user_with_any_subscription(monkeypatch) -> None:
    monkeypatch.setattr(
        user_service_module,
        'get_inactive_users',
        AsyncMock(return_value=[_user(7, subscriptions=[SimpleNamespace(id=70)])]),
    )

    service = UserService()
    panel_check = AsyncMock()
    monkeypatch.setattr(service, '_panel_user_is_active', panel_check)
    delete = AsyncMock()
    monkeypatch.setattr(service, 'delete_user_account', delete)

    deleted, skipped = await service.cleanup_inactive_users(AsyncMock(), months=1)

    assert (deleted, skipped) == (0, 1)
    panel_check.assert_not_awaited()
    delete.assert_not_awaited()
