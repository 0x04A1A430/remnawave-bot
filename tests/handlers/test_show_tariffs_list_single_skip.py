"""Первая покупка: один доступный тариф — сразу к периоду, без экрана списка."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.handlers.subscription import tariff_purchase as m


def _callback(data: str = 'menu_buy'):
    return SimpleNamespace(
        data=data,
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


def _state():
    state = MagicMock()
    state.clear = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    return state


def _user():
    user = MagicMock()
    user.language = 'ru'
    user.promo_group_id = None
    user.balance_kopeks = 100_000
    user.get_primary_promo_group = MagicMock(return_value=None)
    return user


async def test_show_tariffs_list_single_tariff_skips_list_and_proceeds(monkeypatch):
    """Один тариф из get_tariffs_for_user → не рисуем список, сразу _proceed_with_selected_tariff."""
    tariff = SimpleNamespace(id=42, name='Единственный')
    proceed = AsyncMock()

    monkeypatch.setattr(m, 'get_tariffs_for_user', AsyncMock(return_value=[tariff]))
    monkeypatch.setattr(m, '_proceed_with_selected_tariff', proceed)
    monkeypatch.setattr(m, 'format_tariffs_list_text', MagicMock(return_value='LIST'))
    monkeypatch.setattr(m, 'get_tariffs_keyboard', MagicMock(return_value='KB'))

    callback = _callback()
    await m.show_tariffs_list.__wrapped__(callback, _user(), AsyncMock(), _state())

    proceed.assert_awaited_once()
    assert proceed.await_args.args[4] == 42
    callback.message.edit_text.assert_not_awaited()


async def test_show_tariffs_list_multiple_tariffs_shows_list(monkeypatch):
    """Два и больше тарифов → список как раньше, без авто-перехода."""
    from app.config import Settings

    tariffs = [
        SimpleNamespace(id=1, name='A'),
        SimpleNamespace(id=2, name='B'),
    ]
    proceed = AsyncMock()

    monkeypatch.setattr(m, 'get_tariffs_for_user', AsyncMock(return_value=tariffs))
    monkeypatch.setattr(m, '_proceed_with_selected_tariff', proceed)
    monkeypatch.setattr(m, 'format_tariffs_list_text', MagicMock(return_value='LIST TEXT'))
    monkeypatch.setattr(m, 'get_tariffs_keyboard', MagicMock(return_value='LIST KB'))
    monkeypatch.setattr(Settings, 'is_multi_tariff_enabled', lambda self: False)

    callback = _callback()
    await m.show_tariffs_list.__wrapped__(callback, _user(), AsyncMock(), _state())

    proceed.assert_not_awaited()
    callback.message.edit_text.assert_awaited_once()
    assert callback.message.edit_text.await_args.args[0] == 'LIST TEXT'
    assert callback.message.edit_text.await_args.kwargs['reply_markup'] == 'LIST KB'
    callback.answer.assert_awaited_once()


async def test_select_tariff_wrapper_parses_id_and_delegates(monkeypatch):
    """select_tariff остаётся тонкой обёрткой: парсит id и зовёт _proceed_with_selected_tariff."""
    proceed = AsyncMock()
    monkeypatch.setattr(m, '_proceed_with_selected_tariff', proceed)

    callback = _callback('tariff_select:77')
    db_user = _user()
    db = AsyncMock()
    state = _state()

    await m.select_tariff.__wrapped__(callback, db_user, db, state)

    proceed.assert_awaited_once_with(callback, db_user, db, state, 77)
