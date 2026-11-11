"""Главное меню: кнопка «Баланс» доступна и без активной подписки."""

from app.keyboards.inline import get_main_menu_keyboard


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_balance_button_shown_for_inactive_subscription():
    kb = get_main_menu_keyboard(
        has_had_paid_subscription=True,
        has_active_subscription=False,
        subscription_is_active=False,
        balance_kopeks=150000,
    )
    assert 'menu_balance' in _callbacks(kb)


def test_balance_button_shown_for_expired_subscription():
    kb = get_main_menu_keyboard(
        has_had_paid_subscription=True,
        has_active_subscription=True,
        subscription_is_active=False,
        balance_kopeks=0,
    )
    assert 'menu_balance' in _callbacks(kb)


def test_balance_button_shown_for_new_user():
    kb = get_main_menu_keyboard(
        has_had_paid_subscription=False,
        has_active_subscription=False,
        subscription_is_active=False,
    )
    assert 'menu_balance' in _callbacks(kb)


def test_balance_button_shown_for_active_subscription():
    kb = get_main_menu_keyboard(
        has_had_paid_subscription=True,
        has_active_subscription=True,
        subscription_is_active=True,
        balance_kopeks=5000,
    )
    assert 'menu_balance' in _callbacks(kb)
