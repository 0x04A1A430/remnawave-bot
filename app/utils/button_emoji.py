import re
from dataclasses import dataclass
from typing import Any

from aiogram.types import InlineKeyboardButton


_TG_EMOJI_RE = re.compile(r"<tg-emoji\s+emoji-id=['\"](\d+)['\"]>([^<]*)</tg-emoji>")


@dataclass
class ParsedButtonLabel:
    text: str
    icon_custom_emoji_id: str | None = None


def parse_button_label(raw: str) -> ParsedButtonLabel:
    """Extract a custom emoji ID and visible text from a locale string."""
    match = _TG_EMOJI_RE.search(raw)
    if not match:
        return ParsedButtonLabel(text=raw)

    clean_text = _TG_EMOJI_RE.sub('', raw).strip()
    if not clean_text:
        clean_text = match.group(2).strip() or '•'
    return ParsedButtonLabel(text=clean_text, icon_custom_emoji_id=match.group(1))


def make_button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    web_app: Any = None,
    style: str | None = None,
    icon_custom_emoji_id: str | None = None,
    copy_text: str | None = None,
) -> InlineKeyboardButton:
    """Create an inline button, extracting custom emoji markup when present."""
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
    if copy_text is not None:
        kwargs['copy_text'] = {'text': copy_text}

    emoji_id = icon_custom_emoji_id or parsed.icon_custom_emoji_id
    if emoji_id:
        kwargs['icon_custom_emoji_id'] = emoji_id
    return InlineKeyboardButton(**kwargs)
