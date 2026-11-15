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
- пагинация (``←``, ``→``, ``1/3``, callbacks ``*_page_*``, ``current_page``,
  ``noop``) и кнопки «Назад» живут без premium-иконок и без цвета: стрелка уже
  есть в тексте;
- в админке кнопки не красятся. ``danger`` остаётся только у забаненных
  пользователей в списке (текст начинается с ``×``), остальные админ-кнопки
  нейтральные, даже если экран просил ``primary``/``success``/``danger``;
- иконки глубоких меню (save/create/edit/delete/refresh/search/filter/chart/
  backup/chat …) сохраняются;
- пользовательские кнопки (callback вне админ-пространства) не трогаются:
  там back/cancel по-прежнему ``danger``, как и было.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.utils.button_emoji import make_button
from app.utils.text_compact import compact_blank_lines, compact_message_spacing


@dataclass(frozen=True)
class AdminIcon:
    """Premium-иконка: ID из существующих локалей + юникод-фоллбэк."""

    emoji_id: str
    glyph: str

    def html(self) -> str:
        return f"<tg-emoji emoji-id='{self.emoji_id}'>{self.glyph}</tg-emoji>"


# Premium-пак TgAndroidIcons (515 эмодзи). Имена ниже — семантические:
# экран/кнопка выбирает смысл, а не конкретный глиф. Юникод-фоллбэк остаётся
# внутри тега, поэтому старые клиенты без premium-эмодзи видят обычный символ.
ADMIN_ICONS: dict[str, AdminIcon] = {
    # Навигация и статусы
    'crown': AdminIcon('5807868868886009920', '👑'),
    'back': AdminIcon('5877629862306385808', '◀️'),
    'arrow_left': AdminIcon('5875082500023258804', '⬅️'),
    'arrow_right': AdminIcon('5877468380125990242', '➡️'),
    'arrow_up': AdminIcon('5875078273775439450', '🔼'),
    'arrow_down': AdminIcon('5875008416132370818', '🔽'),
    'success': AdminIcon('5776375003280838798', '✅'),
    'check': AdminIcon('5825794181183836432', '✔️'),
    'error': AdminIcon('5778527486270770928', '❌'),
    'cross': AdminIcon('5778527486270770928', '❌'),
    'warning': AdminIcon('5881702736843511327', '⚠️'),
    'info': AdminIcon('5879785854284599288', 'ℹ️'),
    'question': AdminIcon('5873121512445187130', '❓'),
    'plus': AdminIcon('5877219383691972108', '➕'),
    'prohibit': AdminIcon('5872829476143894491', '🚫'),
    'circle_red': AdminIcon('5967333011652350314', '🔴'),
    'circle_white': AdminIcon('5994324703559290598', '⚪️'),
    'lock': AdminIcon('5832546462478635761', '🔒'),
    'unlock': AdminIcon('6034962180875490251', '🔓'),
    'refresh': AdminIcon('5839200986022812209', '🔄'),
    'sync': AdminIcon('6006085522311614682', '🔃'),
    'repeat': AdminIcon('6005843436479975944', '🔁'),
    'queue': AdminIcon('6028482475385164542', '⏩'),
    'infinity': AdminIcon('5908972566537572230', '♾'),
    'clock': AdminIcon('5778605968208170641', '🕒'),
    'calendar': AdminIcon('5967412305338568701', '📅'),
    'trials': AdminIcon('5985616167740379273', '⏰'),
    # Пользователи и доступ
    'user': AdminIcon('5886412370347036129', '👤'),
    'users': AdminIcon('5944970130554359187', '👥'),
    'id': AdminIcon('5936017305585586269', '🪪'),
    'key': AdminIcon('6005570495603282482', '🔑'),
    'shield': AdminIcon('5926783847453692661', '🛡'),
    'eye': AdminIcon('5960714428394507968', '👁'),
    'hand': AdminIcon('5870734657384877785', '✋'),
    'thumbs_up': AdminIcon('5992199545151295755', '👍'),
    'thumbs_down': AdminIcon('5994368422031397063', '👎'),
    'heart': AdminIcon('5992453811510186287', '❤️'),
    # Инфраструктура, серверы, система
    'servers': AdminIcon('5967432491684860012', '🛜'),
    'network': AdminIcon('5967432491684860012', '🛜'),
    'wifi': AdminIcon('5874986954180791957', '📶'),
    'system': AdminIcon('5877318502947229960', '💻'),
    'terminal': AdminIcon('5877396173135811032', '⌨'),
    'database': AdminIcon('5877316724830768997', '🗃'),
    'folder': AdminIcon('5875206779196935950', '📁'),
    'image': AdminIcon('5888799736508454231', '🖼'),
    'map': AdminIcon('5883955653348692941', '🗺'),
    'battery': AdminIcon('5778546461436284629', '🔋'),
    'save': AdminIcon('5884448719889240368', '💾'),
    'backup': AdminIcon('5884448719889240368', '💾'),
    'download': AdminIcon('5886451926995833684', '⬇️'),
    'upload': AdminIcon('6005807101056651268', '⬆️'),
    'export': AdminIcon('5877540355187937244', '📤'),
    'import': AdminIcon('5877307202888273539', '📥'),
    'terminal_action': AdminIcon('5877396173135811032', '⌨'),
    'tools': AdminIcon('5988023995125993550', '🛠'),
    'settings': AdminIcon('5877260593903177342', '⚙'),
    'filter': AdminIcon('5875431869842985304', '🎛'),
    'broom': AdminIcon('5845947563601041174', '🧹'),
    'flask': AdminIcon('5897607722295103204', '🧪'),
    'fire': AdminIcon('6008118472066732010', '🔥'),
    'sparkles': AdminIcon('5931621672846103580', '💫'),
    'airplane': AdminIcon('5875465628285931233', '✈️'),
    'home': AdminIcon('5967822972931542886', '🏠'),
    'door': AdminIcon('5877341274863832725', '🚪'),
    # Деньги, тарифы, продажи
    'tariff': AdminIcon('5924720918826848520', '📦'),
    'pricing': AdminIcon('5967548335542767952', '💳'),
    'payments': AdminIcon('5967390100357648692', '💵'),
    'money': AdminIcon('5811989245761426317', '💰'),
    'coin': AdminIcon('5992430854909989581', '🪙'),
    'diamond': AdminIcon('5771755323572359189', '💎'),
    'star': AdminIcon('5958376256788502078', '⭐️'),
    'medal': AdminIcon('5961051261204696786', '🥇'),
    'gift': AdminIcon('6032937473162614352', '🎁'),
    'tag': AdminIcon('5854776233950188167', '🏷'),
    'promo': AdminIcon('5854776233950188167', '🏷'),
    'certificate': AdminIcon('5992157823838984339', '🎓'),
    # Коммуникации и контент
    'messages': AdminIcon('5771695636411847302', '📢'),
    'broadcast': AdminIcon('5771695636411847302', '📢'),
    'chat': AdminIcon('5886666250158870040', '💬'),
    'support': AdminIcon('5931415565955503486', '🤖'),
    'phone': AdminIcon('5897567714674741148', '📞'),
    'email': AdminIcon('5985817223749439505', '✉️'),
    'mic': AdminIcon('5897554554894946515', '🎤'),
    'video': AdminIcon('5882002216323125435', '🎥'),
    'music': AdminIcon('5891249688933305846', '🎵'),
    'palette': AdminIcon('5814690801665446789', '🎨'),
    'dice': AdminIcon('5960608239623082921', '🎲'),
    'bell': AdminIcon('5909201569898827582', '🔔'),
    'bell_off': AdminIcon('5909123362839335003', '🔕'),
    'pin': AdminIcon('5908961403917570106', '📌'),
    'search': AdminIcon('5874960879434338403', '🔎'),
    'edit': AdminIcon('5879841310902324730', '✏️'),
    'delete': AdminIcon('5879896690210639947', '🗑'),
    'link': AdminIcon('5877465816030515018', '🔗'),
    'document': AdminIcon('5956561916573782596', '📄'),
    'rules': AdminIcon('5897850551156084824', '📖'),
    'faq': AdminIcon('5873121512445187130', '❓'),
    'logs': AdminIcon('5877597667231534929', '🗒'),
    'report': AdminIcon('5886506997066502636', '📰'),
    'stats': AdminIcon('5877485980901971030', '📊'),
    'chart': AdminIcon('5776219138917668486', '📈'),
    'devices': AdminIcon('5985833664884250583', '📱'),
    'users_group': AdminIcon('5944970130554359187', '👥'),
    'referrals': AdminIcon('5944970130554359187', '👥'),
    'updates': AdminIcon('5839200986022812209', '🔄'),
    'sun': AdminIcon('6005924903419646424', '🌞'),
    'flower': AdminIcon('5967574255670399788', '🌸'),
    'burger': AdminIcon('5875271289605722323', '🍔'),
}

