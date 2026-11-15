"""Управление тарифами в админ-панели."""

import html

import structlog
from aiogram import Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.promo_group import get_promo_groups_with_counts
from app.database.crud.server_squad import get_all_server_squads
from app.database.crud.tariff import (
    create_tariff,
    delete_tariff,
    get_active_subscriptions_count_by_tariff_id,
    get_tariff_by_id,
    get_tariff_subscriptions_count,
    get_tariffs_with_subscriptions_count,
    update_tariff,
)
from app.database.models import Tariff, User
from app.handlers.admin.tariff_custom_days import format_custom_days_settings, register_custom_days_handlers
from app.handlers.admin.tariff_custom_traffic import (
    format_custom_traffic_settings,
    register_custom_traffic_handlers,
)
from app.handlers.admin.tariff_panel_settings import format_panel_settings, register_panel_settings_handlers
from app.handlers.admin.tariff_server_limits import format_server_limits_summary, register_server_limits_handlers
from app.localization.texts import Texts, get_texts
from app.states import AdminStates
from app.utils.admin_ui import (
    admin_back_button,
    admin_button,
    admin_heading,
    admin_icon,
    admin_quote,
    admin_screen,
)
from app.utils.decorators import admin_required, error_handler
from app.utils.formatting import format_period, format_price_kopeks, format_traffic
from app.utils.miniapp_buttons import strip_leading_emoji


logger = structlog.get_logger(__name__)

ITEMS_PER_PAGE = 10


def _tariff_screen(text: str, icon: str = 'tariff') -> str:
    """Готовит экран тарифа: premium-иконка вместо legacy-глифа и без пустых краёв."""
    return admin_screen(strip_leading_emoji(text), icon=icon, compact=True)


def _tariff_field(icon: str, label: str, value: str) -> str:
    """Строка параметра: premium-иконка + жирная подпись."""
    return f'{admin_icon(icon)} <b>{label}:</b> {value}'


def _tariff_prompt(icon: str, title: str, params: list[str], hint: str = '') -> str:
    """Промпт тарифа: заголовок, параметры в blockquote, подсказка."""
    body = '\n'.join(param for param in params if param).strip()
    parts = [admin_heading(icon, title), '', admin_quote(body or '—')]
    if hint:
        parts.extend(['', hint])
    return admin_screen('\n'.join(parts), compact=True)


def _tariff_notice(icon: str, message: str) -> str:
    """Короткое сообщение — жирный заголовок; длинное — заголовок и цитата."""
    if '\n' in message or len(message) > 90:
        return admin_screen(f'{admin_heading(icon, "Внимание")}\n\n{admin_quote(message)}', compact=True)
    return admin_screen(admin_heading(icon, message), compact=True)


def _topup_detail(
    tariff_name: str,
    status: str,
    packages_display: str,
    max_limit_display: str,
    *,
    enabled: bool = True,
) -> str:
    """Экран докупки трафика: статус, пакеты и максимальный лимит."""
    return _tariff_prompt(
        'coin',
        f'Докупка трафика для «{html.escape(tariff_name)}»',
        [
            _tariff_field('check' if enabled else 'prohibit', 'Статус', status),
            f'{admin_icon("coin")} <b>Пакеты:</b>',
            packages_display,
            _tariff_field('stats', 'Макс. лимит', max_limit_display),
        ],
        'Пользователи смогут докупать трафик по заданным ценам.',
    )


def _squads_screen(tariff_name: str, selected: int, total: int) -> str:
    """Экран выбора серверов тарифа."""
    return _tariff_prompt(
        'servers',
        f'Серверы для тарифа «{html.escape(tariff_name)}»',
        [_tariff_field('servers', 'Выбрано', f'<b>{selected} из {total}</b>')],
        'Если не выбран ни один сервер — доступны все.\nНажмите на сервер для выбора/отмены:',
    )


def _promo_groups_screen(tariff_name: str, selected: int) -> str:
    """Экран выбора промогрупп тарифа."""
    return _tariff_prompt(
        'promo',
        f'Промогруппы для тарифа «{html.escape(tariff_name)}»',
        [_tariff_field('promo', 'Выбрано', f'<b>{selected}</b>')],
        'Если не выбрана ни одна группа — тариф доступен всем.\nВыберите группы, которым доступен этот тариф:',
    )


def _reset_mode_screen(tariff_name: str, mode_display: str) -> str:
    """Экран выбора режима сброса трафика."""
    return _tariff_prompt(
        'refresh',
        f'Режим сброса трафика для тарифа «{html.escape(tariff_name)}»',
        [_tariff_field('refresh', 'Текущий режим', mode_display)],
        'Выберите, когда сбрасывать использованный трафик у подписчиков этого тарифа:\n\n'
        '• <b>Глобальная настройка</b> — использовать значение из конфига бота\n'
        '• <b>Ежедневно</b> — сброс каждый день\n'
        '• <b>Еженедельно</b> — сброс каждую неделю\n'
        '• <b>Ежемесячно</b> — сброс каждый месяц\n'
        '• <b>Никогда</b> — трафик накапливается за весь период подписки',
    )


def _tariff_button(
    text: str,
    callback_data: str,
    *,
    icon: str | None = None,
) -> InlineKeyboardButton:
    """Кнопка тарифа: старый глиф из локали убираем, premium-иконка своя."""
    return admin_button(strip_leading_emoji(text).strip(), callback_data=callback_data, icon=icon)


def _parse_period_prices(text: str) -> dict[str, int]:
    """
    Парсит строку с ценами периодов.
    Формат: "30:9900, 90:24900, 180:44900" или "30=9900; 90=24900"
    """
    prices = {}
    text = text.replace(';', ',').replace('=', ':')

    for part in text.split(','):
        part = part.strip()
        if not part:
            continue

        if ':' not in part:
            continue

        period_str, price_str = part.split(':', 1)
        try:
            period = int(period_str.strip())
            price = int(price_str.strip())
            if period > 0 and price >= 0:
                prices[str(period)] = price
        except ValueError:
            continue

    return prices


def _format_period_prices_display(prices: dict[str, int], highlight: int | None = None, language: str = 'ru') -> str:
    """Форматирует цены периодов для отображения; выделенный период отмечен звездой."""
    if not prices:
        return get_texts(language).t('ADMIN_TARIFF_PRICES_NOT_SET', 'Не заданы')

    lines = []
    for period_str in sorted(prices.keys(), key=int):
        period = int(period_str)
        price = prices[period_str]
        mark = ' ★' if highlight is not None and int(highlight) == period else ''
        lines.append(f'• {format_period(period, language=language)}: {format_price_kopeks(price)}{mark}')

    return '\n'.join(lines)


def _format_period_prices_for_edit(prices: dict[str, int]) -> str:
    """Форматирует цены периодов для редактирования."""
    if not prices:
        return '30:9900, 90:24900, 180:44900'

    parts = []
    for period_str in sorted(prices.keys(), key=int):
        parts.append(f'{period_str}:{prices[period_str]}')

    return ', '.join(parts)


def get_tariffs_list_keyboard(
    tariffs: list[tuple[Tariff, int]],
    language: str,
    page: int = 0,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру списка тарифов."""
    texts = get_texts(language)
    buttons = []

    for tariff, subs_count in tariffs:
        buttons.append(
            [
                admin_button(
                    f'{tariff.name} ({subs_count})',
                    callback_data=f'admin_tariff_view:{tariff.id}',
                    icon='tariff',
                )
            ]
        )

    # Пагинация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(admin_button('←', callback_data=f'admin_tariffs_page:{page - 1}'))
    if page < total_pages - 1:
        nav_buttons.append(admin_button('→', callback_data=f'admin_tariffs_page:{page + 1}'))
    if nav_buttons:
        buttons.append(nav_buttons)

    # Кнопка создания
    buttons.append(
        [
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_CREATE', 'Создать тариф'),
                'admin_tariff_create',
                icon='plus',
            )
        ]
    )

    # Кнопка назад
    buttons.append([admin_back_button(texts, 'admin_panel')])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariff_view_keyboard(
    tariff: Tariff,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру просмотра тарифа."""
    texts = get_texts(language)
    buttons = []

    # Редактирование полей
    buttons.append(
        [
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_NAME', 'Название'),
                f'admin_tariff_edit_name:{tariff.id}',
                icon='document',
            ),
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_DESCRIPTION', 'Описание'),
                f'admin_tariff_edit_desc:{tariff.id}',
                icon='document',
            ),
        ]
    )
    buttons.append(
        [
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_TRAFFIC', 'Трафик'),
                f'admin_tariff_edit_traffic:{tariff.id}',
                icon='stats',
            ),
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_DEVICES', 'Устройства'),
                f'admin_tariff_edit_devices:{tariff.id}',
                icon='devices',
            ),
        ]
    )
    # Цены за периоды только для обычных тарифов (не суточных)
    is_daily = getattr(tariff, 'is_daily', False)
    if not is_daily:
        buttons.append(
            [
                _tariff_button(
                    texts.t('ADMIN_TARIFF_BUTTON_PRICES', 'Цены'),
                    f'admin_tariff_edit_prices:{tariff.id}',
                    icon='pricing',
                ),
                _tariff_button(
                    texts.t('ADMIN_TARIFF_BUTTON_TIER', 'Уровень'),
                    f'admin_tariff_edit_tier:{tariff.id}',
                    icon='star',
                ),
            ]
        )
        # Выделять нечего, пока нет ни одного периода.
        if tariff.period_prices:
            buttons.append(
                [
                    _tariff_button(
                        texts.t('ADMIN_TARIFF_BUTTON_FEATURED_PERIOD', 'Выгодный период'),
                        f'admin_tariff_edit_highlight:{tariff.id}',
                        icon='star',
                    ),
                ]
            )
    else:
        buttons.append(
            [
                _tariff_button(
                    texts.t('ADMIN_TARIFF_BUTTON_TIER', 'Уровень'),
                    f'admin_tariff_edit_tier:{tariff.id}',
                    icon='star',
                ),
            ]
        )
    buttons.append(
        [
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_DEVICE_PRICE', 'Цена за устройство'),
                f'admin_tariff_edit_device_price:{tariff.id}',
                icon='money',
            ),
            _tariff_button('Макс. устройств', f'admin_tariff_edit_max_devices:{tariff.id}', icon='devices'),
        ]
    )
    buttons.append(
        [
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_TRIAL_DAYS', 'Дни триала'),
                f'admin_tariff_edit_trial_days:{tariff.id}',
                icon='trials',
            ),
        ]
    )
    buttons.append(
        [
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_CUSTOM_TRAFFIC', 'Произвольный трафик'),
                f'admin_tariff_edit_custom_traffic:{tariff.id}',
                icon='stats',
            ),
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_CUSTOM_DAYS', 'Произвольные дни'),
                f'admin_tariff_edit_custom_days:{tariff.id}',
                icon='calendar',
            ),
        ]
    )
    buttons.append(
        [
            _tariff_button('Докупка трафика', f'admin_tariff_edit_traffic_topup:{tariff.id}', icon='coin'),
        ]
    )
    buttons.append(
        [
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_TRAFFIC_RESET', 'Сброс трафика'),
                f'admin_tariff_edit_reset_mode:{tariff.id}',
                icon='refresh',
            ),
        ]
    )
    buttons.append(
        [
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_SERVERS', 'Серверы'),
                f'admin_tariff_edit_squads:{tariff.id}',
                icon='servers',
            ),
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_PROMO_GROUPS', 'Промогруппы'),
                f'admin_tariff_edit_promo:{tariff.id}',
                icon='promo',
            ),
        ]
    )
    buttons.append(
        [
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_SERVER_LIMITS', 'Лимиты по серверам'),
                f'admin_tariff_edit_server_limits:{tariff.id}',
                icon='servers',
            ),
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_MORE', 'Ещё настройки'),
                f'admin_tariff_edit_more:{tariff.id}',
                icon='settings',
            ),
        ]
    )

    # Суточный режим - только для уже суточных тарифов показываем настройки
    # Новые тарифы делаются суточными только при создании
    if is_daily:
        buttons.append(
            [
                _tariff_button('Суточная цена', f'admin_tariff_edit_daily_price:{tariff.id}', icon='money'),
            ]
        )
        # Примечание: отключение суточного режима убрано - это необратимое решение при создании

    # Переключение триала
    if tariff.is_trial_available:
        buttons.append(
            [
                _tariff_button(
                    texts.t('ADMIN_TARIFF_BUTTON_REMOVE_TRIAL', 'Убрать триал'),
                    f'admin_tariff_toggle_trial:{tariff.id}',
                    icon='trials',
                )
            ]
        )
    else:
        buttons.append(
            [
                _tariff_button(
                    texts.t('ADMIN_TARIFF_BUTTON_SET_TRIAL', 'Сделать триальным'),
                    f'admin_tariff_toggle_trial:{tariff.id}',
                    icon='trials',
                )
            ]
        )

    # Переключение активности
    is_highlighted = getattr(tariff, 'is_highlighted', False)
    highlight_label = 'Снять выделение тарифа' if is_highlighted else 'Выделить тариф'
    buttons.append(
        [
            _tariff_button(
                highlight_label,
                f'admin_tariff_toggle_highlight:{tariff.id}',
                icon='star',
            )
        ]
    )

    if tariff.is_active:
        buttons.append(
            [
                _tariff_button(
                    'Деактивировать',
                    f'admin_tariff_toggle:{tariff.id}',
                    icon='lock',
                )
            ]
        )
    else:
        buttons.append(
            [
                _tariff_button(
                    texts.t('ADMIN_TARIFF_BUTTON_ACTIVATE', 'Активировать'),
                    f'admin_tariff_toggle:{tariff.id}',
                    icon='success',
                )
            ]
        )

    # Удаление
    buttons.append(
        [
            _tariff_button(
                texts.t('ADMIN_TARIFF_BUTTON_DELETE', 'Удалить'),
                f'admin_tariff_delete:{tariff.id}',
                icon='delete',
            )
        ]
    )

    # Назад к списку
    buttons.append([admin_back_button(texts, 'admin_tariffs')])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _format_traffic_reset_mode(mode: str | None, language: str = 'ru') -> str:
    """Форматирует режим сброса трафика для отображения."""
    mode_labels = {
        'DAY': strip_leading_emoji(get_texts(language).t('ADMIN_TARIFF_RESET_DAY', 'Ежедневно')),
        'WEEK': strip_leading_emoji(get_texts(language).t('ADMIN_TARIFF_RESET_WEEK', 'Еженедельно')),
        'MONTH': strip_leading_emoji(get_texts(language).t('ADMIN_TARIFF_RESET_MONTH', 'Ежемесячно')),
        'MONTH_ROLLING': strip_leading_emoji(get_texts(language).t('ADMIN_TARIFF_RESET_ROLLING', 'Скользящий месяц')),
        'NO_RESET': strip_leading_emoji(get_texts(language).t('ADMIN_TARIFF_RESET_NEVER', 'Никогда')),
    }
    if mode is None:
        return (
            get_texts(language)
            .t('ADMIN_TARIFF_GLOBAL_MODE', 'Глобальная настройка ({v0})')
            .format(v0=settings.DEFAULT_TRAFFIC_RESET_STRATEGY)
        )
    return mode_labels.get(
        mode, get_texts(language).t('ADMIN_TARIFF_UNKNOWN_MODE', 'Неизвестно ({v0})').format(v0=mode)
    )


