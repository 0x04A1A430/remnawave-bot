from app.config import settings
from app.handlers.subscription.tariff_purchase import get_tariff_insufficient_balance_keyboard


def _callbacks(keyboard):
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


def test_classic_topup_when_autopurchase_disabled(monkeypatch):
    monkeypatch.setattr(settings, 'AUTO_PURCHASE_AFTER_TOPUP_ENABLED', False)

    keyboard = get_tariff_insufficient_balance_keyboard(7, 30, 'ru', missing_kopeks=50000)
    callbacks = _callbacks(keyboard)

    assert 'balance_topup' in callbacks
    assert not any((c or '').startswith('topup_amount|') for c in callbacks)
    assert 'tariff_select:7' in callbacks


def test_inlines_prefilled_payment_when_autopurchase_enabled(monkeypatch):
    monkeypatch.setattr(settings, 'AUTO_PURCHASE_AFTER_TOPUP_ENABLED', True)
    monkeypatch.setattr(settings, 'TELEGRAM_STARS_ENABLED', True)

    keyboard = get_tariff_insufficient_balance_keyboard(7, 30, 'ru', missing_kopeks=50000)
    callbacks = _callbacks(keyboard)

    # прямая оплата ровно недостающей суммой, без промежуточного «Пополнить баланс»
    assert 'topup_amount|stars|50000' in callbacks
    assert 'balance_topup' not in callbacks
    # возврат ведёт к выбору тарифа
    assert 'tariff_select:7' in callbacks


def test_classic_topup_when_missing_zero(monkeypatch):
    monkeypatch.setattr(settings, 'AUTO_PURCHASE_AFTER_TOPUP_ENABLED', True)
    monkeypatch.setattr(settings, 'TELEGRAM_STARS_ENABLED', True)

    keyboard = get_tariff_insufficient_balance_keyboard(7, 30, 'ru', missing_kopeks=0)

    assert 'balance_topup' in _callbacks(keyboard)


def test_falls_back_to_topup_without_direct_payment_methods(monkeypatch):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    monkeypatch.setattr(settings, 'AUTO_PURCHASE_AFTER_TOPUP_ENABLED', True)
    # Клавиатура пополнения без кнопок прямой оплаты (только навигация) → классический фолбэк
    monkeypatch.setattr(
        'app.keyboards.inline.get_payment_methods_keyboard',
        lambda amount, language=None: InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text='x', callback_data='menu_balance')]]
        ),
    )

    keyboard = get_tariff_insufficient_balance_keyboard(7, 30, 'ru', missing_kopeks=50000)

    assert 'balance_topup' in _callbacks(keyboard)
