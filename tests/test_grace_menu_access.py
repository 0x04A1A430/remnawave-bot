"""Во время grace кнопка меню подписки остаётся доступной."""

from __future__ import annotations

from types import SimpleNamespace

from app.handlers.start import _calculate_subscription_flags
from app.keyboards.inline import get_main_menu_keyboard


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_grace_subscription_keeps_menu_button() -> None:
    subscription = SimpleNamespace(grace_session_open=True)
    keyboard = get_main_menu_keyboard(
        has_had_paid_subscription=True,
        has_active_subscription=False,
        subscription_is_active=False,
        subscription=subscription,
    )

    assert 'menu_subscription' in _callbacks(keyboard)


def test_grace_subscription_counts_as_active_for_menu_flags() -> None:
    subscription = SimpleNamespace(
        actual_status='expired',
        is_active=False,
        grace_session_open=True,
    )

    assert _calculate_subscription_flags(subscription) == (True, True)