def _format_traffic_topup_packages(tariff: Tariff, language: str = 'ru') -> str:
    """Форматирует пакеты докупки трафика для отображения."""
    if not getattr(tariff, 'traffic_topup_enabled', False):
        return get_texts(language).t('ADMIN_TARIFF_DISABLED_STATE', 'Отключено')

    packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
    if not packages:
        return get_texts(language).t('ADMIN_TARIFF_ENABLED_NO_PACKAGES', 'Включено, но пакеты не настроены')

    lines = [get_texts(language).t('ADMIN_TARIFF_ENABLED_STATE', 'Включено')]
    for gb in sorted(packages.keys()):
        price = packages[gb]
        lines.append(
            get_texts(language)
            .t('ADMIN_TARIFF_PACKAGE_LINE', '• {v0} ГБ: {v1}')
            .format(v0=gb, v1=format_price_kopeks(price))
            .strip()
        )

    return '\n'.join(lines)


def format_tariff_info(tariff: Tariff, language: str, subs_count: int = 0) -> str:
    """Форматирует информацию о тарифе."""
    status = (
        strip_leading_emoji(get_texts(language).t('ADMIN_TARIFF_ACTIVE_STATE', 'Активен'))
        if tariff.is_active
        else strip_leading_emoji(get_texts(language).t('ADMIN_TARIFF_INACTIVE_STATE', 'Неактивен'))
    )
    if getattr(tariff, 'is_highlighted', False):
        status += get_texts(language).t('ADMIN_TARIFF_HIGHLIGHT_MARK', ' ·  выделен')
    traffic = format_traffic(tariff.traffic_limit_gb, language=language)
    prices_display = _format_period_prices_display(
        tariff.period_prices or {}, tariff.highlight_period_days, language=language
    )

    # Форматируем список серверов
    squads_list = tariff.allowed_squads or []
    squads_display = (
        get_texts(language).t('ADMIN_TARIFF_SERVER_COUNT', '{v0} серверов').format(v0=len(squads_list))
        if squads_list
        else get_texts(language).t('ADMIN_TARIFF_ALL_SERVERS', 'Все серверы')
    )

    # Форматируем промогруппы
    promo_groups = tariff.allowed_promo_groups or []
    if promo_groups:
        promo_display = ', '.join(pg.name for pg in promo_groups)
    else:
        promo_display = get_texts(language).t('ADMIN_TARIFF_AVAILABLE_ALL', 'Доступен всем')

    trial_status = (
        get_texts(language).t('ADMIN_TARIFF_YES', 'Да')
        if tariff.is_trial_available
        else get_texts(language).t('ADMIN_TARIFF_NO', 'Нет')
    )

    # Форматируем дни триала
    trial_days = getattr(tariff, 'trial_duration_days', None)
    if trial_days:
        trial_days_display = get_texts(language).t('ADMIN_TARIFF_DAY_COUNT', '{v0} дней').format(v0=trial_days)
    else:
        trial_days_display = (
            get_texts(language)
            .t('ADMIN_TARIFF_DEFAULT_TRIAL_DAYS', 'По умолчанию ({v0} дней)')
            .format(v0=settings.TRIAL_DURATION_DAYS)
        )

    # Форматируем цену за устройство
    device_price = getattr(tariff, 'device_price_kopeks', None)
    if device_price is not None and device_price > 0:
        device_price_display = format_price_kopeks(device_price) + get_texts(language).t(
            'ADMIN_TARIFF_PER_MONTH', '/мес'
        )
    else:
        device_price_display = get_texts(language).t('ADMIN_TARIFF_UNAVAILABLE', 'Недоступно')

    # Форматируем макс. устройств
    max_devices = getattr(tariff, 'max_device_limit', None)
    if max_devices is not None and max_devices > 0:
        max_devices_display = str(max_devices)
    else:
        max_devices_display = get_texts(language).t('ADMIN_TARIFF_UNLIMITED', '∞ (без лимита)')

    # Форматируем произвольный трафик и докупку трафика
    custom_traffic_display = format_custom_traffic_settings(tariff, language=language)
    custom_days_display = format_custom_days_settings(tariff, language=language)
    traffic_topup_display = _format_traffic_topup_packages(tariff, language=language)
    server_limits_display = format_server_limits_summary(tariff, language=language)
    panel_settings_display = format_panel_settings(tariff, language=language)

    # Форматируем режим сброса трафика
    traffic_reset_mode = getattr(tariff, 'traffic_reset_mode', None)
    traffic_reset_display = _format_traffic_reset_mode(traffic_reset_mode, language=language)

    # Форматируем суточный тариф
    is_daily = getattr(tariff, 'is_daily', False)
    daily_price_kopeks = getattr(tariff, 'daily_price_kopeks', 0)

    # Формируем блок цен в зависимости от типа тарифа
    if is_daily:
        price_block = (
            get_texts(language)
            .t('ADMIN_TARIFF_DAILY_PRICE_BLOCK', '<b> Суточная цена:</b> {v0}/день')
            .format(v0=format_price_kopeks(daily_price_kopeks))
        )
        tariff_type = strip_leading_emoji(get_texts(language).t('ADMIN_TARIFF_DAILY_TYPE', 'Суточный'))
    else:
        price_block = (
            get_texts(language).t('ADMIN_TARIFF_PERIOD_PRICE_BLOCK', '<b>Цены:</b>\n{v0}').format(v0=prices_display)
        )
        tariff_type = strip_leading_emoji(get_texts(language).t('ADMIN_TARIFF_PERIOD_TYPE', 'Периодный'))

    extra_block = '\n'.join(
        [
            f'{admin_icon("stats")} <b>Произвольный трафик:</b>',
            custom_traffic_display,
            '',
            f'{admin_icon("calendar")} <b>Произвольные дни:</b>',
            custom_days_display,
            '',
            f'{admin_icon("coin")} <b>Докупка трафика:</b>',
            traffic_topup_display,
            '',
            f'{admin_icon("refresh")} <b>Сброс трафика:</b> {traffic_reset_display}',
            '',
            f'{admin_icon("servers")} <b>Серверы:</b> {squads_display}',
            f'{admin_icon("network")} <b>Лимиты по серверам:</b> {server_limits_display}',
            f'{admin_icon("promo")} <b>Промогруппы:</b> {promo_display}',
            '',
            f'{admin_icon("settings")} <b>Панель и прочее:</b>',
            panel_settings_display,
        ]
    )

    lines = [
        admin_heading('tariff', f'Тариф: {html.escape(tariff.name)}'),
        '',
        admin_quote(
            '\n'.join(
                [
                    _tariff_field(
                        'success' if tariff.is_active else 'prohibit',
                        'Статус',
                        f'{status} · {tariff_type}',
                    ),
                    _tariff_field('star', 'Уровень', str(tariff.tier_level)),
                    _tariff_field('settings', 'Порядок', str(tariff.display_order)),
                ]
            )
        ),
        '',
        f'{admin_icon("chart")} <b>Параметры</b>',
        admin_quote(
            '\n'.join(
                [
                    _tariff_field('chart', 'Трафик', traffic),
                    _tariff_field('devices', 'Устройств', Texts.format_device_limit(tariff.device_limit)),
                    _tariff_field('devices', 'Макс. устройств', max_devices_display),
                    _tariff_field('money', 'Цена за доп. устройство', device_price_display),
                    _tariff_field('trials', 'Триал', trial_status),
                    _tariff_field('calendar', 'Дней триала', trial_days_display),
                ]
            )
        ),
        '',
        admin_quote(strip_leading_emoji(price_block).strip()),
        '',
        f'{admin_icon("settings")} <b>Дополнительно</b>',
        admin_quote(extra_block),
    ]
    if tariff.description:
        lines.extend(['', admin_quote(html.escape(tariff.description))])
    lines.extend(['', f'{admin_icon("users")} <b>Подписок на тарифе:</b> {subs_count}'])

    return admin_screen('\n'.join(lines), compact=True)


