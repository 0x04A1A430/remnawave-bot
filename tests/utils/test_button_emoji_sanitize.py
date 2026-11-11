"""Кнопки не должны показывать сырой HTML: эмодзи-разметка уезжает в icon_custom_emoji_id."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils.button_emoji import make_button, parse_button_label, sanitize_markup


def test_parse_strips_custom_emoji_from_label():
    parsed = parse_button_label('<tg-emoji emoji-id="123">🔥</tg-emoji> Докупить')

    assert parsed.text == 'Докупить'
    assert parsed.icon_custom_emoji_id == '123'


def test_make_button_converts_markup_to_icon():
    button = make_button('<tg-emoji emoji-id="42">✅</tg-emoji> Да', callback_data='yes')

    assert button.text == 'Да'
    assert button.icon_custom_emoji_id == '42'
    assert button.callback_data == 'yes'


def test_sanitize_markup_cleans_directly_built_buttons():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='<tg-emoji emoji-id="7">📱</tg-emoji> Устройства', callback_data='dev')],
            [InlineKeyboardButton(text='Назад', callback_data='back')],
        ]
    )

    cleaned = sanitize_markup(markup)

    assert cleaned is not markup
    assert cleaned.inline_keyboard[0][0].text == 'Устройства'
    assert cleaned.inline_keyboard[0][0].icon_custom_emoji_id == '7'
    assert cleaned.inline_keyboard[1][0].text == 'Назад'
    assert cleaned.inline_keyboard[1][0].icon_custom_emoji_id is None


def test_sanitize_keeps_plain_markup_untouched():
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Назад', callback_data='back')]])

    assert sanitize_markup(markup) is markup
    assert sanitize_markup(None) is None