_LEGACY_BOLD_RE = re.compile(r'\*\*(.+?)\*\*', re.DOTALL)
_ZERO_WIDTH_RE = re.compile(r'[\ufeff\u200b]')
_LEGACY_LINE_GLYPHS_RE = re.compile(r'^[ \t]*(?:[▤▣▥◷↻]\s*)+', re.MULTILINE)
_LEGACY_BUTTON_GLYPHS_RE = re.compile(r'^[▤▣▥◷↻]+\s*')

# Общие разделы админки: иконка подставляется по callback_data на отправке.
# Это покрывает и клавиатуры из app/keyboards/admin.py, и кнопки, собранные
# вручную в хендлерах, не меняя ни один callback.
ADMIN_BUTTON_ICONS: dict[str, str] = {
    'admin_panel': 'crown',
    'admin_submenu_users': 'users',
    'admin_servers': 'servers',
    'admin_tariffs': 'tariff',
    'admin_pricing': 'pricing',
    'admin_submenu_promo': 'promo',
    'admin_submenu_support': 'support',
    'admin_submenu_communications': 'messages',
    'admin_submenu_settings': 'settings',
    'admin_submenu_system': 'system',
    'admin_trials': 'trials',
    'admin_trials_reset': 'refresh',
    'admin_payments': 'payments',
    'admin_users': 'users',
    'admin_user_search': 'search',
    'admin_referrals': 'referrals',
    'admin_subscriptions': 'tariff',
    'admin_promocodes': 'promo',
    'admin_statistics': 'chart',
    'admin_campaigns': 'broadcast',
    'admin_coupons': 'gift',
    'admin_contests': 'medal',
    'admin_promo_groups': 'users',
    'admin_remnawave': 'shield',
    'admin_remna_config': 'terminal',
    'admin_monitoring': 'chart',
    'admin_mon_settings': 'filter',
    'admin_bot_config': 'settings',
    'admin_rules': 'rules',
    'maintenance_panel': 'tools',
    'admin_privacy_policy': 'shield',
    'admin_public_offer': 'document',
    'admin_faq': 'faq',
    'reqch:list': 'broadcast',
    'qamounts:list': 'payments',
    'overpay_cert': 'certificate',
    'admin_updates': 'updates',
    'backup_panel': 'backup',
    'admin_system_logs': 'logs',
    'admin_reports': 'report',
    'admin_tickets': 'chat',
    'admin_support_audit': 'logs',
    'admin_support_settings': 'tools',
    'admin_blacklist_settings': 'prohibit',
    'admin_blocked_users': 'lock',
    'admin_messages': 'broadcast',
    'admin_polls': 'stats',
    'admin_promo_offers': 'gift',
    'welcome_text_panel': 'edit',
    'user_messages_panel': 'chat',
    # Частые действия в списках/карточках
    'admin_users_stats': 'chart',
    'admin_subs_list': 'document',
    'admin_subs_expiring': 'calendar',
    'admin_subs_stats': 'chart',
    'admin_subs_countries': 'map',
    'admin_stats_users': 'users',
    'admin_stats_subs': 'tariff',
    'admin_stats_revenue': 'payments',
    'admin_stats_referrals': 'referrals',
    'admin_stats_summary': 'chart',
    'admin_rw_system': 'chart',
    'admin_rw_nodes': 'servers',
    'admin_rw_sync': 'sync',
    'admin_mon_logs': 'logs',
    'admin_mon_logs_filters': 'filter',
    'admin_mon_clear_logs': 'delete',
    'admin_system_logs_refresh': 'refresh',
    'admin_system_logs_download': 'download',
    'admin_payments_check_all': 'check',
    'admin_payments_export': 'export',
    'admin_promo_create': 'plus',
    'admin_promo_list': 'promo',
    'admin_ref_levels': 'medal',
    'admin_support_audit_page': 'logs',
    'admin_blacklist_update': 'refresh',
    'admin_blacklist_view': 'eye',
    'admin_blacklist_set_url': 'link',
    'admin_blacklist_toggle': 'unlock',
    'admin_blocked_scan': 'refresh',
    'admin_blocked_list': 'lock',
}

