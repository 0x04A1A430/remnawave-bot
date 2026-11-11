"""Inline-подарки используют rich-контент (InputRichMessageContent)."""

from __future__ import annotations

from aiogram import types

from app.handlers.admin.inline_gift import _gift_result


THUMB = 'https://example.com/gift.png'
CAPTION = '<b>Подарок</b>\n\n<blockquote>+30 дней</blockquote>'


def _keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text='Активировать', url='https://t.me/x')]])


def test_default_result_is_rich():
    result = _gift_result('code', 'title', 'desc', CAPTION, THUMB, _keyboard(), silent=False)

    assert isinstance(result, types.InlineQueryResultArticle)
    content = result.input_message_content
    assert isinstance(content, types.InputRichMessageContent)
    assert content.rich_message.html == f'<img src="{THUMB}"/><b>Подарок</b><br/><br/><blockquote>+30 дней</blockquote>'


def test_photo_mode_is_rich_too():
    result = _gift_result('code', 'title', 'desc', CAPTION, THUMB, _keyboard(), silent=True)

    assert isinstance(result, types.InlineQueryResultArticle)
    assert isinstance(result.input_message_content, types.InputRichMessageContent)


def test_classic_fallback_is_plain_text():
    result = _gift_result('code', 'title', 'desc', CAPTION, THUMB, _keyboard(), silent=False, rich=False)

    assert isinstance(result, types.InlineQueryResultArticle)
    assert isinstance(result.input_message_content, types.InputTextMessageContent)


def test_classic_photo_fallback_is_photo_result():
    result = _gift_result('code', 'title', 'desc', CAPTION, THUMB, _keyboard(), silent=True, rich=False)

    assert isinstance(result, types.InlineQueryResultPhoto)
