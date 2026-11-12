"""Очистка неактивных: старую подписку удаляем, действующую и панель ACTIVE — нет."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services import user_service as user_service_module
from app.services.user_service import DeleteUserResult, UserService


def _user(user_id: int, *, subscriptions=None):
    return SimpleNamespace(id=user_id, subscriptions=subscriptions or [])


def _sub(*, days_ago: int = 30, status: str = 'expired'):
    return SimpleNamespace(
        id=1,
        end_date=datetime.now(UTC) - timedelta(days=days_ago),
        status=status,
    )


@pytest.fixture(autouse=True)
def expired_window(monkeypatch):
    monkeypatch.setattr(settings, 'INACTIVE_USER_SUBSCRIPTION_EXPIRE_DAYS', 14)


@pytest.mark.asyncio
async def test_cleanup_deletes_old_expired_but_skips_current_and_panel(monkeypatch) -> None:
    users = [
        _user(1, subscriptions=[_sub(days_ago=30)]),  # старую подписку удаляем
        _user(2, subscriptions=[_sub(days_ago=1)]),  # истекла недавно — нельзя
        _user(3, subscriptions=[_sub(days_ago=30, status='active')]),  # действующая — нельзя
        _user(4),  # без подписки — можно
        _user(5, subscriptions=[_sub(days_ago=30)]),  # панель ACTIVE — нельзя
        _user(6, subscriptions=[_sub(days_ago=30)]),  # панель недоступна — нельзя
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
        AsyncMock(side_effect=[False, False, True, None]),
    )
    delete = AsyncMock(return_value=DeleteUserResult(bot_deleted=True))
    monkeypatch.setattr(service, 'delete_user_account', delete)

    deleted, skipped = await service.cleanup_inactive_users(AsyncMock(), months=1)

    assert (deleted, skipped) == (2, 4)
    assert [call.args[1] for call in delete.await_args_list] == [1, 4]


@pytest.mark.asyncio
async def test_cleanup_never_deletes_current_subscription_before_panel_check(monkeypatch) -> None:
    monkeypatch.setattr(
        user_service_module,
        'get_inactive_users',
        AsyncMock(return_value=[_user(7, subscriptions=[_sub(days_ago=1)])]),
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
