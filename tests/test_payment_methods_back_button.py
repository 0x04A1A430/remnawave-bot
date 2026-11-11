"""Кнопка «Назад» на экране выбора способа оплаты.

Из флоу «Недостаточно средств» возврат ведёт в главное меню, из меню баланса —
в сам баланс.
"""

from app.config import settings
from app.keyboards.inline import get_payment_methods_keyboard


def test_default_back_leads_to_balance(monkeypatch):
    monkeypatch.setattr(settings, 'TELEGRAM_STARS_ENABLED', True)

    kb = get_payment_methods_keyboard(50000, 'ru')
    last_button = kb.inline_keyboard[-1][0]

    assert last_button.callback_data == 'menu_balance'


def test_insufficient_flow_back_leads_to_main_menu(monkeypatch):
    monkeypatch.setattr(settings, 'TELEGRAM_STARS_ENABLED', True)

    kb = get_payment_methods_keyboard(
        50000,
        'ru',
        back_callback='back_to_menu',
        back_text='← В главное меню',
    )
    last_button = kb.inline_keyboard[-1][0]

    assert last_button.callback_data == 'back_to_menu'
    assert last_button.text == '← В главное меню'
