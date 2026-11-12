"""Кнопка «Очистить неактивных» удаляет пользователей из бота и панели."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.handlers.admin import users as admin_users


def _unwrap(handler):
    while hasattr(handler, '__wrapped__'):
        handler = handler.__wrapped__
    return handler


@pytest.mark.asyncio
async def test_cleanup_inactive_forces_panel_delete(monkeypatch) -> None:
    service = SimpleNamespace(cleanup_inactive_users=AsyncMock(return_value=(3, 1)))
    monkeypatch.setattr(admin_users, 'UserService', lambda: service)

    callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
    db_user = SimpleNamespace(id=42, language='ru')

    await _unwrap(admin_users.cleanup_inactive_users)(callback, db_user, AsyncMock())

    service.cleanup_inactive_users.assert_awaited_once()
    args, kwargs = service.cleanup_inactive_users.await_args
    assert args[1] == max(1, settings.INACTIVE_USER_DELETE_MONTHS)
    assert kwargs == {'admin_id': 42, 'force_panel_delete': True}

    final_text = callback.message.edit_text.await_args.args[0]
    assert '3' in final_text
    assert '1' in final_text


@pytest.mark.asyncio
async def test_cleanup_confirmation_is_shown_before_deleting(monkeypatch) -> None:
    user = SimpleNamespace(id=1)
    monkeypatch.setattr(
        admin_users,
        '_load_inactive_users_for_cleanup',
        AsyncMock(return_value=(1, [user], 0)),
    )

    callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
    await _unwrap(admin_users.prepare_cleanup_inactive_users)(
        callback,
        SimpleNamespace(id=42, language='ru'),
        AsyncMock(),
    )

    text = callback.message.edit_text.await_args.args[0]
    keyboard = callback.message.edit_text.await_args.kwargs['reply_markup']
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert buttons[0].callback_data == 'admin_cleanup_inactive_confirm'
    assert 'панели' in text
    assert 'необратимо' in text
