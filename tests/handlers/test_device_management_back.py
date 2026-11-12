"""Кнопка «Назад» в устройствах ведёт в меню, откуда пришли."""

from __future__ import annotations

from app.handlers.subscription.devices import _devices_back_callback_from_callback
from app.keyboards.inline import get_updated_subscription_settings_keyboard


def test_devices_back_uses_entry_origin() -> None:
    assert _devices_back_callback_from_callback('subscription_manage_devices_menu', None) == 'back_to_menu'
    assert (
        _devices_back_callback_from_callback('subscription_manage_devices:subscription_settings', None)
        == 'subscription_settings'
    )
    assert _devices_back_callback_from_callback('subscription_manage_devices', None) == 'menu_subscription'


def test_settings_device_button_passes_settings_as_origin() -> None:
    keyboard = get_updated_subscription_settings_keyboard('ru', subscription=None)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert 'subscription_manage_devices:subscription_settings' in callbacks
