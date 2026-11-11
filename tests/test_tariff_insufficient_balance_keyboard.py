from app.config import settings
from app.handlers.subscription.tariff_purchase import (
    get_daily_tariff_insufficient_balance_keyboard,
    get_instant_switch_insufficient_balance_keyboard,
    get_tariff_extend_insufficient_balance_keyboard,
    get_tariff_insufficient_balance_keyboard,
    get_tariff_switch_insufficient_balance_keyboard,
)


def _callbacks(keyboard):
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


def test_buy_insufficient_offers_single_topup_button(monkeypatch):
    monkeypatch.setattr(settings, 'AUTO_PURCHASE_AFTER_TOPUP_ENABLED', True)
    monkeypatch.setattr(settings, 'TELEGRAM_STARS_ENABLED', True)

    callbacks = _callbacks(get_tariff_insufficient_balance_keyboard(7, 30, 'ru', missing_kopeks=50000))

    assert 'balance_topup_amount|50000' in callbacks
    # Никаких прямых оплат, подтверждения покупки и СБП на экране нехватки.
    assert not any((c or '').startswith('topup_amount|') for c in callbacks)
    assert not any((c or '').startswith('tariff_confirm:') for c in callbacks)
    assert not any((c or '').startswith('tariff_sbp:') for c in callbacks)
    assert 'tariff_select:7' in callbacks


def test_buy_insufficient_topup_carries_missing_amount(monkeypatch):
    price = 63000
    balance = 37000
    missing = price - balance

    callbacks = _callbacks(get_tariff_insufficient_balance_keyboard(7, 30, 'ru', missing_kopeks=missing))

    assert f'balance_topup_amount|{missing}' in callbacks
    assert f'balance_topup_amount|{price}' not in callbacks
    assert f'balance_topup_amount|{balance}' not in callbacks


def test_extend_insufficient_offers_single_topup_button(monkeypatch):
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: False)
    monkeypatch.setattr(settings, 'AUTO_PURCHASE_AFTER_TOPUP_ENABLED', True)

    keyboard = get_tariff_extend_insufficient_balance_keyboard(7, 42, 30, 'ru', missing_kopeks=50000)
    callbacks = _callbacks(keyboard)

    assert 'balance_topup_amount|50000' in callbacks
    assert 'subscription_extend' in callbacks
    assert not any((c or '').startswith('topup_amount|') for c in callbacks)
    assert not any((c or '').startswith('tariff_select:') for c in callbacks)
    assert not any((c or '').startswith('tariff_sbp:') for c in callbacks)


def test_extend_topup_carries_missing_amount(monkeypatch):
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: False)

    price = 63000
    balance = 37000
    missing = price - balance

    callbacks = _callbacks(get_tariff_extend_insufficient_balance_keyboard(7, 42, 30, 'ru', missing_kopeks=missing))

    assert f'balance_topup_amount|{missing}' in callbacks


def test_daily_insufficient_offers_single_topup_button():
    callbacks = _callbacks(get_daily_tariff_insufficient_balance_keyboard(7, 'ru', missing_kopeks=50000))

    assert 'balance_topup_amount|50000' in callbacks
    assert 'menu_buy' in callbacks
    assert not any((c or '').startswith('tariff_sbp:') for c in callbacks)


def test_switch_insufficient_offers_single_topup_button():
    callbacks = _callbacks(get_tariff_switch_insufficient_balance_keyboard(7, 30, 'ru', missing_kopeks=50000))

    assert 'balance_topup_amount|50000' in callbacks
    assert 'tariff_sw_select:7' in callbacks


def test_instant_switch_insufficient_offers_single_topup_button():
    callbacks = _callbacks(get_instant_switch_insufficient_balance_keyboard(7, 'ru', missing_kopeks=50000))

    assert 'balance_topup_amount|50000' in callbacks
    assert 'instant_switch' in callbacks
