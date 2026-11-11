"""Единый вид пользовательских уведомлений: premium-эмодзи, цитата, цветные кнопки."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton

from app.utils.button_emoji import make_button


# id premium-эмодзи из фирменного набора (см. locales/*.json)
ICONS: dict[str, str] = {
    '🔒': '5832546462478635761',
    '⏰': '5985616167740379273',
    '🎁': '6032937473162614352',
    '💎': '5963312935148195483',
    '🔥': '6008118472066732010',
    '💰': '5811989245761426317',
    '💳': '5967548335542767952',
    '✅': '5776375003280838798',
    '🧪': '5897607722295103204',
    '💬': '5886666250158870040',
    '📅': '5967412305338568701',
    '👥': '5915556996215476302',
}

STYLES = {'primary': 'primary', 'success': 'success', 'danger': 'danger'}


def premium(emoji: str) -> str:
    """Premium-эмодзи из набора, либо обычный символ, если id неизвестен."""
    emoji_id = ICONS.get(emoji)
    if not emoji_id:
        return emoji
    return f'<tg-emoji emoji-id="{emoji_id}">{emoji}</tg-emoji>'


def card(*, emoji: str, title: str, lines: list[str], note: str | None = None) -> str:
    """Заголовок с premium-эмодзи, детали в цитате, необязательная подпись."""
    body = '\n'.join(line for line in lines if line)
    text = f'{premium(emoji)} <b>{title}</b>'
    if body:
        text += f'\n<blockquote>{body}</blockquote>'
    if note:
        text += f'\n\n<i>{note}</i>'
    return text


def button(
    text: str,
    emoji: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
) -> InlineKeyboardButton:
    """Кнопка с premium-эмодзи и цветом; тег в подписи превращается в иконку."""
    label = f'{premium(emoji)} {text}' if emoji else text
    return make_button(
        label,
        callback_data=callback_data,
        url=url,
        style=STYLES.get(style or '', style),
        icon_custom_emoji_id=ICONS.get(emoji),
    )
