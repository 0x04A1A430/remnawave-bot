"""Общий визуальный слой админ-экранов.

Пользовательские экраны собраны из HTML-заголовка, ``<blockquote>`` с
параметрами и inline-кнопок с premium-иконками. Админка исторически рисовала
плоский текст без иконок, а часть подменю вообще уходила в legacy Markdown.
Этот модуль даёт маленький набор безопасных хелперов, чтобы привести админку
к тому же языку, не переписывая бизнес-логику экранов.

Правила:
- иконки берутся только из уже используемых в локалях premium-emoji ID;
- ``admin_screen`` чистит ведущие пустые строки, конвертирует legacy ``**…**``
  в HTML и добавляет иконку к заголовку;
- ``admin_button`` не меняет callback_data и не дублирует иконку, если она уже
  есть в тексте кнопки;
- back/cancel остаются ``danger``, как в пользовательской части.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.utils.button_emoji import make_button
from app.utils.text_compact import compact_blank_lines


@dataclass(frozen=True)
class AdminIcon:
    """Premium-иконка: ID из существующих локалей + юникод-фоллбэк."""

    emoji_id: str
    glyph: str

    def html(self) -> str:
        return f"<tg-emoji emoji-id='{self.emoji_id}'>{self.glyph}</tg-emoji>"


# Только ID, которые уже встречаются в app/localization/locales/*.json.
# Так админка не зависит от новых emoji-паков и одинаково выглядит во всех языках.
ADMIN_ICONS: dict[str, AdminIcon] = {
    'crown': AdminIcon('5807868868886009920', '👑'),
    'users': AdminIcon('5944970130554359187', '👥'),
    'user': AdminIcon('5886412370347036129', '👤'),
    'id': AdminIcon('5936017305585586269', '🪪'),
    'servers': AdminIcon('5879585266426973039', '🌐'),
    'tariff': AdminIcon('5924720918826848520', '📦'),
    'pricing': AdminIcon('5967548335542767952', '💳'),
    'stats': AdminIcon('5877485980901971030', '📊'),
    'chart': AdminIcon('5931472654660800739', '📊'),
    'support': AdminIcon('5931415565955503486', '🤖'),
    'messages': AdminIcon('5909015791088439934', '🎙'),
    'settings': AdminIcon('5877260593903177342', '⚙'),
    'system': AdminIcon('5877318502947229960', '💻'),
    'trials': AdminIcon('5985616167740379273', '⏰'),
    'payments': AdminIcon('5811989245761426317', '💰'),
    'money': AdminIcon('5967548335542767952', '💳'),
    'promo': AdminIcon('5967548335542767952', '💳'),
    'referrals': AdminIcon('5944970130554359187', '👥'),
    'gift': AdminIcon('6032937473162614352', '🎁'),
    'calendar': AdminIcon('5967412305338568701', '📅'),
    'refresh': AdminIcon('5839200986022812209', '🔄'),
    'updates': AdminIcon('5839200986022812209', '🔄'),
    'link': AdminIcon('5877465816030515018', '🔗'),
    'document': AdminIcon('5956561916573782596', '📄'),
    'rules': AdminIcon('5956561916573782596', '📄'),
    'faq': AdminIcon('5956561916573782596', '📄'),
    'logs': AdminIcon('5956561916573782596', '📄'),
    'success': AdminIcon('5776375003280838798', '✅'),
    'error': AdminIcon('5778527486270770928', '❌'),
    'warning': AdminIcon('5881702736843511327', '⚠️'),
    'lock': AdminIcon('5872829476143894491', '🚫'),
    'delete': AdminIcon('5879896690210639947', '🗑'),
    'devices': AdminIcon('5985833664884250583', '📱'),
    'star': AdminIcon('5958376256788502078', '⭐️'),
    'diamond': AdminIcon('5771755323572359189', '💎'),
    'coin': AdminIcon('5992430854909989581', '🪙'),
    'fire': AdminIcon('6008118472066732010', '🔥'),
    'back': AdminIcon('5877629862306385808', '◀️'),
}

_LEGACY_BOLD_RE = re.compile(r'\*\*(.+?)\*\*', re.DOTALL)
_ZERO_WIDTH_RE = re.compile(r'[\ufeff\u200b]')
_LEGACY_LINE_GLYPHS_RE = re.compile(r'^[ \t]*(?:[▤▣▥◷↻]\s*)+', re.MULTILINE)
_LEGACY_BUTTON_GLYPHS_RE = re.compile(r'^[▤▣▥◷↻]+\s*')
_LEADING_BACK_ARROW_RE = re.compile(r'^[←◀⬅⏪\s]+')

# Общие разделы админки: иконка подставляется по callback_data на отправке.
# Это покрывает и клавиатуры из app/keyboards/admin.py, и кнопки, собранные
# вручную в хендлерах, не меняя ни один callback.
ADMIN_BUTTON_ICONS: dict[str, str] = {
    'admin_panel': 'crown',
    'admin_submenu_users': 'users',
    'admin_servers': 'servers',
    'admin_tariffs': 'tariff',
    'admin_pricing': 'pricing',
    'admin_submenu_promo': 'stats',
    'admin_submenu_support': 'support',
    'admin_submenu_communications': 'messages',
    'admin_submenu_settings': 'settings',
    'admin_submenu_system': 'system',
    'admin_trials': 'trials',
    'admin_trials_reset': 'refresh',
    'admin_payments': 'payments',
    'admin_users': 'users',
    'admin_user_search': 'user',
    'admin_referrals': 'referrals',
    'admin_subscriptions': 'tariff',
    'admin_promocodes': 'promo',
    'admin_statistics': 'stats',
    'admin_campaigns': 'messages',
    'admin_coupons': 'gift',
    'admin_contests': 'star',
    'admin_promo_groups': 'users',
    'admin_remnawave': 'servers',
    'admin_remna_config': 'settings',
    'admin_monitoring': 'chart',
    'admin_mon_settings': 'chart',
    'admin_bot_config': 'settings',
    'admin_rules': 'rules',
    'maintenance_panel': 'settings',
    'admin_privacy_policy': 'document',
    'admin_public_offer': 'document',
    'admin_faq': 'faq',
    'reqch:list': 'link',
    'qamounts:list': 'money',
    'overpay_cert': 'success',
    'admin_updates': 'updates',
    'backup_panel': 'tariff',
    'admin_system_logs': 'logs',
    'admin_reports': 'stats',
    'admin_tickets': 'support',
    'admin_support_audit': 'logs',
    'admin_support_settings': 'settings',
    'admin_blacklist_settings': 'lock',
    'admin_blocked_users': 'lock',
    'admin_messages': 'messages',
    'admin_polls': 'stats',
    'admin_promo_offers': 'gift',
    'welcome_text_panel': 'document',
    'user_messages_panel': 'messages',
}

# Только разделы, уникальные для админки: пользовательская кнопка «Админ-панель»
# уже несёт свою иконку и не должна менять цвет.
ADMIN_BUTTON_STYLES: dict[str, str] = {
    'admin_submenu_users': 'primary',
    'admin_submenu_support': 'primary',
    'admin_submenu_settings': 'primary',
    'admin_trials': 'success',
    'admin_trials_reset': 'primary',
    'admin_payments': 'success',
}


def admin_icon(name: str) -> str:
    """HTML premium-иконки по имени; неизвестное имя — пустая строка."""
    icon = ADMIN_ICONS.get(name)
    return icon.html() if icon else ''


def normalize_admin_text(text: str | None) -> str:
    """Убирает legacy Markdown и служебные пробелы, не трогая HTML-разметку."""
    if not text:
        return ''
    normalized = _ZERO_WIDTH_RE.sub('', text)
    normalized = _LEGACY_LINE_GLYPHS_RE.sub('', normalized)
    normalized = _LEGACY_BOLD_RE.sub(r'<b>\1</b>', normalized)
    return normalized.strip()


def admin_screen(
    text: str | None,
    *,
    icon: str | None = None,
    compact: bool = False,
) -> str:
    """Готовит текст админ-экрана: иконка + заголовок, без ведущих пустых строк."""
    prepared = normalize_admin_text(text)
    if not prepared:
        return ''

    emoji = admin_icon(icon) if icon else ''
    if emoji and not prepared.startswith('<tg-emoji'):
        prepared = f'{emoji} {prepared}'

    if compact:
        prepared = compact_blank_lines(prepared)
    return prepared


def admin_heading(icon: str, title: str) -> str:
    """Заголовок экрана в стиле пользовательского меню."""
    return f'{admin_icon(icon)} <b>{title}</b>'


def admin_quote(body: str) -> str:
    """Блок параметров, как в главном меню и карточках пользователя."""
    return f'<blockquote>{body}</blockquote>'


def admin_button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    web_app: Any = None,
    icon: str | None = None,
    style: str | None = None,
    copy_text: str | None = None,
) -> Any:
    """Inline-кнопка с premium-иконкой и цветом; callback_data не меняется."""
    emoji_id = None
    if icon and '<tg-emoji' not in text:
        token = ADMIN_ICONS.get(icon)
        emoji_id = token.emoji_id if token else None

    return make_button(
        text=text,
        callback_data=callback_data,
        url=url,
        web_app=web_app,
        style=style,
        icon_custom_emoji_id=emoji_id,
        copy_text=copy_text,
    )


def admin_back_button(texts: Any, callback_data: str, *, label: str | None = None) -> Any:
    """Единая кнопка возврата: последняя в ряду, как в пользовательских меню."""
    raw_label = label or texts.BACK
    # Убираем старую текстовую стрелку: её место занимает premium-иконка.
    clean_label = _LEADING_BACK_ARROW_RE.sub('', raw_label).strip() or raw_label
    return make_button(
        text=clean_label,
        callback_data=callback_data,
        style='danger',
        icon_custom_emoji_id=ADMIN_ICONS['back'].emoji_id,
    )


def decorate_admin_markup(markup: Any) -> Any:
    """Добавляет иконки/цвета кнопкам админки по callback_data.

    Вызывается на общем пути отправки. Не переопределяет уже заданную иконку
    или цвет, поэтому явные настройки экрана всегда выигрывают.
    """
    from aiogram.types import InlineKeyboardMarkup

    if not isinstance(markup, InlineKeyboardMarkup):
        return markup

    changed = False
    rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for button in row:
            updates: dict[str, Any] = {}
            callback = button.callback_data or ''
            is_admin_button = callback in ADMIN_BUTTON_ICONS or callback.startswith('admin_')

            text = button.text or ''
            if is_admin_button:
                cleaned = _ZERO_WIDTH_RE.sub('', text).strip()
                cleaned = _LEGACY_BUTTON_GLYPHS_RE.sub('', cleaned).strip()
                if cleaned != text:
                    updates['text'] = cleaned

            icon_name = ADMIN_BUTTON_ICONS.get(callback)
            if (
                icon_name
                and not button.icon_custom_emoji_id
                and button.style != 'danger'
                and updates.get('style') != 'danger'
                and '<tg-emoji' not in (button.text or '')
            ):
                token = ADMIN_ICONS.get(icon_name)
                if token:
                    updates['icon_custom_emoji_id'] = token.emoji_id

            style = ADMIN_BUTTON_STYLES.get(callback)
            if style and not button.style:
                updates['style'] = style

            if updates:
                new_row.append(button.model_copy(update=updates))
                changed = True
            else:
                new_row.append(button)
        rows.append(new_row)

    if not changed:
        return markup
    return markup.model_copy(update={'inline_keyboard': rows})