@admin_required
@error_handler
async def show_tariffs_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает список тарифов."""
    await state.clear()
    texts = get_texts(db_user.language)

    # Проверяем режим продаж
    if not settings.is_tariffs_mode():
        await callback.message.edit_text(
            _tariff_screen(
                f'{admin_heading("warning", "Режим тарифов отключен")}\n\n'
                + admin_quote(
                    'Для использования тарифов установите:\n<code>SALES_MODE=tariffs</code>\n\n'
                    'Текущий режим: <code>classic</code>'
                ),
                icon='warning',
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[admin_back_button(texts, 'admin_panel')]]),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    tariffs_data = await get_tariffs_with_subscriptions_count(db, include_inactive=True)

    if not tariffs_data:
        await callback.message.edit_text(
            _tariff_screen(
                f'{admin_heading("tariff", "Тарифы")}\n\n'
                + admin_quote('Тарифы ещё не созданы.\nСоздайте первый тариф для начала работы.'),
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _tariff_button(
                            texts.t('ADMIN_TARIFF_BUTTON_CREATE', 'Создать тариф'),
                            'admin_tariff_create',
                            icon='plus',
                        )
                    ],
                    [admin_back_button(texts, 'admin_panel')],
                ]
            ),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    total_pages = (len(tariffs_data) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page_data = tariffs_data[:ITEMS_PER_PAGE]

    total_subs = sum(count for _, count in tariffs_data)
    active_count = sum(1 for t, _ in tariffs_data if t.is_active)

    await callback.message.edit_text(
        _tariff_screen(
            f'{admin_heading("tariff", "Тарифы")}\n\n'
            + admin_quote(
                '\n'.join(
                    [
                        f'{admin_icon("tariff")} Всего: <b>{len(tariffs_data)}</b> (активных: <b>{active_count}</b>)',
                        f'{admin_icon("users")} Подписок на тарифах: <b>{total_subs}</b>',
                    ]
                )
            )
            + '\n\nВыберите тариф для просмотра и редактирования:',
        ),
        reply_markup=get_tariffs_list_keyboard(page_data, db_user.language, 0, total_pages),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_tariffs_page(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Показывает страницу списка тарифов."""
    page = int(callback.data.split(':')[1])

    tariffs_data = await get_tariffs_with_subscriptions_count(db, include_inactive=True)
    total_pages = (len(tariffs_data) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_data = tariffs_data[start_idx:end_idx]

    total_subs = sum(count for _, count in tariffs_data)
    active_count = sum(1 for t, _ in tariffs_data if t.is_active)

    await callback.message.edit_text(
        _tariff_screen(
            f'{admin_heading("tariff", "Тарифы")}\n\n'
            + admin_quote(
                '\n'.join(
                    [
                        f'{admin_icon("document")} Страница: <b>{page + 1}/{total_pages}</b>',
                        f'{admin_icon("tariff")} Всего: <b>{len(tariffs_data)}</b> (активных: <b>{active_count}</b>)',
                        f'{admin_icon("users")} Подписок на тарифах: <b>{total_subs}</b>',
                    ]
                )
            )
            + '\n\nВыберите тариф для просмотра и редактирования:',
        ),
        reply_markup=get_tariffs_list_keyboard(page_data, db_user.language, page, total_pages),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def view_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Просмотр тарифа."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await callback.message.edit_text(
        _tariff_screen(format_tariff_info(tariff, db_user.language, subs_count)),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_tariff_highlight(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Отмечает тариф как выгодный — в списке тарифов он показывается с подписью."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    tariff = await update_tariff(db, tariff, is_highlighted=not getattr(tariff, 'is_highlighted', False))
    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await callback.answer(
        get_texts(db_user.language).t('ADMIN_TARIFF_HIGHLIGHTED', 'Тариф выделен')
        if tariff.is_highlighted
        else get_texts(db_user.language).t('ADMIN_TARIFF_UNHIGHLIGHTED', 'Выделение снято'),
        show_alert=True,
    )
    await callback.message.edit_text(
        _tariff_screen(format_tariff_info(tariff, db_user.language, subs_count)),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def toggle_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает активность тарифа."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    tariff = await update_tariff(db, tariff, is_active=not tariff.is_active)
    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    status = (
        get_texts(db_user.language).t('ADMIN_TARIFF_ACTIVATED', 'активирован')
        if tariff.is_active
        else get_texts(db_user.language).t('ADMIN_TARIFF_DEACTIVATED', 'деактивирован')
    )
    await callback.answer(
        get_texts(db_user.language).t('ADMIN_TARIFF_STATUS_CHANGED', 'Тариф {v0}').format(v0=status), show_alert=True
    )

    await callback.message.edit_text(
        _tariff_screen(format_tariff_info(tariff, db_user.language, subs_count)),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def toggle_trial_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает тариф как триальный."""
    from app.database.crud.tariff import clear_trial_tariff, set_trial_tariff

    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    if tariff.is_trial_available:
        # Снимаем флаг триала
        await clear_trial_tariff(db)
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_TRIAL_REMOVED', 'Триал снят с тарифа'), show_alert=True
        )
    else:
        # Устанавливаем этот тариф как триальный (снимает флаг с других)
        await set_trial_tariff(db, tariff_id)
        await callback.answer(
            get_texts(db_user.language)
            .t('ADMIN_TARIFF_TRIAL_ASSIGNED', 'Тариф «{v0}» установлен как триальный')
            .format(v0=tariff.name),
            show_alert=True,
        )

    # Перезагружаем тариф
    tariff = await get_tariff_by_id(db, tariff_id)
    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await callback.message.edit_text(
        _tariff_screen(format_tariff_info(tariff, db_user.language, subs_count)),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def toggle_daily_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает суточный режим тарифа."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    is_daily = getattr(tariff, 'is_daily', False)

    if is_daily:
        # Отключаем суточный режим
        tariff = await update_tariff(db, tariff, is_daily=False, daily_price_kopeks=0)
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_DAILY_DISABLED', 'Суточный режим отключен'), show_alert=True
        )
    else:
        # Включаем суточный режим (с ценой по умолчанию)
        tariff = await update_tariff(db, tariff, is_daily=True, daily_price_kopeks=5000)  # 50 руб по умолчанию
        await callback.answer(
            get_texts(db_user.language).t(
                'ADMIN_TARIFF_DAILY_ENABLED_DEFAULT',
                'Суточный режим включен. Цена: 50 ₽/день\nНастройте цену через кнопку « Суточная цена»',
            ),
            show_alert=True,
        )

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await callback.message.edit_text(
        _tariff_screen(format_tariff_info(tariff, db_user.language, subs_count)),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_daily_price(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование суточной цены."""
    texts = get_texts(db_user.language)

    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    current_price = getattr(tariff, 'daily_price_kopeks', 0)

    await state.set_state(AdminStates.editing_tariff_daily_price)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    await callback.message.edit_text(
        _tariff_prompt(
            'money',
            'Редактирование суточной цены',
            [
                _tariff_field('tariff', 'Тариф', html.escape(tariff.name)),
                _tariff_field('money', 'Текущая цена', f'{format_price_kopeks(current_price)}/день'),
            ],
            'Введите новую цену за день в рублях.\nПример: <code>50</code> или <code>99.90</code>',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, f'admin_tariff_view:{tariff_id}', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_daily_price_input(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает ввод суточной цены (создание и редактирование)."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    # Парсим цену
    try:
        price_rubles = float(message.text.strip().replace(',', '.'))
        if price_rubles <= 0:
            raise ValueError(
                get_texts(db_user.language).t('ADMIN_TARIFF_POSITIVE_PRICE', 'Цена должна быть положительной')
            )

        price_kopeks = int(price_rubles * 100)
    except ValueError:
        await message.answer(
            _tariff_notice(
                'warning',
                'Некорректная цена. Введите положительное число.\nПример: <code>50</code> или <code>99.90</code>',
            ),
            parse_mode='HTML',
        )
        return

    # Проверяем - это создание или редактирование
    is_creating = data.get('tariff_is_daily') and not tariff_id

    if is_creating:
        # Создаем новый суточный тариф
        tariff = await create_tariff(
            db,
            name=data['tariff_name'],
            traffic_limit_gb=data['tariff_traffic'],
            device_limit=data['tariff_devices'],
            tier_level=data['tariff_tier'],
            period_prices={},
            is_active=True,
            is_daily=True,
            daily_price_kopeks=price_kopeks,
        )
        await state.clear()

        await message.answer(
            _tariff_screen(
                f'{admin_heading("success", "Суточный тариф создан")}\n\n'
                + format_tariff_info(tariff, db_user.language, 0),
            ),
            reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
            parse_mode='HTML',
        )
    else:
        # Редактируем существующий тариф
        if not tariff_id:
            await state.clear()
            return

        tariff = await get_tariff_by_id(db, tariff_id)
        if not tariff:
            await message.answer(
                _tariff_notice('warning', 'Тариф не найден'),
                parse_mode='HTML',
            )
            await state.clear()
            return

        tariff = await update_tariff(db, tariff, daily_price_kopeks=price_kopeks)
        await state.clear()

        subs_count = await get_tariff_subscriptions_count(db, tariff_id)

        await message.answer(
            _tariff_screen(
                f'{admin_heading("success", f"Суточная цена установлена: {format_price_kopeks(price_kopeks)}/день")}\n\n'
                + format_tariff_info(tariff, db_user.language, subs_count),
            ),
            reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
            parse_mode='HTML',
        )


# ============ СОЗДАНИЕ ТАРИФА ============


@admin_required
@error_handler
async def start_create_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает создание тарифа."""
    texts = get_texts(db_user.language)

    await state.set_state(AdminStates.creating_tariff_name)
    await state.update_data(language=db_user.language)

    await callback.message.edit_text(
        _tariff_prompt(
            'plus',
            'Создание тарифа',
            [f'{admin_icon("document")} Шаг 1/6'],
            'Введите название тарифа\n\nПример: <i>Базовый</i>, <i>Премиум</i>, <i>Бизнес</i>',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, 'admin_tariffs', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_tariff_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает название тарифа."""
    texts = get_texts(db_user.language)
    name = message.text.strip()

    if len(name) < 2:
        await message.answer(
            _tariff_notice('warning', 'Название должно быть не короче 2 символов'),
            parse_mode='HTML',
        )
        return

    if len(name) > 50:
        await message.answer(
            _tariff_notice('warning', 'Название должно быть не длиннее 50 символов'),
            parse_mode='HTML',
        )
        return

    await state.update_data(tariff_name=name)
    await state.set_state(AdminStates.creating_tariff_traffic)

    await message.answer(
        _tariff_prompt(
            'tariff',
            'Создание тарифа',
            [
                _tariff_field('edit', 'Название', f'<b>{html.escape(name)}</b>'),
                f'{admin_icon("document")} Шаг 2/6',
            ],
            'Введите лимит трафика в ГБ\n\nВведите <code>0</code> для безлимитного трафика\n'
            'Пример: <i>100</i>, <i>500</i>, <i>0</i>',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, 'admin_tariffs', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def process_tariff_traffic(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает лимит трафика."""
    texts = get_texts(db_user.language)

    try:
        traffic = int(message.text.strip())
        if traffic < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            _tariff_notice('warning', 'Введите корректное число (0 или больше)'),
            parse_mode='HTML',
        )
        return

    data = await state.get_data()
    await state.update_data(tariff_traffic=traffic)
    await state.set_state(AdminStates.creating_tariff_devices)

    traffic_display = format_traffic(traffic, language=db_user.language)

    await message.answer(
        _tariff_prompt(
            'tariff',
            'Создание тарифа',
            [
                _tariff_field('edit', 'Название', f'<b>{html.escape(data["tariff_name"])}</b>'),
                _tariff_field('chart', 'Трафик', f'<b>{traffic_display}</b>'),
                f'{admin_icon("document")} Шаг 3/6',
            ],
            'Введите лимит устройств\n\nПример: <i>1</i>, <i>3</i>, <i>5</i>',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, 'admin_tariffs', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def process_tariff_devices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает лимит устройств."""
    texts = get_texts(db_user.language)

    try:
        devices = int(message.text.strip())
        if devices < 1:
            raise ValueError
    except ValueError:
        await message.answer(
            _tariff_notice('warning', 'Введите корректное число (1 или больше)'),
            parse_mode='HTML',
        )
        return

    data = await state.get_data()
    await state.update_data(tariff_devices=devices)
    await state.set_state(AdminStates.creating_tariff_tier)

    traffic_display = format_traffic(data['tariff_traffic'], language=db_user.language)

    await message.answer(
        _tariff_prompt(
            'tariff',
            'Создание тарифа',
            [
                _tariff_field('edit', 'Название', f'<b>{html.escape(data["tariff_name"])}</b>'),
                _tariff_field('chart', 'Трафик', f'<b>{traffic_display}</b>'),
                _tariff_field('devices', 'Устройств', f'<b>{devices}</b>'),
                f'{admin_icon("document")} Шаг 4/6',
            ],
            'Введите уровень тарифа (1-10)\n\nУровень используется для визуального отображения\n'
            '1 - базовый, 10 - максимальный\nПример: <i>1</i>, <i>2</i>, <i>3</i>',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, 'admin_tariffs', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def process_tariff_tier(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает уровень тарифа."""
    texts = get_texts(db_user.language)

    try:
        tier = int(message.text.strip())
        if tier < 1 or tier > 10:
            raise ValueError
    except ValueError:
        await message.answer(
            _tariff_notice('warning', 'Введите число от 1 до 10'),
            parse_mode='HTML',
        )
        return

    data = await state.get_data()
    await state.update_data(tariff_tier=tier)

    traffic_display = format_traffic(data['tariff_traffic'], language=db_user.language)

    # Шаг 5/6: Выбор типа тарифа
    await message.answer(
        _tariff_prompt(
            'tariff',
            'Создание тарифа',
            [
                _tariff_field('edit', 'Название', f'<b>{html.escape(data["tariff_name"])}</b>'),
                _tariff_field('chart', 'Трафик', f'<b>{traffic_display}</b>'),
                _tariff_field('devices', 'Устройств', f'<b>{data["tariff_devices"]}</b>'),
                _tariff_field('star', 'Уровень', f'<b>{tier}</b>'),
                f'{admin_icon("document")} Шаг 5/6',
            ],
            'Выберите тип тарифа:',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _tariff_button(
                        get_texts(db_user.language).t('ADMIN_TARIFF_PERIOD_TYPE_CHOICE', 'Периодный (месяцы)'),
                        'tariff_type_periodic',
                        icon='calendar',
                    )
                ],
                [
                    _tariff_button(
                        get_texts(db_user.language).t('ADMIN_TARIFF_DAILY_TYPE_CHOICE', 'Суточный (оплата за день)'),
                        'tariff_type_daily',
                        icon='calendar',
                    )
                ],
                [admin_back_button(texts, 'admin_tariffs', label=texts.CANCEL)],
            ]
        ),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def select_tariff_type_periodic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Выбирает периодный тип тарифа."""
    texts = get_texts(db_user.language)
    data = await state.get_data()

    await state.update_data(tariff_is_daily=False)
    await state.set_state(AdminStates.creating_tariff_prices)

    traffic_display = format_traffic(data['tariff_traffic'], language=db_user.language)

    await callback.message.edit_text(
        _tariff_prompt(
            'pricing',
            'Создание тарифа',
            [
                _tariff_field('edit', 'Название', f'<b>{html.escape(data["tariff_name"])}</b>'),
                _tariff_field('chart', 'Трафик', f'<b>{traffic_display}</b>'),
                _tariff_field('devices', 'Устройств', f'<b>{data["tariff_devices"]}</b>'),
                _tariff_field('star', 'Уровень', f'<b>{data["tariff_tier"]}</b>'),
                _tariff_field('calendar', 'Тип', '<b>Периодный</b>'),
                f'{admin_icon("document")} Шаг 6/6',
            ],
            'Введите цены на периоды\n\nФормат: <code>дней:цена_в_копейках</code>\n'
            'Несколько периодов через запятую\n\nПример:\n<code>30:9900, 90:24900, 180:44900, 360:79900</code>',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, 'admin_tariffs', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def select_tariff_type_daily(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Выбирает суточный тип тарифа."""
    from app.states import AdminStates

    texts = get_texts(db_user.language)
    data = await state.get_data()

    await state.update_data(tariff_is_daily=True)
    await state.set_state(AdminStates.editing_tariff_daily_price)

    traffic_display = format_traffic(data['tariff_traffic'], language=db_user.language)

    await callback.message.edit_text(
        _tariff_prompt(
            'money',
            'Создание суточного тарифа',
            [
                _tariff_field('edit', 'Название', f'<b>{html.escape(data["tariff_name"])}</b>'),
                _tariff_field('chart', 'Трафик', f'<b>{traffic_display}</b>'),
                _tariff_field('devices', 'Устройств', f'<b>{data["tariff_devices"]}</b>'),
                _tariff_field('star', 'Уровень', f'<b>{data["tariff_tier"]}</b>'),
                _tariff_field('calendar', 'Тип', '<b>Суточный</b>'),
                f'{admin_icon("document")} Шаг 6/6',
            ],
            'Введите суточную цену в рублях\n\nПример: <i>50</i> (50 ₽/день), <i>99.90</i> (99.90 ₽/день)',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, 'admin_tariffs', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_tariff_prices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает цены тарифа."""
    prices = _parse_period_prices(message.text.strip())

    if not prices:
        await message.answer(
            _tariff_notice(
                'warning',
                'Не удалось распознать цены.\n\nФормат: <code>дней:цена_в_копейках</code>\n'
                'Пример: <code>30:9900, 90:24900</code>',
            ),
            parse_mode='HTML',
        )
        return

    data = await state.get_data()
    await state.update_data(tariff_prices=prices)

    # Создаем тариф
    tariff = await create_tariff(
        db,
        name=data['tariff_name'],
        traffic_limit_gb=data['tariff_traffic'],
        device_limit=data['tariff_devices'],
        tier_level=data['tariff_tier'],
        period_prices=prices,
        is_active=True,
    )

    await state.clear()

    subs_count = 0

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Тариф создан")}\n\n'
            + format_tariff_info(tariff, db_user.language, subs_count),
        ),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


# ============ РЕДАКТИРОВАНИЕ ТАРИФА ============


@admin_required
@error_handler
async def start_edit_tariff_name(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование названия тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await state.set_state(AdminStates.editing_tariff_name)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    await callback.message.edit_text(
        _tariff_prompt(
            'edit',
            'Редактирование названия',
            [_tariff_field('edit', 'Текущее название', f'<b>{html.escape(tariff.name)}</b>')],
            'Введите новое название:',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, f'admin_tariff_view:{tariff_id}', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое название тарифа."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer(
            _tariff_notice('warning', 'Тариф не найден'),
            parse_mode='HTML',
        )
        await state.clear()
        return

    name = message.text.strip()
    if len(name) < 2 or len(name) > 50:
        await message.answer(
            _tariff_notice('warning', 'Название должно быть от 2 до 50 символов'),
            parse_mode='HTML',
        )
        return

    tariff = await update_tariff(db, tariff, name=name)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Название изменено")}\n\n'
            + format_tariff_info(tariff, db_user.language, subs_count),
        ),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_tariff_description(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование описания тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await state.set_state(AdminStates.editing_tariff_description)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    current_desc = tariff.description or get_texts(db_user.language).t('ADMIN_TARIFF_DESCRIPTION_NOT_SET', 'Не задано')

    await callback.message.edit_text(
        _tariff_prompt(
            'document',
            'Редактирование описания',
            [
                _tariff_field(
                    'document', 'Текущее описание', html.escape(current_desc) if tariff.description else current_desc
                )
            ],
            'Введите новое описание (или <code>-</code> для удаления):',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, f'admin_tariff_view:{tariff_id}', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_description(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое описание тарифа."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer(
            _tariff_notice('warning', 'Тариф не найден'),
            parse_mode='HTML',
        )
        await state.clear()
        return

    description = message.text.strip()
    if description == '-':
        description = None

    tariff = await update_tariff(db, tariff, description=description)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Описание изменено")}\n\n'
            + format_tariff_info(tariff, db_user.language, subs_count),
        ),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_tariff_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование трафика тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await state.set_state(AdminStates.editing_tariff_traffic)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    current_traffic = format_traffic(tariff.traffic_limit_gb, language=db_user.language)

    await callback.message.edit_text(
        _tariff_prompt(
            'stats',
            'Редактирование трафика',
            [_tariff_field('chart', 'Текущий лимит', f'<b>{current_traffic}</b>')],
            'Введите новый лимит в ГБ (0 = безлимит):',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, f'admin_tariff_view:{tariff_id}', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_traffic(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новый лимит трафика."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer(
            _tariff_notice('warning', 'Тариф не найден'),
            parse_mode='HTML',
        )
        await state.clear()
        return

    try:
        traffic = int(message.text.strip())
        if traffic < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            _tariff_notice('warning', 'Введите корректное число (0 или больше)'),
            parse_mode='HTML',
        )
        return

    tariff = await update_tariff(db, tariff, traffic_limit_gb=traffic)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Трафик изменён")}\n\n'
            + format_tariff_info(tariff, db_user.language, subs_count),
        ),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_tariff_devices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование лимита устройств."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await state.set_state(AdminStates.editing_tariff_devices)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    await callback.message.edit_text(
        _tariff_prompt(
            'devices',
            'Редактирование устройств',
            [_tariff_field('devices', 'Текущий лимит', f'<b>{tariff.device_limit}</b>')],
            'Введите новый лимит устройств:',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, f'admin_tariff_view:{tariff_id}', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_devices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новый лимит устройств."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer(
            _tariff_notice('warning', 'Тариф не найден'),
            parse_mode='HTML',
        )
        await state.clear()
        return

    try:
        devices = int(message.text.strip())
        if devices < 1:
            raise ValueError
    except ValueError:
        await message.answer(
            _tariff_notice('warning', 'Введите корректное число (1 или больше)'),
            parse_mode='HTML',
        )
        return

    tariff = await update_tariff(db, tariff, device_limit=devices)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Лимит устройств изменён")}\n\n'
            + format_tariff_info(tariff, db_user.language, subs_count),
        ),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_tariff_tier(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование уровня тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await state.set_state(AdminStates.editing_tariff_tier)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    await callback.message.edit_text(
        _tariff_prompt(
            'star',
            'Редактирование уровня',
            [_tariff_field('star', 'Текущий уровень', f'<b>{tariff.tier_level}</b>')],
            'Введите новый уровень (1-10):',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, f'admin_tariff_view:{tariff_id}', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_tier(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новый уровень тарифа."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer(
            _tariff_notice('warning', 'Тариф не найден'),
            parse_mode='HTML',
        )
        await state.clear()
        return

    try:
        tier = int(message.text.strip())
        if tier < 1 or tier > 10:
            raise ValueError
    except ValueError:
        await message.answer(
            _tariff_notice('warning', 'Введите число от 1 до 10'),
            parse_mode='HTML',
        )
        return

    tariff = await update_tariff(db, tariff, tier_level=tier)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Уровень изменён")}\n\n'
            + format_tariff_info(tariff, db_user.language, subs_count),
        ),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_tariff_prices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование цен тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await state.set_state(AdminStates.editing_tariff_prices)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    current_prices = _format_period_prices_for_edit(tariff.period_prices or {})
    prices_display = _format_period_prices_display(tariff.period_prices or {}, language=db_user.language)

    await callback.message.edit_text(
        _tariff_prompt(
            'pricing',
            'Редактирование цен',
            [
                f'{admin_icon("pricing")} <b>Текущие цены:</b>',
                prices_display,
            ],
            f'Введите новые цены в формате:\n<code>{current_prices}</code>\n\n(дней:цена_в_копейках, через запятую)',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, f'admin_tariff_view:{tariff_id}', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_prices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новые цены тарифа."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer(
            _tariff_notice('warning', 'Тариф не найден'),
            parse_mode='HTML',
        )
        await state.clear()
        return

    prices = _parse_period_prices(message.text.strip())
    if not prices:
        await message.answer(
            _tariff_notice(
                'warning',
                'Не удалось распознать цены.\nФормат: <code>дней:цена</code>\nПример: <code>30:9900, 90:24900</code>',
            ),
            parse_mode='HTML',
        )
        return

    tariff = await update_tariff(db, tariff, period_prices=prices)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Цены изменены")}\n\n'
            + format_tariff_info(tariff, db_user.language, subs_count),
        ),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


# ============ ВЫГОДНЫЙ ПЕРИОД ============


def _highlight_keyboard(tariff: Tariff, language: str) -> InlineKeyboardMarkup:
    """Список периодов тарифа: текущий выделенный отмечен звездой."""
    texts = get_texts(language)
    prices = tariff.period_prices or {}
    current = tariff.highlight_period_days
    buttons = []

    for period_str in sorted(prices.keys(), key=int):
        period = int(period_str)
        mark = '★ ' if current is not None and int(current) == period else ''
        buttons.append(
            [
                admin_button(
                    f'{mark}{format_period(period, language=language)} — {format_price_kopeks(prices[period_str])}',
                    callback_data=f'admin_tariff_highlight_set:{tariff.id}:{period}',
                    icon='star',
                )
            ]
        )

    if current is not None:
        buttons.append(
            [
                admin_button(
                    'Убрать выделение',
                    callback_data=f'admin_tariff_highlight_set:{tariff.id}:0',
                    icon='delete',
                )
            ]
        )

    buttons.append([admin_back_button(texts, f'admin_tariff_view:{tariff.id}')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@admin_required
@error_handler
async def start_edit_tariff_highlight(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Показывает выбор периода, который будет отмечен как самый выгодный."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    current = tariff.highlight_period_days
    current_display = (
        format_period(int(current), language=db_user.language)
        if current is not None
        else get_texts(db_user.language).t('ADMIN_TARIFF_PERIOD_NOT_SELECTED', 'не выбран')
    )

    await callback.message.edit_text(
        _tariff_prompt(
            'star',
            'Выгодный период',
            [_tariff_field('star', 'Сейчас выделен', f'<b>{current_display}</b>')],
            'Выделенный период показывается клиенту с отметкой при покупке, продлении и смене тарифа. '
            'Выделить можно только один период.',
        ),
        reply_markup=_highlight_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def set_tariff_highlight(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Сохраняет выделенный период. 0 — снять выделение."""
    _, tariff_id_raw, period_raw = callback.data.split(':')
    tariff = await get_tariff_by_id(db, int(tariff_id_raw))

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    period = int(period_raw)
    # Повторное нажатие на уже выделенный период снимает выделение: иначе снять
    # его можно было бы только отдельной кнопкой, которой легко не заметить.
    if period == 0 or (tariff.highlight_period_days is not None and int(tariff.highlight_period_days) == period):
        new_value = None
    else:
        new_value = period

    tariff = await update_tariff(db, tariff, highlight_period_days=new_value)
    subs_count = await get_tariff_subscriptions_count(db, tariff.id)

    confirmation = (
        get_texts(db_user.language).t('ADMIN_TARIFF_HIGHLIGHT_REMOVED', 'Выделение снято')
        if new_value is None
        else get_texts(db_user.language)
        .t('ADMIN_TARIFF_PERIOD_HIGHLIGHTED', 'Выделен период: {v0}')
        .format(v0=format_period(new_value, language=db_user.language))
    )

    await callback.message.edit_text(
        _tariff_screen(
            f'{admin_heading("success", confirmation)}\n\n' + format_tariff_info(tariff, db_user.language, subs_count),
        ),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


# ============ РЕДАКТИРОВАНИЕ ЦЕНЫ ЗА УСТРОЙСТВО ============


@admin_required
@error_handler
async def start_edit_tariff_device_price(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование цены за устройство."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await state.set_state(AdminStates.editing_tariff_device_price)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    device_price = getattr(tariff, 'device_price_kopeks', None)
    if device_price is not None and device_price > 0:
        current_price = format_price_kopeks(device_price) + get_texts(db_user.language).t(
            'ADMIN_TARIFF_PER_MONTH', '/мес'
        )
    else:
        current_price = get_texts(db_user.language).t(
            'ADMIN_TARIFF_DEVICES_UNAVAILABLE', 'Недоступно (докупка устройств запрещена)'
        )

    await callback.message.edit_text(
        _tariff_prompt(
            'money',
            'Редактирование цены за устройство',
            [_tariff_field('money', 'Текущая цена', f'<b>{current_price}</b>')],
            'Введите цену в копейках за одно устройство в месяц.\n\n'
            '• <code>0</code> или <code>-</code> — докупка устройств недоступна\n'
            '• Например: <code>5000</code> = 50₽/мес за устройство',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, f'admin_tariff_view:{tariff_id}', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_device_price(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новую цену за устройство."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer(
            _tariff_notice('warning', 'Тариф не найден'),
            parse_mode='HTML',
        )
        await state.clear()
        return

    text = message.text.strip()

    if text == '-' or text == '0':
        device_price = None
    else:
        try:
            device_price = int(text)
            if device_price < 0:
                raise ValueError
        except ValueError:
            await message.answer(
                _tariff_notice(
                    'warning',
                    'Введите корректное число (0 или больше).\n'
                    'Для отключения докупки введите <code>0</code> или <code>-</code>',
                ),
                parse_mode='HTML',
            )
            return

    tariff = await update_tariff(db, tariff, device_price_kopeks=device_price)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Цена за устройство изменена")}\n\n'
            + format_tariff_info(tariff, db_user.language, subs_count),
        ),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


# ============ РЕДАКТИРОВАНИЕ МАКС. УСТРОЙСТВ ============


@admin_required
@error_handler
async def start_edit_tariff_max_devices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование макс. устройств."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await state.set_state(AdminStates.editing_tariff_max_devices)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    max_devices = getattr(tariff, 'max_device_limit', None)
    if max_devices is not None and max_devices > 0:
        current_max = str(max_devices)
    else:
        current_max = get_texts(db_user.language).t('ADMIN_TARIFF_UNLIMITED', '∞ (без лимита)')

    await callback.message.edit_text(
        _tariff_prompt(
            'devices',
            'Редактирование макс. устройств',
            [
                _tariff_field('devices', 'Текущее значение', f'<b>{current_max}</b>'),
                _tariff_field('devices', 'Базовое кол-во устройств', f'<b>{tariff.device_limit}</b>'),
            ],
            'Введите максимальное количество устройств, которое пользователь может докупить.\n\n'
            '• <code>0</code> или <code>-</code> — без ограничений\n'
            '• Например: <code>5</code> = максимум 5 устройств на тарифе',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, f'admin_tariff_view:{tariff_id}', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_max_devices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое макс. кол-во устройств."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer(
            _tariff_notice('warning', 'Тариф не найден'),
            parse_mode='HTML',
        )
        await state.clear()
        return

    text = message.text.strip()

    if text == '-' or text == '0':
        max_devices = None
    else:
        try:
            max_devices = int(text)
            if max_devices < 1:
                raise ValueError
        except ValueError:
            await message.answer(
                _tariff_notice(
                    'warning',
                    'Введите корректное число (1 или больше).\n'
                    'Для снятия ограничения введите <code>0</code> или <code>-</code>',
                ),
                parse_mode='HTML',
            )
            return

    tariff = await update_tariff(db, tariff, max_device_limit=max_devices)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Макс. устройств изменено")}\n\n'
            + format_tariff_info(tariff, db_user.language, subs_count),
        ),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


# ============ РЕДАКТИРОВАНИЕ ДНЕЙ ТРИАЛА ============


@admin_required
@error_handler
async def start_edit_tariff_trial_days(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование дней триала."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await state.set_state(AdminStates.editing_tariff_trial_days)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    trial_days = getattr(tariff, 'trial_duration_days', None)
    if trial_days:
        current_days = get_texts(db_user.language).t('ADMIN_TARIFF_DAY_COUNT', '{v0} дней').format(v0=trial_days)
    else:
        current_days = (
            get_texts(db_user.language)
            .t('ADMIN_TARIFF_DEFAULT_TRIAL_DAYS', 'По умолчанию ({v0} дней)')
            .format(v0=settings.TRIAL_DURATION_DAYS)
        )

    await callback.message.edit_text(
        _tariff_prompt(
            'trials',
            'Редактирование дней триала',
            [_tariff_field('calendar', 'Текущее значение', f'<b>{current_days}</b>')],
            'Введите количество дней триала.\n\n'
            f'• <code>0</code> или <code>-</code> — использовать настройку по умолчанию '
            f'({settings.TRIAL_DURATION_DAYS} дней)\n'
            '• Например: <code>7</code> = 7 дней триала',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[admin_back_button(texts, f'admin_tariff_view:{tariff_id}', label=texts.CANCEL)]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_trial_days(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое количество дней триала."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer(
            _tariff_notice('warning', 'Тариф не найден'),
            parse_mode='HTML',
        )
        await state.clear()
        return

    text = message.text.strip()

    if text == '-' or text == '0':
        trial_days = None
    else:
        try:
            trial_days = int(text)
            if trial_days < 1:
                raise ValueError
        except ValueError:
            await message.answer(
                _tariff_notice(
                    'warning',
                    'Введите корректное число дней (1 или больше).\n'
                    'Для использования настройки по умолчанию введите <code>0</code> или <code>-</code>',
                ),
                parse_mode='HTML',
            )
            return

    tariff = await update_tariff(db, tariff, trial_duration_days=trial_days)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Дни триала изменены")}\n\n'
            + format_tariff_info(tariff, db_user.language, subs_count),
        ),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


# ============ РЕДАКТИРОВАНИЕ ДОКУПКИ ТРАФИКА ============


def _parse_traffic_topup_packages(text: str) -> dict[int, int]:
    """
    Парсит строку с пакетами докупки трафика.
    Формат: "5:5000, 10:9000, 20:15000" (ГБ:цена_в_копейках)
    """
    packages = {}
    text = text.replace(';', ',').replace('=', ':')

    for part in text.split(','):
        part = part.strip()
        if not part:
            continue

        if ':' not in part:
            continue

        gb_str, price_str = part.split(':', 1)
        try:
            gb = int(gb_str.strip())
            price = int(price_str.strip())
            if gb > 0 and price > 0:
                packages[gb] = price
        except ValueError:
            continue

    return packages


def _format_traffic_topup_packages_for_edit(packages: dict[int, int]) -> str:
    """Форматирует пакеты докупки для редактирования."""
    if not packages:
        return '5:5000, 10:9000, 20:15000'

    parts = []
    for gb in sorted(packages.keys()):
        parts.append(f'{gb}:{packages[gb]}')

    return ', '.join(parts)


@admin_required
@error_handler
async def start_edit_tariff_traffic_topup(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает меню настройки докупки трафика."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    # Проверяем, безлимитный ли тариф
    if tariff.is_unlimited_traffic:
        await callback.answer(
            get_texts(db_user.language).t(
                'ADMIN_TARIFF_TOPUP_UNLIMITED_UNAVAILABLE', 'Докупка недоступна для безлимитного тарифа'
            ),
            show_alert=True,
        )
        return

    is_enabled = getattr(tariff, 'traffic_topup_enabled', False)
    packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
    max_topup_traffic = getattr(tariff, 'max_topup_traffic_gb', 0) or 0

    # Форматируем текущие настройки
    if is_enabled:
        status = get_texts(db_user.language).t('ADMIN_TARIFF_ENABLED_STATE', 'Включено')
        if packages:
            packages_display = '\n'.join(
                get_texts(db_user.language)
                .t('ADMIN_TARIFF_PACKAGE_LINE', '• {v0} ГБ: {v1}')
                .format(v0=gb, v1=format_price_kopeks(price))
                .strip()
                for gb, price in sorted(packages.items())
            )
        else:
            packages_display = (
                get_texts(db_user.language).t('ADMIN_TARIFF_PACKAGES_NOT_CONFIGURED', 'Пакеты не настроены').strip()
            )
    else:
        status = get_texts(db_user.language).t('ADMIN_TARIFF_DISABLED_STATE', 'Отключено')
        packages_display = '—'

    # Форматируем лимит
    if max_topup_traffic > 0:
        max_limit_display = (
            get_texts(db_user.language).t('ADMIN_TARIFF_TRAFFIC_VALUE', '{v0} ГБ').format(v0=max_topup_traffic)
        )
    else:
        max_limit_display = get_texts(db_user.language).t('ADMIN_TARIFF_NO_LIMIT', 'Без ограничений')

    buttons = []

    # Переключение вкл/выкл
    if is_enabled:
        buttons.append(
            [
                _tariff_button(
                    get_texts(db_user.language).t('ADMIN_TARIFF_DISABLE', 'Отключить'),
                    f'admin_tariff_toggle_traffic_topup:{tariff_id}',
                    icon='lock',
                )
            ]
        )
    else:
        buttons.append(
            [
                _tariff_button(
                    get_texts(db_user.language).t('ADMIN_TARIFF_ENABLE', 'Включить'),
                    f'admin_tariff_toggle_traffic_topup:{tariff_id}',
                    icon='success',
                )
            ]
        )

    # Редактирование пакетов и лимита (только если включено)
    if is_enabled:
        buttons.append(
            [
                _tariff_button(
                    get_texts(db_user.language).t('ADMIN_TARIFF_CONFIGURE_PACKAGES', 'Настроить пакеты'),
                    f'admin_tariff_edit_topup_packages:{tariff_id}',
                    icon='settings',
                )
            ]
        )
        buttons.append(
            [
                _tariff_button(
                    get_texts(db_user.language).t('ADMIN_TARIFF_MAX_TRAFFIC', 'Макс. лимит трафика'),
                    f'admin_tariff_edit_max_topup:{tariff_id}',
                    icon='stats',
                )
            ]
        )

    buttons.append([admin_back_button(texts, f'admin_tariff_view:{tariff_id}')])

    await callback.message.edit_text(
        _topup_detail(
            tariff.name,
            status,
            packages_display,
            max_limit_display,
            enabled=is_enabled,
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_tariff_traffic_topup(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает включение/выключение докупки трафика."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    is_enabled = getattr(tariff, 'traffic_topup_enabled', False)
    new_value = not is_enabled

    tariff = await update_tariff(db, tariff, traffic_topup_enabled=new_value)

    status_text = (
        get_texts(db_user.language).t('ADMIN_TARIFF_TOPUP_ENABLED', 'включена')
        if new_value
        else get_texts(db_user.language).t('ADMIN_TARIFF_TOPUP_DISABLED', 'отключена')
    )
    await callback.answer(
        get_texts(db_user.language)
        .t('ADMIN_TARIFF_TOPUP_STATUS_CHANGED', 'Докупка трафика {v0}')
        .format(v0=status_text)
    )

    # Перерисовываем меню
    texts = get_texts(db_user.language)
    packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
    max_topup_traffic = getattr(tariff, 'max_topup_traffic_gb', 0) or 0

    if new_value:
        status = get_texts(db_user.language).t('ADMIN_TARIFF_ENABLED_STATE', 'Включено')
        if packages:
            packages_display = '\n'.join(
                get_texts(db_user.language)
                .t('ADMIN_TARIFF_PACKAGE_LINE', '• {v0} ГБ: {v1}')
                .format(v0=gb, v1=format_price_kopeks(price))
                .strip()
                for gb, price in sorted(packages.items())
            )
        else:
            packages_display = (
                get_texts(db_user.language).t('ADMIN_TARIFF_PACKAGES_NOT_CONFIGURED', 'Пакеты не настроены').strip()
            )
    else:
        status = get_texts(db_user.language).t('ADMIN_TARIFF_DISABLED_STATE', 'Отключено')
        packages_display = '—'

    # Форматируем лимит
    if max_topup_traffic > 0:
        max_limit_display = (
            get_texts(db_user.language).t('ADMIN_TARIFF_TRAFFIC_VALUE', '{v0} ГБ').format(v0=max_topup_traffic)
        )
    else:
        max_limit_display = get_texts(db_user.language).t('ADMIN_TARIFF_NO_LIMIT', 'Без ограничений')

    buttons = []

    if new_value:
        buttons.append(
            [
                _tariff_button(
                    get_texts(db_user.language).t('ADMIN_TARIFF_DISABLE', 'Отключить'),
                    f'admin_tariff_toggle_traffic_topup:{tariff_id}',
                    icon='lock',
                )
            ]
        )
        buttons.append(
            [
                _tariff_button(
                    get_texts(db_user.language).t('ADMIN_TARIFF_CONFIGURE_PACKAGES', 'Настроить пакеты'),
                    f'admin_tariff_edit_topup_packages:{tariff_id}',
                    icon='settings',
                )
            ]
        )
        buttons.append(
            [
                _tariff_button(
                    get_texts(db_user.language).t('ADMIN_TARIFF_MAX_TRAFFIC', 'Макс. лимит трафика'),
                    f'admin_tariff_edit_max_topup:{tariff_id}',
                    icon='stats',
                )
            ]
        )
    else:
        buttons.append(
            [
                _tariff_button(
                    get_texts(db_user.language).t('ADMIN_TARIFF_ENABLE', 'Включить'),
                    f'admin_tariff_toggle_traffic_topup:{tariff_id}',
                    icon='success',
                )
            ]
        )

    buttons.append([admin_back_button(texts, f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            _topup_detail(
                tariff.name,
                status,
                packages_display,
                max_limit_display,
                enabled=new_value,
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass


@admin_required
@error_handler
async def start_edit_traffic_topup_packages(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование пакетов докупки трафика."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await state.set_state(AdminStates.editing_tariff_traffic_topup_packages)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
    current_packages = _format_traffic_topup_packages_for_edit(packages)

    if packages:
        packages_display = '\n'.join(
            get_texts(db_user.language)
            .t('ADMIN_TARIFF_PACKAGE_LINE', '• {v0} ГБ: {v1}')
            .format(v0=gb, v1=format_price_kopeks(price))
            .strip()
            for gb, price in sorted(packages.items())
        )
    else:
        packages_display = get_texts(db_user.language).t('ADMIN_TARIFF_NOT_CONFIGURED', 'Не настроены')

    await callback.message.edit_text(
        _tariff_prompt(
            'coin',
            'Настройка пакетов докупки трафика',
            [
                _tariff_field('tariff', 'Тариф', f'<b>{html.escape(tariff.name)}</b>'),
                f'{admin_icon("coin")} <b>Текущие пакеты:</b>',
                packages_display,
            ],
            f'Введите пакеты в формате:\n<code>{current_packages}</code>\n\n'
            '(ГБ:цена_в_копейках, через запятую)\n'
            'Например: <code>5:5000, 10:9000</code> = 5ГБ за 50₽, 10ГБ за 90₽',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [admin_back_button(texts, f'admin_tariff_edit_traffic_topup:{tariff_id}', label=texts.CANCEL)]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_traffic_topup_packages(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новые пакеты докупки трафика."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer(
            _tariff_notice('warning', 'Тариф не найден'),
            parse_mode='HTML',
        )
        await state.clear()
        return

    if not message.text:
        await message.answer(
            _tariff_notice(
                'warning',
                'Пожалуйста, отправьте текстовое сообщение.\n\n'
                'Формат: <code>ГБ:цена_в_копейках</code>\n'
                'Пример: <code>5:5000, 10:9000, 20:15000</code>',
            ),
            parse_mode='HTML',
        )
        return

    packages = _parse_traffic_topup_packages(message.text.strip())

    if not packages:
        await message.answer(
            _tariff_notice(
                'warning',
                'Не удалось распознать пакеты.\n\n'
                'Формат: <code>ГБ:цена_в_копейках</code>\n'
                'Пример: <code>5:5000, 10:9000, 20:15000</code>',
            ),
            parse_mode='HTML',
        )
        return

    # Преобразуем в формат для JSON (строковые ключи)
    packages_json = {str(gb): price for gb, price in packages.items()}

    tariff = await update_tariff(db, tariff, traffic_topup_packages=packages_json)
    await state.clear()

    # Показываем обновленное меню
    texts = get_texts(db_user.language)
    packages_display = '\n'.join(
        get_texts(db_user.language)
        .t('ADMIN_TARIFF_PACKAGE_LINE', '• {v0} ГБ: {v1}')
        .format(v0=gb, v1=format_price_kopeks(price))
        .strip()
        for gb, price in sorted(packages.items())
    )
    max_topup_traffic = getattr(tariff, 'max_topup_traffic_gb', 0) or 0
    max_limit_display = (
        get_texts(db_user.language).t('ADMIN_TARIFF_TRAFFIC_VALUE', '{v0} ГБ').format(v0=max_topup_traffic)
        if max_topup_traffic > 0
        else get_texts(db_user.language).t('ADMIN_TARIFF_NO_LIMIT', 'Без ограничений')
    )

    buttons = [
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_DISABLE', 'Отключить'),
                f'admin_tariff_toggle_traffic_topup:{tariff_id}',
                icon='lock',
            )
        ],
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_CONFIGURE_PACKAGES', 'Настроить пакеты'),
                f'admin_tariff_edit_topup_packages:{tariff_id}',
                icon='settings',
            )
        ],
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_MAX_TRAFFIC', 'Макс. лимит трафика'),
                f'admin_tariff_edit_max_topup:{tariff_id}',
                icon='stats',
            )
        ],
        [admin_back_button(texts, f'admin_tariff_view:{tariff_id}')],
    ]

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Пакеты обновлены")}\n\n'
            + _topup_detail(
                tariff.name,
                get_texts(db_user.language).t('ADMIN_TARIFF_ENABLED_STATE', 'Включено'),
                packages_display,
                max_limit_display,
            ),
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )


# ============ МАКСИМАЛЬНЫЙ ЛИМИТ ДОКУПКИ ТРАФИКА ============


@admin_required
@error_handler
async def start_edit_max_topup_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование максимального лимита докупки трафика."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await state.set_state(AdminStates.editing_tariff_max_topup_traffic)
    await state.update_data(tariff_id=tariff_id)

    current_limit = getattr(tariff, 'max_topup_traffic_gb', 0) or 0
    if current_limit > 0:
        current_display = (
            get_texts(db_user.language).t('ADMIN_TARIFF_TRAFFIC_VALUE', '{v0} ГБ').format(v0=current_limit)
        )
    else:
        current_display = get_texts(db_user.language).t('ADMIN_TARIFF_NO_LIMIT', 'Без ограничений')

    await callback.message.edit_text(
        _tariff_prompt(
            'stats',
            'Максимальный лимит трафика',
            [
                _tariff_field('tariff', 'Тариф', f'<b>{html.escape(tariff.name)}</b>'),
                _tariff_field('stats', 'Текущий лимит', f'<b>{current_display}</b>'),
            ],
            'Введите максимальный общий объем трафика (в ГБ), который может быть на подписке после всех докупок.\n\n'
            '• Например, если тариф дает 100 ГБ и лимит 200 ГБ — пользователь сможет докупить еще 100 ГБ\n'
            '• Введите <code>0</code> для снятия ограничения',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [admin_back_button(texts, f'admin_tariff_edit_traffic_topup:{tariff_id}', label=texts.CANCEL)]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_max_topup_traffic(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое значение максимального лимита докупки трафика."""
    texts = get_texts(db_user.language)
    state_data = await state.get_data()
    tariff_id = state_data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer(
            _tariff_notice('warning', 'Тариф не найден'),
            parse_mode='HTML',
        )
        await state.clear()
        return

    # Парсим значение
    text = message.text.strip()
    try:
        new_limit = int(text)
        if new_limit < 0:
            raise ValueError('Negative value')
    except ValueError:
        await message.answer(
            _tariff_notice(
                'warning',
                'Введите целое число (0 или больше).\n\n'
                '• <code>0</code> — без ограничений\n'
                '• <code>200</code> — максимум 200 ГБ на подписке',
            ),
            parse_mode='HTML',
        )
        return

    tariff = await update_tariff(db, tariff, max_topup_traffic_gb=new_limit)
    await state.clear()

    # Показываем обновленное меню
    packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
    if packages:
        packages_display = '\n'.join(
            get_texts(db_user.language)
            .t('ADMIN_TARIFF_PACKAGE_LINE', '• {v0} ГБ: {v1}')
            .format(v0=gb, v1=format_price_kopeks(price))
            .strip()
            for gb, price in sorted(packages.items())
        )
    else:
        packages_display = (
            get_texts(db_user.language).t('ADMIN_TARIFF_PACKAGES_NOT_CONFIGURED', 'Пакеты не настроены').strip()
        )

    max_limit_display = (
        get_texts(db_user.language).t('ADMIN_TARIFF_TRAFFIC_VALUE', '{v0} ГБ').format(v0=new_limit)
        if new_limit > 0
        else get_texts(db_user.language).t('ADMIN_TARIFF_NO_LIMIT', 'Без ограничений')
    )

    buttons = [
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_DISABLE', 'Отключить'),
                f'admin_tariff_toggle_traffic_topup:{tariff_id}',
                icon='lock',
            )
        ],
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_CONFIGURE_PACKAGES', 'Настроить пакеты'),
                f'admin_tariff_edit_topup_packages:{tariff_id}',
                icon='settings',
            )
        ],
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_MAX_TRAFFIC', 'Макс. лимит трафика'),
                f'admin_tariff_edit_max_topup:{tariff_id}',
                icon='stats',
            )
        ],
        [admin_back_button(texts, f'admin_tariff_view:{tariff_id}')],
    ]

    await message.answer(
        _tariff_screen(
            f'{admin_heading("success", "Лимит обновлён")}\n\n'
            + _topup_detail(
                tariff.name,
                get_texts(db_user.language).t('ADMIN_TARIFF_ENABLED_STATE', 'Включено'),
                packages_display,
                max_limit_display,
            ),
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )


# ============ УДАЛЕНИЕ ТАРИФА ============


@admin_required
@error_handler
async def confirm_delete_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Запрашивает подтверждение удаления тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    active_count = await get_active_subscriptions_count_by_tariff_id(db, tariff_id)

    if active_count > 0:
        total_count = await get_tariff_subscriptions_count(db, tariff_id)
        await callback.message.edit_text(
            _tariff_screen(
                f'{admin_heading("warning", "Удаление тарифа")}\n\n'
                + admin_quote(
                    '\n'.join(
                        [
                            f'{admin_icon("tariff")} Невозможно удалить тариф <b>{html.escape(tariff.name)}</b>.',
                            '',
                            (
                                f'{admin_icon("warning")} <b>Активных подписок:</b> {active_count} '
                                f'(всего: {total_count})'
                            ),
                        ]
                    )
                )
                + '\n\nСначала деактивируйте тариф и дождитесь окончания всех активных подписок, '
                'либо переведите подписки на другой тариф.',
                icon='warning',
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            f'admin_tariff_view:{tariff_id}',
                            label=texts.t('ADMIN_TARIFF_BACK_TO_TARIFF', '← Назад к тарифу'),
                        )
                    ],
                ]
            ),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    warning = ''
    if subs_count > 0:
        warning = (
            f'\n\n{admin_icon("warning")} <b>Внимание!</b> На этом тарифе {subs_count} неактивных подписок.\n'
            'Они потеряют привязку к тарифу.'
        )

    await callback.message.edit_text(
        _tariff_screen(
            f'{admin_heading("warning", "Удаление тарифа")}\n\n'
            + admin_quote(f'Вы действительно хотите удалить тариф <b>{html.escape(tariff.name)}</b>?{warning}'),
            icon='warning',
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_button(
                        'Да, удалить',
                        callback_data=f'admin_tariff_delete_confirm:{tariff_id}',
                        icon='delete',
                    ),
                    admin_back_button(
                        texts,
                        f'admin_tariff_view:{tariff_id}',
                        label=texts.CANCEL,
                    ),
                ]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_tariff_confirmed(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Удаляет тариф после подтверждения."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    # Защита от удаления тарифа с активными подписками (FK RESTRICT)
    active_count = await get_active_subscriptions_count_by_tariff_id(db, tariff.id)
    if active_count > 0:
        await callback.answer(
            get_texts(db_user.language)
            .t(
                'ADMIN_TARIFF_DELETE_ACTIVE_ALERT',
                'Невозможно удалить тариф: {v0} активных подписок. Сначала деактивируйте тариф.',
            )
            .format(v0=active_count),
            show_alert=True,
        )
        return

    tariff_name = tariff.name
    await delete_tariff(db, tariff)

    await callback.answer(
        get_texts(db_user.language).t('ADMIN_TARIFF_DELETED', 'Тариф «{v0}» удален').format(v0=tariff_name),
        show_alert=True,
    )

    # Возвращаемся к списку
    tariffs_data = await get_tariffs_with_subscriptions_count(db, include_inactive=True)

    if not tariffs_data:
        await callback.message.edit_text(
            _tariff_screen(
                f'{admin_heading("tariff", "Тарифы")}\n\n' + admin_quote('Тарифы ещё не созданы.'),
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _tariff_button(
                            texts.t('ADMIN_TARIFF_BUTTON_CREATE', 'Создать тариф'),
                            'admin_tariff_create',
                            icon='plus',
                        )
                    ],
                    [admin_back_button(texts, 'admin_panel')],
                ]
            ),
            parse_mode='HTML',
        )
        return

    total_pages = (len(tariffs_data) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page_data = tariffs_data[:ITEMS_PER_PAGE]

    await callback.message.edit_text(
        _tariff_screen(
            f'{admin_heading("tariff", "Тарифы")}\n\n'
            + admin_quote(
                f'{admin_icon("delete")} Тариф «{html.escape(tariff_name)}» удалён\n'
                f'{admin_icon("tariff")} <b>Всего:</b> {len(tariffs_data)}'
            ),
        ),
        reply_markup=get_tariffs_list_keyboard(page_data, db_user.language, 0, total_pages),
        parse_mode='HTML',
    )


# ============ РЕДАКТИРОВАНИЕ СЕРВЕРОВ ============


@admin_required
@error_handler
async def start_edit_tariff_squads(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает меню выбора серверов для тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    squads, _ = await get_all_server_squads(db, limit=10000)

    if not squads:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NO_SERVERS', 'Нет доступных серверов'), show_alert=True
        )
        return

    current_squads = set(tariff.allowed_squads or [])

    buttons = []
    for squad in squads:
        is_selected = squad.squad_uuid in current_squads
        mark = '✅ ' if is_selected else ''
        buttons.append(
            [
                admin_button(
                    f'{mark}{squad.display_name}',
                    callback_data=f'trf_sq:{tariff_id}:{squad.squad_uuid}',
                    icon='servers',
                )
            ]
        )

    buttons.append(
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_CLEAR_ALL', 'Очистить все'),
                f'admin_tariff_clear_squads:{tariff_id}',
                icon='delete',
            ),
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_SELECT_ALL', 'Выбрать все'),
                f'admin_tariff_select_all_squads:{tariff_id}',
                icon='check',
            ),
        ]
    )
    buttons.append([admin_back_button(texts, f'admin_tariff_view:{tariff_id}')])

    selected_count = len(current_squads)

    await callback.message.edit_text(
        _squads_screen(tariff.name, selected_count, len(squads)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_tariff_squad(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает выбор сервера для тарифа."""
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    squad_uuid = parts[2]

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    current_squads = set(tariff.allowed_squads or [])

    if squad_uuid in current_squads:
        current_squads.remove(squad_uuid)
    else:
        current_squads.add(squad_uuid)

    tariff = await update_tariff(db, tariff, allowed_squads=list(current_squads))

    # Перерисовываем меню
    squads, _ = await get_all_server_squads(db, limit=10000)
    texts = get_texts(db_user.language)

    buttons = []
    for squad in squads:
        is_selected = squad.squad_uuid in current_squads
        mark = '✅ ' if is_selected else ''
        buttons.append(
            [
                admin_button(
                    f'{mark}{squad.display_name}',
                    callback_data=f'trf_sq:{tariff_id}:{squad.squad_uuid}',
                    icon='servers',
                )
            ]
        )

    buttons.append(
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_CLEAR_ALL', 'Очистить все'),
                f'admin_tariff_clear_squads:{tariff_id}',
                icon='delete',
            ),
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_SELECT_ALL', 'Выбрать все'),
                f'admin_tariff_select_all_squads:{tariff_id}',
                icon='check',
            ),
        ]
    )
    buttons.append([admin_back_button(texts, f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            _squads_screen(tariff.name, len(current_squads), len(squads)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass

    await callback.answer()

    # Применяем изменения серверов к существующим подпискам
    from app.services.subscription_service import SubscriptionService

    propagate_result = await SubscriptionService().propagate_tariff_squads(db, tariff.id, list(current_squads))
    if propagate_result.failed_ids:
        await callback.message.answer(
            _tariff_notice(
                'warning',
                f'{len(propagate_result.failed_ids)} из {propagate_result.total} подписок '
                'не синхронизированы с RemnaWave',
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def clear_tariff_squads(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Очищает список серверов тарифа."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    tariff = await update_tariff(db, tariff, allowed_squads=[])
    await callback.answer(get_texts(db_user.language).t('ADMIN_TARIFF_SERVERS_CLEARED', 'Все серверы очищены'))

    # Перерисовываем меню
    squads, _ = await get_all_server_squads(db, limit=10000)
    texts = get_texts(db_user.language)

    buttons = []
    for squad in squads:
        buttons.append(
            [
                admin_button(
                    squad.display_name,
                    callback_data=f'trf_sq:{tariff_id}:{squad.squad_uuid}',
                    icon='servers',
                )
            ]
        )

    buttons.append(
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_CLEAR_ALL', 'Очистить все'),
                f'admin_tariff_clear_squads:{tariff_id}',
                icon='delete',
            ),
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_SELECT_ALL', 'Выбрать все'),
                f'admin_tariff_select_all_squads:{tariff_id}',
                icon='check',
            ),
        ]
    )
    buttons.append([admin_back_button(texts, f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            _squads_screen(tariff.name, 0, len(squads)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass

    # Применяем изменения серверов к существующим подпискам (пустой список = все серверы)
    from app.services.subscription_service import SubscriptionService

    propagate_result = await SubscriptionService().propagate_tariff_squads(db, tariff.id, [])
    if propagate_result.failed_ids:
        await callback.message.answer(
            _tariff_notice(
                'warning',
                f'{len(propagate_result.failed_ids)} из {propagate_result.total} подписок '
                'не синхронизированы с RemnaWave',
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def select_all_tariff_squads(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Выбирает все серверы для тарифа."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    squads, _ = await get_all_server_squads(db, limit=10000)
    all_uuids = [s.squad_uuid for s in squads if s.squad_uuid]

    tariff = await update_tariff(db, tariff, allowed_squads=all_uuids)
    await callback.answer(get_texts(db_user.language).t('ADMIN_TARIFF_SERVERS_SELECTED', 'Все серверы выбраны'))

    texts = get_texts(db_user.language)

    buttons = []
    for squad in squads:
        buttons.append(
            [
                admin_button(
                    squad.display_name,
                    callback_data=f'trf_sq:{tariff_id}:{squad.squad_uuid}',
                    icon='servers',
                )
            ]
        )

    buttons.append(
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_CLEAR_ALL', 'Очистить все'),
                f'admin_tariff_clear_squads:{tariff_id}',
                icon='delete',
            ),
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_SELECT_ALL', 'Выбрать все'),
                f'admin_tariff_select_all_squads:{tariff_id}',
                icon='check',
            ),
        ]
    )
    buttons.append([admin_back_button(texts, f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            _squads_screen(tariff.name, len(squads), len(squads)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass

    # Применяем изменения серверов к существующим подпискам
    from app.services.subscription_service import SubscriptionService

    propagate_result = await SubscriptionService().propagate_tariff_squads(db, tariff.id, all_uuids)
    if propagate_result.failed_ids:
        await callback.message.answer(
            _tariff_notice(
                'warning',
                f'{len(propagate_result.failed_ids)} из {propagate_result.total} подписок '
                'не синхронизированы с RemnaWave',
            ),
            parse_mode='HTML',
        )


# ============ РЕДАКТИРОВАНИЕ ПРОМОГРУПП ============


@admin_required
@error_handler
async def start_edit_tariff_promo_groups(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Показывает меню выбора промогрупп для тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    promo_groups_data = await get_promo_groups_with_counts(db)

    if not promo_groups_data:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NO_PROMO_GROUPS', 'Нет промогрупп'), show_alert=True
        )
        return

    current_groups = {pg.id for pg in (tariff.allowed_promo_groups or [])}

    buttons = []
    for promo_group, _ in promo_groups_data:
        is_selected = promo_group.id in current_groups
        mark = '✅ ' if is_selected else ''
        buttons.append(
            [
                admin_button(
                    f'{mark}{promo_group.name}',
                    callback_data=f'admin_tariff_toggle_promo:{tariff_id}:{promo_group.id}',
                    icon='promo',
                )
            ]
        )

    buttons.append(
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_CLEAR_ALL', 'Очистить все'),
                f'admin_tariff_clear_promo:{tariff_id}',
                icon='delete',
            ),
        ]
    )
    buttons.append([admin_back_button(texts, f'admin_tariff_view:{tariff_id}')])

    selected_count = len(current_groups)

    await callback.message.edit_text(
        _promo_groups_screen(tariff.name, selected_count),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_tariff_promo_group(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает выбор промогруппы для тарифа."""
    from app.database.crud.tariff import add_promo_group_to_tariff, remove_promo_group_from_tariff

    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    promo_group_id = int(parts[2])

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    current_groups = {pg.id for pg in (tariff.allowed_promo_groups or [])}

    if promo_group_id in current_groups:
        await remove_promo_group_from_tariff(db, tariff, promo_group_id)
        current_groups.remove(promo_group_id)
    else:
        await add_promo_group_to_tariff(db, tariff, promo_group_id)
        current_groups.add(promo_group_id)

    # Обновляем тариф из БД
    tariff = await get_tariff_by_id(db, tariff_id)
    current_groups = {pg.id for pg in (tariff.allowed_promo_groups or [])}

    # Перерисовываем меню
    promo_groups_data = await get_promo_groups_with_counts(db)
    texts = get_texts(db_user.language)

    buttons = []
    for promo_group, _ in promo_groups_data:
        is_selected = promo_group.id in current_groups
        mark = '✅ ' if is_selected else ''
        buttons.append(
            [
                admin_button(
                    f'{mark}{promo_group.name}',
                    callback_data=f'admin_tariff_toggle_promo:{tariff_id}:{promo_group.id}',
                    icon='promo',
                )
            ]
        )

    buttons.append(
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_CLEAR_ALL', 'Очистить все'),
                f'admin_tariff_clear_promo:{tariff_id}',
                icon='delete',
            ),
        ]
    )
    buttons.append([admin_back_button(texts, f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            _promo_groups_screen(tariff.name, len(current_groups)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass

    await callback.answer()


@admin_required
@error_handler
async def clear_tariff_promo_groups(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Очищает список промогрупп тарифа."""
    from app.database.crud.tariff import set_tariff_promo_groups

    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    await set_tariff_promo_groups(db, tariff, [])
    await callback.answer(get_texts(db_user.language).t('ADMIN_TARIFF_PROMO_GROUPS_CLEARED', 'Все промогруппы очищены'))

    # Перерисовываем меню
    promo_groups_data = await get_promo_groups_with_counts(db)
    texts = get_texts(db_user.language)

    buttons = []
    for promo_group, _ in promo_groups_data:
        buttons.append(
            [
                admin_button(
                    promo_group.name,
                    callback_data=f'admin_tariff_toggle_promo:{tariff_id}:{promo_group.id}',
                    icon='promo',
                )
            ]
        )

    buttons.append(
        [
            _tariff_button(
                get_texts(db_user.language).t('ADMIN_TARIFF_CLEAR_ALL', 'Очистить все'),
                f'admin_tariff_clear_promo:{tariff_id}',
                icon='delete',
            ),
        ]
    )
    buttons.append([admin_back_button(texts, f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            _promo_groups_screen(tariff.name, 0),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass


# ==================== Режим сброса трафика ====================

TRAFFIC_RESET_MODES = [
    ('DAY', 'ADMIN_TARIFF_RESET_DAY', 'ADMIN_TARIFF_RESET_DAY_DESC'),
    ('WEEK', 'ADMIN_TARIFF_RESET_WEEK', 'ADMIN_TARIFF_RESET_WEEK_DESC'),
    ('MONTH', 'ADMIN_TARIFF_RESET_MONTH', 'ADMIN_TARIFF_RESET_MONTH_DESC'),
    ('MONTH_ROLLING', 'ADMIN_TARIFF_RESET_ROLLING', 'ADMIN_TARIFF_RESET_ROLLING_DESC'),
    ('NO_RESET', 'ADMIN_TARIFF_RESET_NEVER', 'ADMIN_TARIFF_RESET_NEVER_DESC'),
]


def get_traffic_reset_mode_keyboard(tariff_id: int, current_mode: str | None, language: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора режима сброса трафика."""
    texts = get_texts(language)
    buttons = []

    # Кнопка "Глобальная настройка"
    global_label = (
        texts.t('ADMIN_TARIFF_GLOBAL_MODE_CHOICE', '{v0} Глобальная настройка ({v1})')
        .format(v0='', v1=settings.DEFAULT_TRAFFIC_RESET_STRATEGY)
        .strip()
    )
    buttons.append(
        [
            admin_button(
                global_label,
                callback_data=f'admin_tariff_set_reset_mode:{tariff_id}:GLOBAL',
                icon='check' if current_mode is None else 'settings',
            )
        ]
    )

    mode_icons = {
        'DAY': 'calendar',
        'WEEK': 'calendar',
        'MONTH': 'calendar',
        'MONTH_ROLLING': 'refresh',
        'NO_RESET': 'prohibit',
    }

    # Кнопки для каждого режима
    for mode_value, mode_label_key, mode_desc_key in TRAFFIC_RESET_MODES:
        mode_label = strip_leading_emoji(texts.t(mode_label_key))
        is_selected = current_mode == mode_value
        buttons.append(
            [
                admin_button(
                    mode_label,
                    callback_data=f'admin_tariff_set_reset_mode:{tariff_id}:{mode_value}',
                    icon='check' if is_selected else mode_icons.get(mode_value, 'refresh'),
                )
            ]
        )

    # Кнопка назад
    buttons.append([admin_back_button(texts, f'admin_tariff_view:{tariff_id}')])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@admin_required
@error_handler
async def start_edit_traffic_reset_mode(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Начинает редактирование режима сброса трафика."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    current_mode = getattr(tariff, 'traffic_reset_mode', None)

    await callback.message.edit_text(
        _reset_mode_screen(
            tariff.name,
            _format_traffic_reset_mode(current_mode, language=db_user.language),
        ),
        reply_markup=get_traffic_reset_mode_keyboard(tariff_id, current_mode, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def set_traffic_reset_mode(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Устанавливает режим сброса трафика для тарифа."""
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    new_mode = parts[2]

    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_TARIFF_NOT_FOUND', 'Тариф не найден'), show_alert=True
        )
        return

    # Преобразуем GLOBAL в None
    if new_mode == 'GLOBAL':
        new_mode = None

    # Обновляем тариф
    tariff = await update_tariff(db, tariff, traffic_reset_mode=new_mode)

    mode_display = _format_traffic_reset_mode(new_mode, language=db_user.language)
    await callback.answer(
        get_texts(db_user.language)
        .t('ADMIN_TARIFF_RESET_MODE_UPDATED', 'Режим сброса изменён: {v0}')
        .format(v0=mode_display),
        show_alert=True,
    )

    # Обновляем клавиатуру
    await callback.message.edit_text(
        _reset_mode_screen(tariff.name, mode_display),
        reply_markup=get_traffic_reset_mode_keyboard(tariff_id, new_mode, db_user.language),
        parse_mode='HTML',
    )


def register_handlers(dp: Dispatcher):
    """Регистрирует обработчики для управления тарифами."""
    # Отдельные экраны регистрируются до общего toggle-фильтра.
    register_custom_traffic_handlers(dp)
    register_custom_days_handlers(dp)
    register_panel_settings_handlers(dp)
    register_server_limits_handlers(dp)

    # Список тарифов
    dp.callback_query.register(show_tariffs_list, F.data == 'admin_tariffs')
    dp.callback_query.register(show_tariffs_page, F.data.startswith('admin_tariffs_page:'))

    # Просмотр и переключение
    dp.callback_query.register(view_tariff, F.data.startswith('admin_tariff_view:'))
    dp.callback_query.register(
        toggle_tariff,
        F.data.startswith('admin_tariff_toggle:')
        & ~F.data.startswith('admin_tariff_toggle_trial:')
        & ~F.data.startswith('trf_sq:')
        & ~F.data.startswith('admin_tariff_toggle_promo:')
        & ~F.data.startswith('admin_tariff_toggle_traffic_topup:')
        & ~F.data.startswith('admin_tariff_toggle_custom_traffic:')
        & ~F.data.startswith('admin_tariff_toggle_daily:'),
    )
    dp.callback_query.register(toggle_trial_tariff, F.data.startswith('admin_tariff_toggle_trial:'))

    # Создание тарифа
    dp.callback_query.register(start_create_tariff, F.data == 'admin_tariff_create')
    dp.message.register(process_tariff_name, AdminStates.creating_tariff_name)
    dp.message.register(process_tariff_traffic, AdminStates.creating_tariff_traffic)
    dp.message.register(process_tariff_devices, AdminStates.creating_tariff_devices)
    dp.message.register(process_tariff_tier, AdminStates.creating_tariff_tier)
    dp.callback_query.register(select_tariff_type_periodic, F.data == 'tariff_type_periodic')
    dp.callback_query.register(select_tariff_type_daily, F.data == 'tariff_type_daily')
    dp.message.register(process_tariff_prices, AdminStates.creating_tariff_prices)

    # Редактирование названия
    dp.callback_query.register(start_edit_tariff_name, F.data.startswith('admin_tariff_edit_name:'))
    dp.message.register(process_edit_tariff_name, AdminStates.editing_tariff_name)

    # Редактирование описания
    dp.callback_query.register(start_edit_tariff_description, F.data.startswith('admin_tariff_edit_desc:'))
    dp.message.register(process_edit_tariff_description, AdminStates.editing_tariff_description)

    # Редактирование трафика (traffic_topup BEFORE traffic to avoid prefix conflict)
    dp.callback_query.register(start_edit_tariff_traffic_topup, F.data.startswith('admin_tariff_edit_traffic_topup:'))
    dp.callback_query.register(start_edit_tariff_traffic, F.data.startswith('admin_tariff_edit_traffic:'))
    dp.message.register(process_edit_tariff_traffic, AdminStates.editing_tariff_traffic)

    # Редактирование устройств
    dp.callback_query.register(start_edit_tariff_devices, F.data.startswith('admin_tariff_edit_devices:'))
    dp.message.register(process_edit_tariff_devices, AdminStates.editing_tariff_devices)

    # Редактирование уровня
    dp.callback_query.register(start_edit_tariff_tier, F.data.startswith('admin_tariff_edit_tier:'))
    dp.message.register(process_edit_tariff_tier, AdminStates.editing_tariff_tier)

    # Редактирование цен
    dp.callback_query.register(start_edit_tariff_prices, F.data.startswith('admin_tariff_edit_prices:'))
    dp.callback_query.register(start_edit_tariff_highlight, F.data.startswith('admin_tariff_edit_highlight:'))
    dp.callback_query.register(toggle_tariff_highlight, F.data.startswith('admin_tariff_toggle_highlight:'))
    dp.callback_query.register(set_tariff_highlight, F.data.startswith('admin_tariff_highlight_set:'))
    dp.message.register(process_edit_tariff_prices, AdminStates.editing_tariff_prices)

    # Редактирование цены за устройство
    dp.callback_query.register(start_edit_tariff_device_price, F.data.startswith('admin_tariff_edit_device_price:'))
    dp.message.register(process_edit_tariff_device_price, AdminStates.editing_tariff_device_price)

    # Редактирование макс. устройств
    dp.callback_query.register(start_edit_tariff_max_devices, F.data.startswith('admin_tariff_edit_max_devices:'))
    dp.message.register(process_edit_tariff_max_devices, AdminStates.editing_tariff_max_devices)

    # Редактирование дней триала
    dp.callback_query.register(start_edit_tariff_trial_days, F.data.startswith('admin_tariff_edit_trial_days:'))
    dp.message.register(process_edit_tariff_trial_days, AdminStates.editing_tariff_trial_days)

    # Редактирование докупки трафика (start_edit_tariff_traffic_topup registered above with traffic)
    dp.callback_query.register(toggle_tariff_traffic_topup, F.data.startswith('admin_tariff_toggle_traffic_topup:'))
    dp.callback_query.register(
        start_edit_traffic_topup_packages, F.data.startswith('admin_tariff_edit_topup_packages:')
    )
    dp.message.register(process_edit_traffic_topup_packages, AdminStates.editing_tariff_traffic_topup_packages)

    # Редактирование макс. лимита докупки трафика
    dp.callback_query.register(start_edit_max_topup_traffic, F.data.startswith('admin_tariff_edit_max_topup:'))
    dp.message.register(process_edit_max_topup_traffic, AdminStates.editing_tariff_max_topup_traffic)

    # Удаление (delete_confirm BEFORE delete to avoid prefix conflict)
    dp.callback_query.register(delete_tariff_confirmed, F.data.startswith('admin_tariff_delete_confirm:'))
    dp.callback_query.register(confirm_delete_tariff, F.data.startswith('admin_tariff_delete:'))

    # Редактирование серверов
    dp.callback_query.register(start_edit_tariff_squads, F.data.startswith('admin_tariff_edit_squads:'))
    dp.callback_query.register(toggle_tariff_squad, F.data.startswith('trf_sq:'))
    dp.callback_query.register(clear_tariff_squads, F.data.startswith('admin_tariff_clear_squads:'))
    dp.callback_query.register(select_all_tariff_squads, F.data.startswith('admin_tariff_select_all_squads:'))

    # Редактирование промогрупп
    dp.callback_query.register(start_edit_tariff_promo_groups, F.data.startswith('admin_tariff_edit_promo:'))
    dp.callback_query.register(toggle_tariff_promo_group, F.data.startswith('admin_tariff_toggle_promo:'))
    dp.callback_query.register(clear_tariff_promo_groups, F.data.startswith('admin_tariff_clear_promo:'))

    # Суточный режим
    dp.callback_query.register(toggle_daily_tariff, F.data.startswith('admin_tariff_toggle_daily:'))
    dp.callback_query.register(start_edit_daily_price, F.data.startswith('admin_tariff_edit_daily_price:'))
    dp.message.register(process_daily_price_input, AdminStates.editing_tariff_daily_price)

    # Режим сброса трафика
    dp.callback_query.register(start_edit_traffic_reset_mode, F.data.startswith('admin_tariff_edit_reset_mode:'))
    dp.callback_query.register(set_traffic_reset_mode, F.data.startswith('admin_tariff_set_reset_mode:'))