# Префиксы callback_data для глубоких меню: конкретные правила идут раньше
# общих. Нужны, потому что кнопки вложенных экранов собираются динамически
# (`admin_tariff_edit_name:{id}`, `promo_manage_{id}` и т.п.).
ADMIN_BUTTON_ICON_PREFIXES: tuple[tuple[str, str], ...] = (
    ('admin_tariff_edit', 'edit'),
    ('admin_server_edit', 'edit'),
    ('promo_edit', 'edit'),
    ('admin_campaign_edit', 'edit'),
    ('admin_user_edit', 'edit'),
    ('admin_subs_list', 'document'),
    ('admin_subs_expiring', 'calendar'),
    ('admin_subs_stats', 'chart'),
    ('admin_subs_countries', 'map'),
    ('admin_subs', 'tariff'),
    ('admin_sub', 'tariff'),
    ('admin_stats', 'chart'),
    ('admin_statistics', 'chart'),
    ('admin_servers', 'servers'),
    ('admin_server', 'servers'),
    ('admin_users', 'users'),
    ('admin_user', 'user'),
    ('admin_tariff', 'tariff'),
    ('admin_price', 'pricing'),
    ('admin_pricing', 'pricing'),
    ('admin_rw_system', 'chart'),
    ('admin_rw_nodes', 'servers'),
    ('admin_rw', 'servers'),
    ('admin_mon_logs', 'logs'),
    ('admin_mon_settings', 'filter'),
    ('admin_mon', 'chart'),
    ('admin_monitoring', 'chart'),
    ('admin_promo_offers', 'gift'),
    ('admin_promo', 'promo'),
    ('promo_manage', 'promo'),
    ('promo_', 'promo'),
    ('admin_ref', 'referrals'),
    ('admin_contest', 'medal'),
    ('admin_campaign', 'broadcast'),
    ('admin_support_audit', 'logs'),
    ('admin_support_settings', 'tools'),
    ('admin_support', 'support'),
    ('admin_ticket', 'chat'),
    ('admin_faq', 'faq'),
    ('admin_rules', 'rules'),
    ('admin_privacy', 'shield'),
    ('admin_public_offer', 'document'),
    ('admin_system_logs', 'logs'),
    ('admin_logs', 'logs'),
    ('backup', 'backup'),
    ('admin_updates', 'updates'),
    ('admin_reports', 'report'),
    ('maintenance', 'tools'),
    ('admin_bot_config', 'settings'),
    ('admin_remna_config', 'terminal'),
    ('admin_blacklist', 'prohibit'),
    ('admin_blocked', 'lock'),
    ('admin_trials', 'trials'),
    ('admin_payments', 'payments'),
    ('admin_coupons', 'gift'),
    ('admin_polls', 'stats'),
    ('poll_', 'stats'),
    ('reqch:', 'broadcast'),
    ('qamounts:', 'payments'),
    ('overpay', 'certificate'),
    ('welcome', 'edit'),
    ('user_message', 'chat'),
    ('list_user_messages', 'chat'),
    ('admin_help', 'question'),
    ('admin_gift', 'gift'),
    ('admin_panel', 'crown'),
    ('admin_', 'settings'),
)

