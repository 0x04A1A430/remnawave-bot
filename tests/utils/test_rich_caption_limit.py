"""В rich-режиме лимит caption (1024) не должен убирать логотип с длинных экранов."""

from __future__ import annotations

from app.utils import message_patch, rich_transport


LONG_TEXT = 'x' * 5000


def test_long_screen_keeps_logo_in_rich_mode(monkeypatch):
    monkeypatch.setattr(rich_transport, 'is_rich_transport_enabled', lambda: True)

    assert message_patch.caption_fits_logo(LONG_TEXT) is True


def test_long_screen_falls_back_to_text_in_classic_mode(monkeypatch):
    monkeypatch.setattr(rich_transport, 'is_rich_transport_enabled', lambda: False)

    assert message_patch.caption_fits_logo(LONG_TEXT) is False
    assert message_patch.caption_fits_logo('short') is True


def test_caption_limit_helper_unchanged():
    assert message_patch.caption_exceeds_telegram_limit(LONG_TEXT) is True
    assert message_patch.caption_exceeds_telegram_limit(None) is False
