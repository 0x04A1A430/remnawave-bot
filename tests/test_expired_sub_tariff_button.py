"""Истёкшая подписка: действия перенесены в «Настройки», смена тарифа работает."""

from __future__ import annotations

from types import SimpleNamespace

import app.keyboards.inline as kb


def _callbacks(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]


def _fake_sub(actual_status: str, status: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        actual_status=actual_status,
        status=status,
        tariff_id=7,
        tariff=None,
        traffic_limit_gb=0,
        end_date=None,
        is_daily_paused=False,
    )


def _patch_tariffs_mode(monkeypatch):
    from app.config import Settings

    monkeypatch.setattr(Settings, 'is_tariffs_mode', lambda self: True)
    monkeypatch.setattr(Settings, 'is_multi_tariff_enabled', lambda self: True)
    monkeypatch.setattr(kb, 'get_display_subscription_link', lambda sub: None)


def _settings_callbacks(sub) -> list[str]:
    markup = kb.get_updated_subscription_settings_keyboard(
        'ru',
        show_countries_management=True,
        tariff=None,
        subscription=sub,
        is_legacy_subscription=False,
    )
    return _callbacks(markup)


def test_expired_sub_moves_actions_to_settings(monkeypatch):
    _patch_tariffs_mode(monkeypatch)
    sub = _fake_sub('expired', 'expired')

    card = _callbacks(
        kb.get_subscription_keyboard('ru', has_subscription=True, is_trial=False, subscription=sub)
    )
    assert 'subscription_settings' in card
    assert 'menu_buy' not in card
    assert 'tariff_switch' not in card

    settings = _settings_callbacks(sub)
    assert 'tariff_switch' in settings
    assert 'subscription_extend' in settings


def test_disabled_sub_moves_actions_to_settings(monkeypatch):
    _patch_tariffs_mode(monkeypatch)
    sub = _fake_sub('disabled', 'disabled')

    settings = _settings_callbacks(sub)

    assert 'tariff_switch' in settings
    assert 'subscription_extend' in settings


def test_main_menu_expired_shows_change_tariff_instead_of_buy(monkeypatch):
    _patch_tariffs_mode(monkeypatch)
    sub = _fake_sub('expired', 'expired')

    callbacks = _callbacks(
        kb.get_main_menu_keyboard(
            has_had_paid_subscription=True,
            has_active_subscription=False,
            subscription_is_active=False,
            subscription=sub,
        )
    )

    assert 'tariff_switch' in callbacks
    assert 'menu_buy' not in callbacks
