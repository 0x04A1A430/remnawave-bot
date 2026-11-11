"""Tests for the transparent rich-message transport."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram.methods import EditMessageText, SendMessage, SendPhoto, SendRichMessage
from aiogram.types import Chat, Message, RichBlockParagraph, RichMessage, RichTextBold

from app.config import settings
from app.utils import rich_transport as rt
from app.utils.message_patch import LOGO_PATH


@pytest.fixture(autouse=True)
def _enable_transport(monkeypatch):
    monkeypatch.setattr(settings, 'RICH_MESSAGES_ENABLED', True, raising=False)
    monkeypatch.setattr(rt, '_rich_unavailable', False)
    monkeypatch.setattr(rt, '_warned_unavailable', False)
    monkeypatch.setattr(rt, '_resolve_logo_url', lambda: 'https://example.com/logo.png')


def test_text_message_becomes_rich_with_same_markup():
    method = SendMessage(chat_id=1, text='Привет\n<b>мир</b> <tg-emoji emoji-id="1">🔥</tg-emoji>')
    rich = rt.convert_method(method)

    assert isinstance(rich, SendRichMessage)
    assert rich.rich_message.html == 'Привет<br/><b>мир</b> <tg-emoji emoji-id="1">🔥</tg-emoji>'


def test_pre_block_keeps_newlines():
    rich = rt.convert_method(SendMessage(chat_id=1, text='<pre>a\nb</pre>'))
    assert isinstance(rich, SendRichMessage)
    assert rich.rich_message.html == '<pre>a\nb</pre>'


def test_markdown_is_left_classic():
    assert rt.convert_method(SendMessage(chat_id=1, text='*x*', parse_mode='Markdown')) is None


def test_explicit_none_is_plain_text():
    rich = rt.convert_method(SendMessage(chat_id=1, text='1 < 2 & <b>not bold</b>', parse_mode=None))

    assert isinstance(rich, SendRichMessage)
    assert rich.rich_message.html == '1 &lt; 2 &amp; &lt;b&gt;not bold&lt;/b&gt;'


def test_omitted_parse_mode_uses_html_default():
    rich = rt.convert_method(SendMessage(chat_id=1, text='<b>bold</b>'))

    assert isinstance(rich, SendRichMessage)
    assert rich.rich_message.html == '<b>bold</b>'


def test_entities_are_left_classic():
    method = SendMessage(chat_id=1, text='x')
    method.entities = [object()]
    assert rt.convert_method(method) is None


def test_edit_message_text_becomes_rich():
    rich = rt.convert_method(EditMessageText(chat_id=1, message_id=5, text='Hi\n<i>there</i>'))

    assert isinstance(rich, EditMessageText)
    assert rich.text is None
    assert rich.rich_message.html == '<img src="https://example.com/logo.png"/>Hi<br/><i>there</i>'
    assert rich.message_id == 5


def test_edit_without_logo_mode_has_no_image(monkeypatch):
    monkeypatch.setattr(settings, 'ENABLE_LOGO_MODE', False, raising=False)
    rich = rt.convert_method(EditMessageText(chat_id=1, message_id=5, text='Hi'))
    assert rich.rich_message.html == 'Hi'


def test_edit_does_not_duplicate_existing_image():
    rich = rt.convert_method(
        EditMessageText(chat_id=1, message_id=5, text='<img src="https://example.com/card.png"/>Hi')
    )
    assert rich.rich_message.html == '<img src="https://example.com/card.png"/>Hi'


def test_logo_photo_becomes_rich_image(monkeypatch):
    from aiogram.types import FSInputFile

    monkeypatch.setattr('app.utils.message_patch._logo_send_path', LOGO_PATH)
    rich = rt.convert_method(SendPhoto(chat_id=1, photo=FSInputFile(LOGO_PATH), caption='Caption\n<b>x</b>'))

    assert isinstance(rich, SendRichMessage)
    assert rich.rich_message.html == '<img src="https://example.com/logo.png"/>Caption<br/><b>x</b>'


def test_other_photos_become_rich_media():
    rich = rt.convert_method(SendPhoto(chat_id=1, photo='AgAD-qr-code', caption='QR\n<b>code</b>'))

    assert isinstance(rich, SendRichMessage)
    assert rich.rich_message.html == '<img src="tg://photo?id=photo"/>QR<br/><b>code</b>'
    assert rich.rich_message.media is not None
    assert rich.rich_message.media[0].id == 'photo'
    assert rich.rich_message.media[0].media.media == 'AgAD-qr-code'


def test_rich_message_gets_plain_text_fallback():
    rich = RichMessage(
        blocks=[
            RichBlockParagraph(text=RichTextBold(text='Тело меню')),
        ]
    )
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=1, type='private'),
        rich_message=rich,
    )

    rt.patch_rich_message_compat(message)

    assert message.text == 'Тело меню'
