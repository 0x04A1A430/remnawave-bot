import re
from dataclasses import dataclass
from typing import Any

from aiogram.types import InlineKeyboardButton


_TG_EMOJI_RE = re.compile(r"<tg-emoji\s+emoji-id=['\"](\d+)['\"]>(.*?)</tg-emoji>", re.DOTALL)


@dataclass
class ParsedButtonLabel:
    text: str
    icon_custom_emoji_id: str | None = None


def parse_button_label(raw: str) -> ParsedButtonLabel:
    """Extract custom emoji ID and clean text from a locale string.

    Converts ``<tg-emoji emoji-id='12345'>✔️</tg-emoji> Купить`` into
    ``ParsedButtonLabel(text='Купить', icon_custom_emoji_id='12345')``.

    If no tg-emoji tag is found, returns the original text with no emoji ID.
    If multiple tags exist, only the first one is extracted (buttons support
    a single leading custom emoji icon).
    """
    match = _TG_EMOJI_RE.search(raw)
    if not match:
        return ParsedButtonLabel(text=raw)

    emoji_id = match.group(1)
    full_text = raw
    clean_text = _TG_EMOJI_RE.sub('', full_text).strip()

    return ParsedButtonLabel(text=clean_text, icon_custom_emoji_id=emoji_id)


def make_button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    web_app: Any = None,
    style: str | None = None,
    icon_custom_emoji_id: str | None = None,
) -> InlineKeyboardButton:
    """Create an InlineKeyboardButton with automatic custom emoji extraction.

    Parses ``<tg-emoji>`` tags from the text and passes the emoji ID to
    ``icon_custom_emoji_id`` while stripping the tag from the visible text.

    If ``icon_custom_emoji_id`` is passed explicitly, it takes precedence
    over any tag found in the text.
    """
    parsed = parse_button_label(text)
    kwargs: dict[str, Any] = {'text': parsed.text}

    if callback_data is not None:
        kwargs['callback_data'] = callback_data
    if url is not None:
        kwargs['url'] = url
    if web_app is not None:
        kwargs['web_app'] = web_app
    if style is not None:
        kwargs['style'] = style

    final_emoji_id = icon_custom_emoji_id or parsed.icon_custom_emoji_id
    if final_emoji_id:
        kwargs['icon_custom_emoji_id'] = final_emoji_id

    return InlineKeyboardButton(**kwargs)