_ADMIN_CALLBACK_PREFIXES = (
    'admin_',
    'promo_',
    'poll_',
    'backup_',
    'maintenance_',
    'welcome_',
    'reqch:',
    'qamounts:',
    'overpay_',
    'user_message',
    'list_user_messages',
)

# Служебные callback'и листалки: общие для админки и пользовательской части,
# поэтому их цвет/иконка снимаются всегда.
_PAGINATION_CALLBACKS = frozenset({'current_page', 'noop'})

# «Назад» ищем строго: «Backups» не должен считаться back-кнопкой.
_BACK_TEXT_RE = re.compile(r'^(?:[←◀⬅⏪]|назад\b|back\b)', re.IGNORECASE)
_PAGINATION_TEXTS = frozenset({'←', '→', '◀', '▶', '‹', '›'})
_PAGE_COUNTER_RE = re.compile(r'^(?:\D{0,12})?\d+\s*/\s*\d+$')
_PAGE_NUMBER_RE = re.compile(r'^\d+$')
_PAGE_CALLBACK_RE = re.compile(r'(?:^|_)page(?:_|$)')


def _is_pagination_button(callback: str, text: str) -> bool:
    """Служебный элемент листалки: стрелка, счётчик «1/3», номер страницы."""
    stripped = text.strip()
    if stripped in _PAGINATION_TEXTS or _PAGE_COUNTER_RE.match(stripped):
        return True
    # Номер страницы — только вместе с page-callback: «← К списку» на
    # *_page_* это back-кнопка, а не листалка.
    return bool(_PAGE_CALLBACK_RE.search(callback)) and bool(_PAGE_NUMBER_RE.match(stripped))


def _is_blocked_user_button(callback: str, text: str) -> bool:
    """Красным оставляем только забаненных пользователей в списке."""
    return callback.startswith('admin_user_manage_') and text.lstrip().startswith('×')


def _is_admin_callback(callback: str) -> bool:
    return bool(callback) and callback.startswith(_ADMIN_CALLBACK_PREFIXES)


def _looks_like_back(callback: str, text: str) -> bool:
    if callback.startswith(('back_to_', 'admin_back_')) or callback.endswith('_back'):
        return True
    return bool(_BACK_TEXT_RE.match(text.strip()))


def _infer_button_icon(callback: str, text: str) -> str | None:
    """Иконка глубокого меню по callback/тексту; back и листалка — без иконки."""
    if _looks_like_back(callback, text) or _is_pagination_button(callback, text):
        return None

    lowered = f'{callback} {text}'.lower()

    # Действие важнее раздела: «Сохранить» в карточке тарифа — это дискета,
    # а не иконка тарифа.
    if any(marker in lowered for marker in ('save', 'сохран', 'примен', 'apply', 'confirm', 'подтверд')):
        return 'save'
    if any(marker in lowered for marker in ('create', 'add', 'создат', 'добав')):
        return 'plus'
    if any(marker in lowered for marker in ('edit', 'редакт', 'измен')):
        return 'edit'
    if any(marker in lowered for marker in ('delete', 'remove', 'удал', 'очист')):
        return 'delete'
    if any(marker in lowered for marker in ('send', 'отправ', 'publish', 'опублик')):
        return 'broadcast'
    if any(marker in lowered for marker in ('refresh', 'обнов', 'sync', 'синхрон')):
        return 'refresh'
    if any(marker in lowered for marker in ('search', 'поиск')):
        return 'search'
    if any(marker in lowered for marker in ('filter', 'фильтр')):
        return 'filter'
    if any(marker in lowered for marker in ('download', 'скач')):
        return 'download'
    if any(marker in lowered for marker in ('export', 'экспорт')):
        return 'export'
    if any(marker in lowered for marker in ('stats', 'статистик', 'chart', 'график')):
        return 'chart'

    icon = ADMIN_BUTTON_ICONS.get(callback)
    if icon:
        return icon

    for prefix, icon_name in ADMIN_BUTTON_ICON_PREFIXES:
        if callback.startswith(prefix):
            return icon_name
    return 'settings' if _is_admin_callback(callback) else None


_DANGER_MARKERS = (
    'delete',
    'remove',
    'clear',
    'reset',
    'revoke',
    'stop',
    'disable',
    'deactivate',
    'block_user',
    'ban_user',
    'block_ticket',
    'admin_block_user',
    'close_ticket',
    'close_all',
    'drop',
    'wipe',
    'удал',
    'очист',
    'сброс',
    'отключ',
    'деактив',
    'заблокировать',
    'закрыть',
)
_SUCCESS_MARKERS = (
    'unblock',
    'confirm',
    'save',
    'create',
    'add',
    'apply',
    'enable',
    'activate',
    'start',
    'send',
    'publish',
    'restore',
    'topup',
    'pay',
    'разблок',
    'подтверд',
    'сохран',
    'создат',
    'добав',
    'примен',
    'включ',
    'актив',
    'запуст',
    'отправ',
    'опублик',
    'восстанов',
    'пополн',
    'оплат',
)
_PRIMARY_MARKERS = (
    'list',
    'view',
    'show',
    'stats',
    'settings',
    'edit',
    'open',
    'filter',
    'export',
    'download',
    'detail',
    'info',
    'select',
    'choose',
    'history',
    'log',
    'report',
    'search',
    'refresh',
    'sync',
    'список',
    'просмотр',
    'статистик',
    'настрой',
    'поиск',
    'редакт',
    'открыт',
    'фильтр',
    'экспорт',
    'скач',
    'подроб',
    'истор',
    'лог',
    'отчёт',
    'обнов',
)


def _infer_button_style(callback: str, text: str) -> str | None:
    """Семантический цвет: зелёный — важное/подтверждения, синий — меню/навигация, красный — опасное."""
    if not _is_admin_callback(callback):
        return None

    if _is_blocked_user_button(callback, text):
        return 'danger'
    # Обычные строки пользователей не красим: красный только у забаненных.
    if callback.startswith('admin_user_manage_'):
        return None
    # «Назад» — навигация, а не опасное действие.
    if _looks_like_back(callback, text):
        return None
    if _is_pagination_button(callback, text):
        return 'primary'

    lowered = f'{callback} {text}'.lower()
    # unblock/разблокировать — позитивное действие, а не block.
    if any(marker in lowered for marker in ('unblock', 'разблок')):
        return 'success'
    if any(marker in lowered for marker in _DANGER_MARKERS):
        return 'danger'
    if any(marker in lowered for marker in _SUCCESS_MARKERS):
        return 'success'
    if any(marker in lowered for marker in _PRIMARY_MARKERS):
        return 'primary'
    # Меню и важные разделы — синие.
    return 'primary'


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

    prepared = compact_message_spacing(prepared)
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
    """Единая кнопка возврата без premium-иконки и без цвета.

    В локали текст уже содержит стрелку «←», поэтому иконку не добавляем (иначе
    две стрелки), а текст не переписываем. Нейтральный цвет поддерживает и
    ``decorate_admin_markup``: explicit ``danger`` у админского back снимается.
    """
    return make_button(
        text=label or texts.BACK,
        callback_data=callback_data,
    )


def decorate_admin_markup(markup: Any) -> Any:
    """Приводит кнопки админки к общей политике прямо на отправке.

    Что делает:
    - пагинация и «Назад» — без premium-иконок и без цвета;
    - ``danger`` остаётся только у забаненных пользователей в списке;
      остальные админ-кнопки становятся нейтральными, даже если экран задал
      ``primary``/``success``/``danger``;
    - иконки глубоких меню сохраняются, недостающие подставляются по
      callback/тексту.

    Пользовательские кнопки (callback вне ``admin_``/``promo_``/``poll_``/
    ``backup_``/…) не трогаются: там back/cancel остаются ``danger``.
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
            text = button.text or ''

            is_admin_button = _is_admin_callback(callback)
            is_pagination = _is_pagination_button(callback, text) and (
                is_admin_button or callback in _PAGINATION_CALLBACKS
            )
            if not is_admin_button and not is_pagination:
                new_row.append(button)
                continue

            if is_admin_button:
                cleaned = _ZERO_WIDTH_RE.sub('', text).strip()
                cleaned = _LEGACY_BUTTON_GLYPHS_RE.sub('', cleaned).strip()
                if cleaned != text:
                    updates['text'] = cleaned
                    text = cleaned

            if is_pagination:
                # Служебные стрелки/счётчики: без premium-эмодзи, но это
                # навигация — синий цвет меню.
                if button.icon_custom_emoji_id:
                    updates['icon_custom_emoji_id'] = None
                if button.style != 'primary':
                    updates['style'] = 'primary'
            elif _looks_like_back(callback, text):
                # «Назад»: стрелка уже есть в тексте, иконка и цвет не нужны.
                if button.icon_custom_emoji_id:
                    updates['icon_custom_emoji_id'] = None
                if button.style:
                    updates['style'] = None
            else:
                is_blocked = _is_blocked_user_button(callback, text)
                desired_style = _infer_button_style(callback, text)
                if button.style != desired_style:
                    updates['style'] = desired_style

                if not is_blocked and not button.icon_custom_emoji_id and '<tg-emoji' not in text:
                    icon_name = _infer_button_icon(callback, text)
                    token = ADMIN_ICONS.get(icon_name) if icon_name else None
                    if token:
                        updates['icon_custom_emoji_id'] = token.emoji_id

            if updates:
                new_row.append(button.model_copy(update=updates))
                changed = True
            else:
                new_row.append(button)
        rows.append(new_row)

    if not changed:
        return markup
    return markup.model_copy(update={'inline_keyboard': rows})
