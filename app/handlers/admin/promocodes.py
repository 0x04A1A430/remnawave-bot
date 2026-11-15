import html
import re
from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.promo_group import get_promo_group_by_id, get_promo_groups_with_counts
from app.database.crud.promocode import (
    create_promocode,
    delete_promocode,
    get_promocode_by_code,
    get_promocode_by_id,
    get_promocode_statistics,
    get_promocodes_count,
    get_promocodes_list,
    update_promocode,
)
from app.database.models import PromoCodeType, User
from app.keyboards.admin import (
    get_admin_pagination_keyboard,
    get_admin_promocodes_keyboard,
    get_promocode_type_keyboard,
)
from app.localization.texts import get_texts
from app.states import AdminStates
from app.utils.admin_ui import admin_back_button, admin_button, admin_heading, admin_icon, admin_quote, admin_screen
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_datetime


logger = structlog.get_logger(__name__)

# Иконки типов промокодов для списка и карточки.
_PROMO_TYPE_ICONS = {
    'balance': 'money',
    'subscription_days': 'calendar',
    'trial_subscription': 'trials',
    'promo_group': 'users',
    'discount': 'star',
    'balance_and_days': 'coin',
}

# Следы прежнего оформления, оставшиеся в локалях: декоративные глифы,
# метка NEW и лишние ведущие пробелы строк — иконку и отступы даёт admin_screen.
_LEGACY_PREFIX_RE = re.compile(r'^[ \t]*(?:[▤▣▥◷↻]\s*|NEW\s+)*', re.MULTILINE)


def _clean_legacy(value: str) -> str:
    """Убирает декоративные глифы, метку NEW и ведущие пробелы строк."""
    return _LEGACY_PREFIX_RE.sub('', value).strip()


_BOLD_TAGS_RE = re.compile(r'</?b>')
_PAGE_SUFFIX_RE = re.compile(r'\s*\([^()]*\)\s*$')


def _heading(icon: str, title: str) -> str:
    """Заголовок с premium-иконкой и <b>; существующие теги не дублируются."""
    return admin_heading(icon, _BOLD_TAGS_RE.sub('', title or '').strip())


def _screen(raw: str, *, icon: str) -> str:
    """Карточка: заголовок из локали + блок параметров под ним."""
    prepared = _clean_legacy(raw or '')
    title, separator, body = prepared.partition('\n\n')
    if not separator:
        return _heading(icon, prepared)

    body = body.strip()
    if not body:
        return _heading(icon, title)
    return f'{_heading(icon, title)}\n\n{admin_quote(body)}'


@admin_required
@error_handler
async def show_promocodes_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    total_codes = await get_promocodes_count(db)
    active_codes = await get_promocodes_count(db, is_active=True)

    stats_body = (
        f'{admin_icon("promo")} <b>Всего промокодов:</b> {total_codes}\n'
        f'{admin_icon("success")} <b>Активных:</b> {active_codes}\n'
        f'{admin_icon("prohibit")} <b>Неактивных:</b> {total_codes - active_codes}'
    )
    text = f'{admin_heading("promo", "Управление промокодами")}\n\n{admin_quote(stats_body)}'

    await callback.message.edit_text(
        text,
        reply_markup=get_admin_promocodes_keyboard(db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_promocodes_list(callback: types.CallbackQuery, db_user: User, db: AsyncSession, page: int = 1):
    limit = 10
    offset = (page - 1) * limit

    promocodes = await get_promocodes_list(db, offset=offset, limit=limit)
    total_count = await get_promocodes_count(db)
    total_pages = (total_count + limit - 1) // limit
    texts = get_texts(db_user.language)

    if not promocodes:
        await callback.message.edit_text(
            admin_screen(texts.t('ADMIN_PROMOCODE_UI_060', 'Промокоды не найдены'), icon='promo', compact=True),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[admin_back_button(texts, 'admin_promocodes')]]),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    header_blob = _clean_legacy(
        texts.t('ADMIN_PROMOCODE_UI_001', '<b>Список промокодов</b> (стр. {v0}/{v1})\n\n').format(
            v0=page, v1=total_pages
        )
    )
    header_title, _, header_rest = header_blob.partition('\n\n')
    page_match = _PAGE_SUFFIX_RE.search(header_title)
    page_note = page_match.group(0).strip(' ()') if page_match else ''
    header_title = _PAGE_SUFFIX_RE.sub('', header_title).strip()
    text = ''
    keyboard = []

    for promo in promocodes:
        status_icon = admin_icon('success') if promo.is_active else admin_icon('error')
        type_icon = admin_icon(_PROMO_TYPE_ICONS.get(promo.type, 'promo'))

        text += f'{status_icon} {type_icon} <code>{promo.code}</code>\n'
        text += (
            get_texts(db_user.language)
            .t('ADMIN_PROMOCODE_UI_015', 'Использований: {v0}/{v1}\n')
            .format(v0=promo.current_uses, v1=promo.max_uses)
        )

        if promo.type == PromoCodeType.BALANCE.value:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_038', 'Бонус: {v0}\n')
                .format(v0=settings.format_price(promo.balance_bonus_kopeks))
            )
        elif promo.type == PromoCodeType.SUBSCRIPTION_DAYS.value:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_061', 'Дней: {v0}\n')
                .format(v0=promo.subscription_days)
            )
        elif promo.type == PromoCodeType.BALANCE_AND_DAYS.value:
            if promo.balance_bonus_kopeks:
                text += (
                    get_texts(db_user.language)
                    .t('ADMIN_PROMOCODE_UI_038', 'Бонус: {v0}\n')
                    .format(v0=settings.format_price(promo.balance_bonus_kopeks))
                )
            if promo.subscription_days:
                text += (
                    get_texts(db_user.language)
                    .t('ADMIN_PROMOCODE_UI_061', 'Дней: {v0}\n')
                    .format(v0=promo.subscription_days)
                )
            if getattr(promo, 'traffic_gb', 0):
                text += (
                    get_texts(db_user.language)
                    .t('ADMIN_PROMOCODE_UI_100', 'Трафик: {v0} ГБ\n')
                    .format(v0=promo.traffic_gb)
                )
        elif promo.type == PromoCodeType.PROMO_GROUP.value:
            if promo.promo_group:
                text += (
                    get_texts(db_user.language)
                    .t('ADMIN_PROMOCODE_UI_039', 'Промогруппа: {v0}\n')
                    .format(v0=html.escape(promo.promo_group.name))
                )
        elif promo.type == PromoCodeType.DISCOUNT.value:
            discount_hours = promo.subscription_days
            if discount_hours > 0:
                text += (
                    get_texts(db_user.language)
                    .t('ADMIN_PROMOCODE_UI_116', 'Скидка: {v0}% ({v1} ч.)\n')
                    .format(v0=promo.balance_bonus_kopeks, v1=discount_hours)
                )
            else:
                text += (
                    get_texts(db_user.language)
                    .t('ADMIN_PROMOCODE_UI_117', 'Скидка: {v0}% (до покупки)\n')
                    .format(v0=promo.balance_bonus_kopeks)
                )

        # Промогруппа комбинируется с любым типом (назначается при активации
        # независимо от type) — показываем прикреплённую группу и у составных
        if promo.type != PromoCodeType.PROMO_GROUP.value and promo.promo_group:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_039', 'Промогруппа: {v0}\n')
                .format(v0=html.escape(promo.promo_group.name))
            )

        if promo.valid_until:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_040', 'До: {v0}\n')
                .format(v0=format_datetime(promo.valid_until))
            )

        keyboard.append([admin_button(promo.code, callback_data=f'promo_manage_{promo.id}', icon='promo')])

        text += '\n'

    if total_pages > 1:
        pagination_row = get_admin_pagination_keyboard(
            page, total_pages, 'admin_promo_list', 'admin_promocodes', db_user.language
        ).inline_keyboard[0]
        keyboard.append(pagination_row)

    keyboard.extend(
        [
            [admin_button('Создать', callback_data='admin_promo_create', icon='plus')],
            [admin_back_button(texts, 'admin_promocodes')],
        ]
    )

    cards = [admin_quote(chunk.strip()) for chunk in _clean_legacy(text).split('\n\n') if chunk.strip()]
    blocks = [_heading('promo', header_title)]
    if page_note:
        blocks.append(f'{admin_icon("document")} {page_note}')
    blocks.extend(cards)

    await callback.message.edit_text(
        '\n\n'.join(blocks),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_promocodes_list_page(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Обработчик пагинации списка промокодов."""
    try:
        page = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        page = 1
    await show_promocodes_list(callback, db_user, db, page=page)


@admin_required
@error_handler
async def show_promocode_management(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    promo_id = int(callback.data.split('_')[-1])

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_062', 'Промокод не найден'), show_alert=True
        )
        return

    texts = get_texts(db_user.language)
    status_icon = admin_icon('success') if promo.is_active else admin_icon('error')
    type_icon = admin_icon(_PROMO_TYPE_ICONS.get(promo.type, 'promo'))
    text = f"""<b>Управление промокодом</b>

{type_icon} <b>Код:</b> <code>{promo.code}</code>
{status_icon} <b>Статус:</b> {'Активен' if promo.is_active else 'Неактивен'}
<b>Использований:</b> {promo.current_uses}/{promo.max_uses}
"""

    if promo.type == PromoCodeType.BALANCE.value:
        text += (
            get_texts(db_user.language)
            .t('ADMIN_PROMOCODE_UI_016', '<b>Бонус:</b> {v0}\n')
            .format(v0=settings.format_price(promo.balance_bonus_kopeks))
        )
    elif promo.type == PromoCodeType.SUBSCRIPTION_DAYS.value:
        text += (
            get_texts(db_user.language)
            .t('ADMIN_PROMOCODE_UI_041', '<b>Дней:</b> {v0}\n')
            .format(v0=promo.subscription_days)
        )
    elif promo.type == PromoCodeType.BALANCE_AND_DAYS.value:
        if promo.balance_bonus_kopeks:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_016', '<b>Бонус:</b> {v0}\n')
                .format(v0=settings.format_price(promo.balance_bonus_kopeks))
            )
        if promo.subscription_days:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_041', '<b>Дней:</b> {v0}\n')
                .format(v0=promo.subscription_days)
            )
        if getattr(promo, 'traffic_gb', 0):
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_075', '<b>Трафик:</b> {v0} ГБ\n')
                .format(v0=promo.traffic_gb)
            )
    elif promo.type == PromoCodeType.PROMO_GROUP.value:
        if promo.promo_group:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_103', '<b>Промогруппа:</b> {v0} (приоритет: {v1})\n')
                .format(v0=html.escape(promo.promo_group.name), v1=promo.promo_group.priority)
            )
        elif promo.promo_group_id:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_107', '<b>Промогруппа ID:</b> {v0} (не найдена)\n')
                .format(v0=promo.promo_group_id)
            )
    elif promo.type == PromoCodeType.DISCOUNT.value:
        discount_hours = promo.subscription_days
        if discount_hours > 0:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_108', '<b>Скидка:</b> {v0}% (срок: {v1} ч.)\n')
                .format(v0=promo.balance_bonus_kopeks, v1=discount_hours)
            )
        else:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_109', '<b>Скидка:</b> {v0}% (до первой покупки)\n')
                .format(v0=promo.balance_bonus_kopeks)
            )

    # Промогруппа комбинируется с любым типом (назначается при активации
    # независимо от type) — показываем прикреплённую группу и у составных
    if promo.type != PromoCodeType.PROMO_GROUP.value and promo.promo_group:
        text += (
            get_texts(db_user.language)
            .t('ADMIN_PROMOCODE_UI_017', '<b>Промогруппа:</b> {v0}\n')
            .format(v0=html.escape(promo.promo_group.name))
        )

    if promo.valid_until:
        text += (
            get_texts(db_user.language)
            .t('ADMIN_PROMOCODE_UI_018', '<b>Действует до:</b> {v0}\n')
            .format(v0=format_datetime(promo.valid_until))
        )

    first_purchase_only = getattr(promo, 'first_purchase_only', False)
    first_purchase_icon = admin_icon('success') if first_purchase_only else admin_icon('error')
    text += (
        get_texts(db_user.language)
        .t('ADMIN_PROMOCODE_UI_003', '<b>Только первая покупка:</b> {v0}\n')
        .format(v0=first_purchase_icon)
    )

    text += (
        get_texts(db_user.language)
        .t('ADMIN_PROMOCODE_UI_004', '<b>Создан:</b> {v0}\n')
        .format(v0=format_datetime(promo.created_at))
    )

    first_purchase_btn_text = _clean_legacy(
        texts.t('ADMIN_PROMOCODE_UI_019', 'Первая покупка: ')
        if first_purchase_only
        else texts.t('ADMIN_PROMOCODE_UI_020', 'Первая покупка: ')
    )

    keyboard = [
        [
            admin_button(
                _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_076', 'Редактировать')),
                callback_data=f'promo_edit_{promo.id}',
                icon='edit',
            ),
            admin_button(
                _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_077', 'Переключить статус')),
                callback_data=f'promo_toggle_{promo.id}',
                icon='refresh',
            ),
        ],
        [admin_button(first_purchase_btn_text, callback_data=f'promo_toggle_first_{promo.id}', icon='star')],
        [
            admin_button(
                _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_078', 'Статистика')),
                callback_data=f'promo_stats_{promo.id}',
                icon='stats',
            ),
            admin_button(
                _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_079', 'Удалить')),
                callback_data=f'promo_delete_{promo.id}',
                icon='delete',
            ),
        ],
        [
            admin_back_button(
                texts, 'admin_promo_list', label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_080', 'К списку'))
            )
        ],
    ]

    await callback.message.edit_text(
        _screen(text, icon='promo'),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_promocode_edit_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_081', 'Ошибка получения ID промокода'), show_alert=True
        )
        return

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_062', 'Промокод не найден'), show_alert=True
        )
        return

    texts = get_texts(db_user.language)
    text = f"""<b>Редактирование промокода</b> <code>{promo.code}</code>

<b>Текущие параметры:</b>
"""

    if promo.type == PromoCodeType.BALANCE.value:
        text += (
            get_texts(db_user.language)
            .t('ADMIN_PROMOCODE_UI_021', '• Бонус: {v0}\n')
            .format(v0=settings.format_price(promo.balance_bonus_kopeks))
        )
    elif promo.type in [PromoCodeType.SUBSCRIPTION_DAYS.value, PromoCodeType.TRIAL_SUBSCRIPTION.value]:
        text += (
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_042', '• Дней: {v0}\n').format(v0=promo.subscription_days)
        )
    elif promo.type == PromoCodeType.BALANCE_AND_DAYS.value:
        if promo.balance_bonus_kopeks:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_021', '• Бонус: {v0}\n')
                .format(v0=settings.format_price(promo.balance_bonus_kopeks))
            )
        if promo.subscription_days:
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_042', '• Дней: {v0}\n')
                .format(v0=promo.subscription_days)
            )
        if getattr(promo, 'traffic_gb', 0):
            text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_082', '• Трафик: {v0} ГБ\n')
                .format(v0=promo.traffic_gb)
            )

    text += (
        get_texts(db_user.language)
        .t('ADMIN_PROMOCODE_UI_006', '• Использований: {v0}/{v1}\n')
        .format(v0=promo.current_uses, v1=promo.max_uses)
    )

    if promo.valid_until:
        text += (
            get_texts(db_user.language)
            .t('ADMIN_PROMOCODE_UI_022', '• До: {v0}\n')
            .format(v0=format_datetime(promo.valid_until))
        )
    else:
        text += get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_023', '• Срок: бессрочно\n')

    hint = get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_007', 'Выберите параметр для изменения:')

    keyboard = [
        [
            admin_button(
                _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_083', 'Дата окончания')),
                callback_data=f'promo_edit_date_{promo.id}',
                icon='calendar',
            )
        ],
        [
            admin_button(
                _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_084', 'Количество использований')),
                callback_data=f'promo_edit_uses_{promo.id}',
                icon='chart',
            )
        ],
    ]

    if promo.type == PromoCodeType.BALANCE.value:
        keyboard.insert(
            1,
            [
                admin_button(
                    _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_104', 'Сумма бонуса')),
                    callback_data=f'promo_edit_amount_{promo.id}',
                    icon='money',
                )
            ],
        )
    elif promo.type in [PromoCodeType.SUBSCRIPTION_DAYS.value, PromoCodeType.TRIAL_SUBSCRIPTION.value]:
        keyboard.insert(
            1,
            [
                admin_button(
                    _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_110', 'Количество дней')),
                    callback_data=f'promo_edit_days_{promo.id}',
                    icon='calendar',
                )
            ],
        )
    elif promo.type == PromoCodeType.BALANCE_AND_DAYS.value:
        keyboard.insert(
            1,
            [
                admin_button(
                    _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_104', 'Сумма бонуса')),
                    callback_data=f'promo_edit_amount_{promo.id}',
                    icon='money',
                )
            ],
        )
        keyboard.insert(
            2,
            [
                admin_button(
                    _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_110', 'Количество дней')),
                    callback_data=f'promo_edit_days_{promo.id}',
                    icon='calendar',
                )
            ],
        )

    keyboard.extend([[admin_back_button(texts, f'promo_manage_{promo.id}')]])

    await callback.message.edit_text(
        f'{_screen(text, icon="edit")}\n\n{hint}',
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def start_edit_promocode_date(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_081', 'Ошибка получения ID промокода'), show_alert=True
        )
        return

    await state.update_data(editing_promo_id=promo_id, edit_action='date')

    texts = get_texts(db_user.language)
    text = f"""<b>Изменение даты окончания промокода</b>

Введите количество дней до окончания (от текущего момента):
• Введите <b>0</b> для бессрочного промокода
• Введите положительное число для установки срока

<i>Например: 30 (промокод будет действовать 30 дней)</i>

ID промокода: {promo_id}
"""

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_back_button(
                    texts,
                    f'promo_edit_{promo_id}',
                    label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_111', 'Отмена')),
                )
            ]
        ]
    )

    await callback.message.edit_text(
        admin_screen(text, icon='calendar', compact=True),
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await state.set_state(AdminStates.setting_promocode_expiry)
    await callback.answer()


@admin_required
@error_handler
async def start_edit_promocode_amount(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_081', 'Ошибка получения ID промокода'), show_alert=True
        )
        return

    await state.update_data(editing_promo_id=promo_id, edit_action='amount')

    texts = get_texts(db_user.language)
    text = f"""<b>Изменение суммы бонуса промокода</b>

Введите новую сумму в рублях:
<i>Например: 500</i>

ID промокода: {promo_id}
"""

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_back_button(
                    texts,
                    f'promo_edit_{promo_id}',
                    label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_111', 'Отмена')),
                )
            ]
        ]
    )

    await callback.message.edit_text(
        admin_screen(text, icon='money', compact=True),
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await state.set_state(AdminStates.setting_promocode_value)
    await callback.answer()


@admin_required
@error_handler
async def start_edit_promocode_days(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    # ИСПРАВЛЕНИЕ: берем последний элемент как ID
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_081', 'Ошибка получения ID промокода'), show_alert=True
        )
        return

    await state.update_data(editing_promo_id=promo_id, edit_action='days')

    texts = get_texts(db_user.language)
    text = f"""<b>Изменение количества дней подписки</b>

Введите новое количество дней:
<i>Например: 30</i>

ID промокода: {promo_id}
"""

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_back_button(
                    texts,
                    f'promo_edit_{promo_id}',
                    label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_111', 'Отмена')),
                )
            ]
        ]
    )

    await callback.message.edit_text(
        admin_screen(text, icon='calendar', compact=True),
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await state.set_state(AdminStates.setting_promocode_value)
    await callback.answer()


@admin_required
@error_handler
async def start_edit_promocode_uses(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_081', 'Ошибка получения ID промокода'), show_alert=True
        )
        return

    await state.update_data(editing_promo_id=promo_id, edit_action='uses')

    texts = get_texts(db_user.language)
    text = f"""<b>Изменение максимального количества использований</b>

Введите новое количество использований:
• Введите <b>0</b> для безлимитных использований
• Введите положительное число для ограничения

<i>Например: 100</i>

ID промокода: {promo_id}
"""

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_back_button(
                    texts,
                    f'promo_edit_{promo_id}',
                    label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_111', 'Отмена')),
                )
            ]
        ]
    )

    await callback.message.edit_text(
        admin_screen(text, icon='chart', compact=True),
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await state.set_state(AdminStates.setting_promocode_uses)
    await callback.answer()


@admin_required
@error_handler
async def start_promocode_creation(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    texts = get_texts(db_user.language)
    await callback.message.edit_text(
        admin_screen(
            texts.t('ADMIN_PROMOCODE_UI_043', '<b>Создание промокода</b>\n\nВыберите тип промокода:'),
            icon='promo',
            compact=True,
        ),
        reply_markup=get_promocode_type_keyboard(db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def select_promocode_type(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    promo_type = callback.data.split('_')[-1]
    texts = get_texts(db_user.language)

    type_names = {
        'balance': texts.t('ADMIN_PROMOCODE_UI_024', 'Пополнение баланса'),
        'days': texts.t('ADMIN_PROMOCODE_UI_025', 'Дни подписки'),
        'trial': texts.t('ADMIN_PROMOCODE_UI_026', 'Тестовая подписка'),
        'group': texts.t('ADMIN_PROMOCODE_UI_027', 'Промогруппа'),
        'discount': texts.t('ADMIN_PROMOCODE_UI_028', 'Одноразовая скидка'),
        'combo': texts.t('ADMIN_PROMOCODE_UI_029', 'Баланс + дни подписки'),
    }

    await state.update_data(promocode_type=promo_type)

    await callback.message.edit_text(
        admin_screen(
            texts.t(
                'ADMIN_PROMOCODE_UI_044',
                '<b>Создание промокода</b>\n\nТип: {v0}\n\nВведите код промокода (только латинские буквы и цифры):',
            ).format(v0=type_names.get(promo_type, promo_type)),
            icon='promo',
            compact=True,
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_back_button(
                        texts,
                        'admin_promocodes',
                        label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_111', 'Отмена')),
                    )
                ]
            ]
        ),
        parse_mode='HTML',
    )

    await state.set_state(AdminStates.creating_promocode)
    await callback.answer()


@admin_required
@error_handler
async def process_promocode_code(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    code = message.text.strip().upper()
    texts = get_texts(db_user.language)

    if not code.isalnum() or len(code) < 3 or len(code) > 20:
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_PROMOCODE_UI_065', 'Код должен содержать только латинские буквы и цифры (3-20 символов)'
                ),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    existing = await get_promocode_by_code(db, code)
    if existing:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_PROMOCODE_UI_066', 'Промокод с таким кодом уже существует'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    await state.update_data(promocode_code=code)

    data = await state.get_data()
    promo_type = data.get('promocode_type')

    if promo_type == 'balance':
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_PROMOCODE_UI_067',
                    '<b>Промокод:</b> <code>{v0}</code>\n\nВведите сумму пополнения баланса (в рублях):',
                ).format(v0=code),
                icon='money',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.set_state(AdminStates.setting_promocode_value)
    elif promo_type == 'combo':
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_PROMOCODE_UI_085',
                    '<b>Промокод:</b> <code>{v0}</code>\n\nШаг 1 из 2: введите сумму пополнения баланса (в рублях), дни подписки спрошу следующим шагом:',
                ).format(v0=code),
                icon='coin',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.set_state(AdminStates.setting_promocode_value)
    elif promo_type == 'days':
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_PROMOCODE_UI_105', '<b>Промокод:</b> <code>{v0}</code>\n\nВведите количество дней подписки:'
                ).format(v0=code),
                icon='calendar',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.set_state(AdminStates.setting_promocode_value)
    elif promo_type == 'trial':
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_PROMOCODE_UI_112',
                    '<b>Промокод:</b> <code>{v0}</code>\n\nВведите количество дней тестовой подписки:',
                ).format(v0=code),
                icon='trials',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.set_state(AdminStates.setting_promocode_value)
    elif promo_type == 'discount':
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_PROMOCODE_UI_118', '<b>Промокод:</b> <code>{v0}</code>\n\nВведите процент скидки (1-100):'
                ).format(v0=code),
                icon='star',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.set_state(AdminStates.setting_promocode_value)
    elif promo_type == 'group':
        # Show promo group selection
        groups_with_counts = await get_promo_groups_with_counts(db, limit=50)

        if not groups_with_counts:
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_PROMOCODE_UI_120', 'Промогруппы не найдены. Создайте хотя бы одну промогруппу.'),
                    icon='promo',
                    compact=True,
                ),
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[admin_back_button(texts, 'admin_promocodes')]]
                ),
                parse_mode='HTML',
            )
            await state.clear()
            return

        keyboard = []
        text = texts.t(
            'ADMIN_PROMOCODE_UI_113',
            '<b>Промокод:</b> <code>{v0}</code>\n\nВыберите промогруппу для назначения:\n\n',
        ).format(v0=code)

        for promo_group, user_count in groups_with_counts:
            text += texts.t('ADMIN_PROMOCODE_UI_119', '• {v0} (приоритет: {v1}, пользователей: {v2})\n').format(
                v0=html.escape(promo_group.name), v1=promo_group.priority, v2=user_count
            )
            keyboard.append(
                [
                    admin_button(
                        f'{promo_group.name} (↑{promo_group.priority})',
                        callback_data=f'promo_select_group_{promo_group.id}',
                        icon='users',
                    )
                ]
            )

        keyboard.append(
            [
                admin_back_button(
                    texts,
                    'admin_promocodes',
                    label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_111', 'Отмена')),
                )
            ]
        )

        await message.answer(
            admin_screen(_clean_legacy(text), icon='promo', compact=True),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode='HTML',
        )
        await state.set_state(AdminStates.selecting_promo_group)


@admin_required
@error_handler
async def process_promo_group_selection(
    callback: types.CallbackQuery, db_user: User, state: FSMContext, db: AsyncSession
):
    """Handle promo group selection for promocode"""
    try:
        promo_group_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_086', 'Ошибка получения ID промогруппы'),
            show_alert=True,
        )
        return

    promo_group = await get_promo_group_by_id(db, promo_group_id)
    if not promo_group:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_068', 'Промогруппа не найдена'), show_alert=True
        )
        return

    await state.update_data(promo_group_id=promo_group_id, promo_group_name=promo_group.name)

    await callback.message.edit_text(
        admin_screen(
            get_texts(db_user.language)
            .t(
                'ADMIN_PROMOCODE_UI_045',
                '<b>Промокод для промогруппы</b>\n\nПромогруппа: {v0}\nПриоритет: {v1}\n\nВведите количество использований промокода (или 0 для безлимита):',
            )
            .format(v0=html.escape(promo_group.name), v1=promo_group.priority),
            icon='promo',
            compact=True,
        ),
        parse_mode='HTML',
    )

    await state.set_state(AdminStates.setting_promocode_uses)
    await callback.answer()


@admin_required
@error_handler
async def process_promocode_value(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()

    if data.get('editing_promo_id'):
        await handle_edit_value(message, db_user, state, db)
        return

    try:
        value = int(message.text.strip())

        promo_type = data.get('promocode_type')
        texts = get_texts(db_user.language)

        if promo_type in ['balance', 'combo'] and (value < 1 or value > 10000):
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_PROMOCODE_UI_087', 'Сумма должна быть от 1 до 10,000 рублей'),
                    icon='warning',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return
        if promo_type in ['days', 'trial'] and (value < 1 or value > 3650):
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_PROMOCODE_UI_088', 'Количество дней должно быть от 1 до 3650'),
                    icon='warning',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return
        if promo_type == 'discount' and (value < 1 or value > 100):
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_PROMOCODE_UI_089', 'Процент скидки должен быть от 1 до 100'),
                    icon='warning',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        await state.update_data(promocode_value=value)

        # Для комбинированного типа сумма — только первый шаг, дальше дни
        if promo_type == 'combo':
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_PROMOCODE_UI_090', 'Шаг 2 из 2: введите количество дней подписки:'),
                    icon='coin',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            await state.set_state(AdminStates.setting_promocode_combo_days)
            return

        await message.answer(
            admin_screen(
                texts.t('ADMIN_PROMOCODE_UI_069', 'Введите количество использований промокода (или 0 для безлимита):'),
                icon='chart',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.set_state(AdminStates.setting_promocode_uses)

    except ValueError:
        await message.answer(
            admin_screen(
                get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_091', 'Введите корректное число'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def process_promocode_combo_days(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    """Шаг 2 комбинированного промокода (BALANCE_AND_DAYS): ввод дней подписки."""
    try:
        days = int(message.text.strip())

        if days < 1 or days > 3650:
            await message.answer(
                admin_screen(
                    get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_088', 'Количество дней должно быть от 1 до 3650'),
                    icon='warning',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        await state.update_data(promocode_combo_days=days)

        await message.answer(
            admin_screen(
                get_texts(db_user.language).t(
                    'ADMIN_PROMOCODE_UI_069', 'Введите количество использований промокода (или 0 для безлимита):'
                ),
                icon='chart',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.set_state(AdminStates.setting_promocode_uses)

    except ValueError:
        await message.answer(
            admin_screen(
                get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_092', 'Введите корректное число дней'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )


async def handle_edit_value(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()
    promo_id = data.get('editing_promo_id')
    edit_action = data.get('edit_action')

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await message.answer(
            admin_screen(
                get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_062', 'Промокод не найден'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.clear()
        return

    texts = get_texts(db_user.language)

    try:
        value = int(message.text.strip())

        if edit_action == 'amount':
            if value < 1 or value > 10000:
                await message.answer(
                    admin_screen(
                        texts.t('ADMIN_PROMOCODE_UI_087', 'Сумма должна быть от 1 до 10,000 рублей'),
                        icon='warning',
                        compact=True,
                    ),
                    parse_mode='HTML',
                )
                return

            await update_promocode(db, promo, balance_bonus_kopeks=value * 100)
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_PROMOCODE_UI_093', 'Сумма бонуса изменена на {v0}₽').format(v0=value),
                    icon='success',
                    compact=True,
                ),
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            admin_back_button(
                                texts,
                                f'promo_manage_{promo_id}',
                                label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_121', 'К промокоду')),
                            )
                        ]
                    ]
                ),
                parse_mode='HTML',
            )

        elif edit_action == 'days':
            if value < 1 or value > 3650:
                await message.answer(
                    admin_screen(
                        texts.t('ADMIN_PROMOCODE_UI_088', 'Количество дней должно быть от 1 до 3650'),
                        icon='warning',
                        compact=True,
                    ),
                    parse_mode='HTML',
                )
                return

            await update_promocode(db, promo, subscription_days=value)
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_PROMOCODE_UI_106', 'Количество дней изменено на {v0}').format(v0=value),
                    icon='success',
                    compact=True,
                ),
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            admin_back_button(
                                texts,
                                f'promo_manage_{promo_id}',
                                label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_121', 'К промокоду')),
                            )
                        ]
                    ]
                ),
                parse_mode='HTML',
            )

        await state.clear()
        logger.info(
            'Промокод отредактирован администратором',
            code=promo.code,
            telegram_id=db_user.telegram_id,
            edit_action=edit_action,
            value=value,
        )

    except ValueError:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_PROMOCODE_UI_091', 'Введите корректное число'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def process_promocode_uses(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()

    if data.get('editing_promo_id'):
        await handle_edit_uses(message, db_user, state, db)
        return

    try:
        max_uses = int(message.text.strip())

        if max_uses < 0 or max_uses > 100000:
            await message.answer(
                admin_screen(
                    get_texts(db_user.language).t(
                        'ADMIN_PROMOCODE_UI_094', 'Количество использований должно быть от 0 до 100,000'
                    ),
                    icon='warning',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        if max_uses == 0:
            max_uses = 999999

        await state.update_data(promocode_max_uses=max_uses)

        await message.answer(
            admin_screen(
                get_texts(db_user.language).t(
                    'ADMIN_PROMOCODE_UI_070', 'Введите срок действия промокода в днях (или 0 для бессрочного):'
                ),
                icon='calendar',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.set_state(AdminStates.setting_promocode_expiry)

    except ValueError:
        await message.answer(
            admin_screen(
                get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_091', 'Введите корректное число'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )


async def handle_edit_uses(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()
    promo_id = data.get('editing_promo_id')

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await message.answer(
            admin_screen(
                get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_062', 'Промокод не найден'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.clear()
        return

    texts = get_texts(db_user.language)

    try:
        max_uses = int(message.text.strip())

        if max_uses < 0 or max_uses > 100000:
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_PROMOCODE_UI_094', 'Количество использований должно быть от 0 до 100,000'),
                    icon='warning',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        if max_uses == 0:
            max_uses = 999999

        if max_uses < promo.current_uses:
            await message.answer(
                admin_screen(
                    texts.t(
                        'ADMIN_PROMOCODE_UI_095', 'Новый лимит ({v0}) не может быть меньше текущих использований ({v1})'
                    ).format(v0=max_uses, v1=promo.current_uses),
                    icon='warning',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        await update_promocode(db, promo, max_uses=max_uses)

        uses_text = texts.t('ADMIN_PROMOCODE_UI_046', 'безлимитное') if max_uses == 999999 else str(max_uses)
        await message.answer(
            admin_screen(
                texts.t('ADMIN_PROMOCODE_UI_071', 'Максимальное количество использований изменено на {v0}').format(
                    v0=uses_text
                ),
                icon='success',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            f'promo_manage_{promo_id}',
                            label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_121', 'К промокоду')),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )

        await state.clear()
        logger.info(
            'Промокод отредактирован администратором max_uses',
            code=promo.code,
            telegram_id=db_user.telegram_id,
            max_uses=max_uses,
        )

    except ValueError:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_PROMOCODE_UI_091', 'Введите корректное число'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def process_promocode_expiry(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()

    if data.get('editing_promo_id'):
        await handle_edit_expiry(message, db_user, state, db)
        return

    try:
        expiry_days = int(message.text.strip())

        if expiry_days < 0 or expiry_days > 3650:
            await message.answer(
                admin_screen(
                    get_texts(db_user.language).t(
                        'ADMIN_PROMOCODE_UI_096', 'Срок действия должен быть от 0 до 3650 дней'
                    ),
                    icon='warning',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        code = data.get('promocode_code')
        promo_type = data.get('promocode_type')
        value = data.get('promocode_value', 0)
        max_uses = data.get('promocode_max_uses', 1)
        promo_group_id = data.get('promo_group_id')
        promo_group_name = data.get('promo_group_name')

        # Для DISCOUNT типа нужно дополнительно спросить срок действия скидки в часах
        if promo_type == 'discount':
            await state.update_data(promocode_expiry_days=expiry_days)
            await message.answer(
                admin_screen(
                    get_texts(db_user.language)
                    .t(
                        'ADMIN_PROMOCODE_UI_097',
                        '<b>Промокод:</b> <code>{v0}</code>\n\nВведите срок действия скидки в часах (0-8760):\n0 = бессрочно до первой покупки',
                    )
                    .format(v0=code),
                    icon='clock',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            await state.set_state(AdminStates.setting_discount_hours)
            return

        valid_until = None
        if expiry_days > 0:
            valid_until = datetime.now(UTC) + timedelta(days=expiry_days)

        type_map = {
            'balance': PromoCodeType.BALANCE,
            'days': PromoCodeType.SUBSCRIPTION_DAYS,
            'trial': PromoCodeType.TRIAL_SUBSCRIPTION,
            'group': PromoCodeType.PROMO_GROUP,
            'combo': PromoCodeType.BALANCE_AND_DAYS,
        }

        if promo_type == 'combo':
            balance_bonus_kopeks = value * 100
            subscription_days = data.get('promocode_combo_days', 0)
        else:
            balance_bonus_kopeks = value * 100 if promo_type == 'balance' else 0
            subscription_days = value if promo_type in ['days', 'trial'] else 0

        promocode = await create_promocode(
            db=db,
            code=code,
            type=type_map[promo_type],
            balance_bonus_kopeks=balance_bonus_kopeks,
            subscription_days=subscription_days,
            max_uses=max_uses,
            valid_until=valid_until,
            created_by=db_user.id,
            promo_group_id=promo_group_id if promo_type == 'group' else None,
        )

        type_names = {
            'balance': get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_047', 'Пополнение баланса'),
            'days': get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_048', 'Дни подписки'),
            'trial': get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_049', 'Тестовая подписка'),
            'group': get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_050', 'Промогруппа'),
            'combo': get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_051', 'Баланс + дни подписки'),
        }

        summary_text = f"""<b>Промокод создан!</b>

<b>Код:</b> <code>{promocode.code}</code>
<b>Тип:</b> {type_names.get(promo_type)}
"""

        if promo_type == 'balance':
            summary_text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_052', '<b>Сумма:</b> {v0}\n')
                .format(v0=settings.format_price(promocode.balance_bonus_kopeks))
            )
        elif promo_type in ['days', 'trial']:
            summary_text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_041', '<b>Дней:</b> {v0}\n')
                .format(v0=promocode.subscription_days)
            )
        elif promo_type == 'combo':
            summary_text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_052', '<b>Сумма:</b> {v0}\n')
                .format(v0=settings.format_price(promocode.balance_bonus_kopeks))
            )
            summary_text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_041', '<b>Дней:</b> {v0}\n')
                .format(v0=promocode.subscription_days)
            )
        elif promo_type == 'group' and promo_group_name:
            summary_text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_017', '<b>Промогруппа:</b> {v0}\n')
                .format(v0=promo_group_name)
            )

        summary_text += (
            get_texts(db_user.language)
            .t('ADMIN_PROMOCODE_UI_031', '<b>Использований:</b> {v0}\n')
            .format(v0=promocode.max_uses)
        )

        if promocode.valid_until:
            summary_text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_018', '<b>Действует до:</b> {v0}\n')
                .format(v0=format_datetime(promocode.valid_until))
            )

        await message.answer(
            admin_screen(_clean_legacy(summary_text), icon='success', compact=True),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            get_texts(db_user.language),
                            'admin_promocodes',
                            label=_clean_legacy(
                                get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_115', 'К промокодам')
                            ),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )

        await state.clear()
        logger.info('Создан промокод администратором', code=code, telegram_id=db_user.telegram_id)

    except ValueError:
        await message.answer(
            admin_screen(
                get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_092', 'Введите корректное число дней'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def process_discount_hours(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    """Обработчик ввода срока действия скидки в часах для DISCOUNT промокода."""
    data = await state.get_data()

    try:
        discount_hours = int(message.text.strip())

        if discount_hours < 0 or discount_hours > 8760:
            await message.answer(
                admin_screen(
                    get_texts(db_user.language).t(
                        'ADMIN_PROMOCODE_UI_098', 'Срок действия скидки должен быть от 0 до 8760 часов'
                    ),
                    icon='warning',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        code = data.get('promocode_code')
        value = data.get('promocode_value', 0)  # Процент скидки
        max_uses = data.get('promocode_max_uses', 1)
        expiry_days = data.get('promocode_expiry_days', 0)

        valid_until = None
        if expiry_days > 0:
            valid_until = datetime.now(UTC) + timedelta(days=expiry_days)

        # Создаем DISCOUNT промокод
        # balance_bonus_kopeks = процент скидки (НЕ копейки!)
        # subscription_days = срок действия скидки в часах (НЕ дни!)
        promocode = await create_promocode(
            db=db,
            code=code,
            type=PromoCodeType.DISCOUNT,
            balance_bonus_kopeks=value,  # Процент (1-100)
            subscription_days=discount_hours,  # Часы (0-8760)
            max_uses=max_uses,
            valid_until=valid_until,
            created_by=db_user.id,
            promo_group_id=None,
        )

        summary_text = f"""<b>Промокод создан!</b>

<b>Код:</b> <code>{promocode.code}</code>
<b>Тип:</b> Одноразовая скидка
<b>Скидка:</b> {promocode.balance_bonus_kopeks}%
"""

        if discount_hours > 0:
            summary_text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_053', '<b>Срок скидки:</b> {v0} ч.\n')
                .format(v0=discount_hours)
            )
        else:
            summary_text += get_texts(db_user.language).t(
                'ADMIN_PROMOCODE_UI_054', '<b>Срок скидки:</b> до первой покупки\n'
            )

        summary_text += (
            get_texts(db_user.language)
            .t('ADMIN_PROMOCODE_UI_031', '<b>Использований:</b> {v0}\n')
            .format(v0=promocode.max_uses)
        )

        if promocode.valid_until:
            summary_text += (
                get_texts(db_user.language)
                .t('ADMIN_PROMOCODE_UI_055', '<b>Промокод действует до:</b> {v0}\n')
                .format(v0=format_datetime(promocode.valid_until))
            )

        await message.answer(
            admin_screen(_clean_legacy(summary_text), icon='success', compact=True),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            get_texts(db_user.language),
                            'admin_promocodes',
                            label=_clean_legacy(
                                get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_115', 'К промокодам')
                            ),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )

        await state.clear()
        logger.info(
            'Создан DISCOUNT промокод (%, ч) администратором',
            code=code,
            value=value,
            discount_hours=discount_hours,
            telegram_id=db_user.telegram_id,
        )

    except ValueError:
        await message.answer(
            admin_screen(
                get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_099', 'Введите корректное число часов'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )


async def handle_edit_expiry(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()
    promo_id = data.get('editing_promo_id')

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await message.answer(
            admin_screen(
                get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_062', 'Промокод не найден'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.clear()
        return

    texts = get_texts(db_user.language)

    try:
        expiry_days = int(message.text.strip())

        if expiry_days < 0 or expiry_days > 3650:
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_PROMOCODE_UI_096', 'Срок действия должен быть от 0 до 3650 дней'),
                    icon='warning',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        valid_until = None
        if expiry_days > 0:
            valid_until = datetime.now(UTC) + timedelta(days=expiry_days)

        await update_promocode(db, promo, valid_until=valid_until)

        if valid_until:
            expiry_text = texts.t('ADMIN_PROMOCODE_UI_056', 'до {v0}').format(v0=format_datetime(valid_until))
        else:
            expiry_text = texts.t('ADMIN_PROMOCODE_UI_057', 'бессрочно')

        await message.answer(
            admin_screen(
                texts.t('ADMIN_PROMOCODE_UI_072', 'Срок действия промокода изменен: {v0}').format(v0=expiry_text),
                icon='success',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            f'promo_manage_{promo_id}',
                            label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_121', 'К промокоду')),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )

        await state.clear()
        logger.info(
            'Промокод отредактирован администратором expiry дней',
            code=promo.code,
            telegram_id=db_user.telegram_id,
            expiry_days=expiry_days,
        )

    except ValueError:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_PROMOCODE_UI_092', 'Введите корректное число дней'),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def toggle_promocode_status(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    promo_id = int(callback.data.split('_')[-1])

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_062', 'Промокод не найден'), show_alert=True
        )
        return

    new_status = not promo.is_active
    await update_promocode(db, promo, is_active=new_status)

    status_text = (
        get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_033', 'активирован')
        if new_status
        else get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_034', 'деактивирован')
    )
    await callback.answer(
        get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_058', 'Промокод {v0}').format(v0=status_text),
        show_alert=True,
    )

    await show_promocode_management(callback, db_user, db)


@admin_required
@error_handler
async def toggle_promocode_first_purchase(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Переключает режим 'только для первой покупки'."""
    promo_id = int(callback.data.split('_')[-1])

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_062', 'Промокод не найден'), show_alert=True
        )
        return

    new_status = not getattr(promo, 'first_purchase_only', False)
    await update_promocode(db, promo, first_purchase_only=new_status)

    status_text = (
        get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_035', 'включён')
        if new_status
        else get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_036', 'выключен')
    )
    await callback.answer(
        get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_059', " Режим 'первая покупка' {v0}").format(v0=status_text),
        show_alert=True,
    )

    await show_promocode_management(callback, db_user, db)


@admin_required
@error_handler
async def confirm_delete_promocode(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_081', 'Ошибка получения ID промокода'), show_alert=True
        )
        return

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_062', 'Промокод не найден'), show_alert=True
        )
        return

    texts = get_texts(db_user.language)
    details = (
        f'{admin_icon("promo")} Код: <code>{html.escape(promo.code)}</code>\n'
        f'{admin_icon("chart")} Использований: <b>{promo.current_uses}/{promo.max_uses}</b>\n'
        f'{admin_icon("success") if promo.is_active else admin_icon("prohibit")} '
        f'Статус: {"Активен" if promo.is_active else "Неактивен"}\n'
        f'{admin_icon("id")} ID: <code>{promo.id}</code>'
    )
    text = (
        f'{admin_heading("warning", "Подтверждение удаления")}\n\n'
        f'{admin_quote(details)}\n\n'
        f'Удалить промокод <code>{html.escape(promo.code)}</code>?\n'
        f'{admin_icon("warning")} Это действие нельзя отменить.'
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_114', 'Да, удалить')),
                    callback_data=f'promo_delete_confirm_{promo.id}',
                    icon='delete',
                ),
                admin_back_button(
                    texts,
                    f'promo_manage_{promo.id}',
                    label=_clean_legacy(texts.t('ADMIN_PROMOCODE_UI_111', 'Отмена')),
                ),
            ]
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_promocode_confirmed(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_081', 'Ошибка получения ID промокода'), show_alert=True
        )
        return

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_062', 'Промокод не найден'), show_alert=True
        )
        return

    code = promo.code
    success = await delete_promocode(db, promo)

    if success:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_073', 'Промокод {v0} удален').format(v0=code),
            show_alert=True,
        )
        await show_promocodes_list(callback, db_user, db)
    else:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_074', 'Ошибка удаления промокода'), show_alert=True
        )


@admin_required
@error_handler
async def show_promocode_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    promo_id = int(callback.data.split('_')[-1])

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_PROMOCODE_UI_062', 'Промокод не найден'), show_alert=True
        )
        return

    stats = await get_promocode_statistics(db, promo_id)

    texts = get_texts(db_user.language)
    overview = (
        f'{admin_icon("chart")} Всего использований: <b>{stats["total_uses"]}</b>\n'
        f'{admin_icon("calendar")} Использований сегодня: <b>{stats["today_uses"]}</b>\n'
        f'{admin_icon("infinity")} Осталось использований: <b>{promo.max_uses - promo.current_uses}</b>'
    )
    heading = admin_heading('chart', f'Статистика промокода {promo.code}')
    blocks = [heading, admin_quote(overview)]

    uses: list[str] = []
    if stats['recent_uses']:
        for use in stats['recent_uses'][:5]:
            use_date = format_datetime(use.used_at)

            if hasattr(use, 'user_username') and use.user_username:
                user_display = f'@{html.escape(use.user_username)}'
            elif hasattr(use, 'user_full_name') and use.user_full_name:
                user_display = html.escape(use.user_full_name)
            elif hasattr(use, 'user_telegram_id'):
                user_display = f'ID{use.user_telegram_id}'
            else:
                user_display = f'ID{use.user_id}'

            uses.append(f'{admin_icon("user")} {use_date} — {user_display}')
    else:
        uses.append(
            f'{admin_icon("info")} ' + _clean_legacy(texts.t('ADMIN_PROMOCODE_UI_037', 'Пока не было использований'))
        )

    blocks.append('\n'.join(uses))

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[admin_back_button(texts, f'promo_manage_{promo.id}')]])

    await callback.message.edit_text(
        '\n\n'.join(blocks),
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_general_promocode_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    total_codes = await get_promocodes_count(db)
    active_codes = await get_promocodes_count(db, is_active=True)

    texts = get_texts(db_user.language)
    overview = (
        f'{admin_icon("promo")} Всего промокодов: <b>{total_codes}</b>\n'
        f'{admin_icon("success")} Активных: <b>{active_codes}</b>\n'
        f'{admin_icon("prohibit")} Неактивных: <b>{total_codes - active_codes}</b>'
    )
    text = (
        f'{admin_heading("chart", "Общая статистика промокодов")}\n\n{admin_quote(overview)}\n\n'
        'Для детальной статистики выберите конкретный промокод из списка.'
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    'К промокодам',
                    callback_data='admin_promo_list',
                    icon='promo',
                )
            ],
            [admin_back_button(texts, 'admin_promocodes')],
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_promocodes_menu, F.data == 'admin_promocodes')
    dp.callback_query.register(show_promocodes_list, F.data == 'admin_promo_list')
    dp.callback_query.register(show_promocodes_list_page, F.data.startswith('admin_promo_list_page_'))
    dp.callback_query.register(start_promocode_creation, F.data == 'admin_promo_create')
    dp.callback_query.register(select_promocode_type, F.data.startswith('promo_type_'))
    dp.callback_query.register(process_promo_group_selection, F.data.startswith('promo_select_group_'))

    dp.callback_query.register(show_promocode_management, F.data.startswith('promo_manage_'))
    dp.callback_query.register(toggle_promocode_first_purchase, F.data.startswith('promo_toggle_first_'))
    dp.callback_query.register(toggle_promocode_status, F.data.startswith('promo_toggle_'))
    dp.callback_query.register(show_promocode_stats, F.data.startswith('promo_stats_'))

    dp.callback_query.register(start_edit_promocode_date, F.data.startswith('promo_edit_date_'))
    dp.callback_query.register(start_edit_promocode_amount, F.data.startswith('promo_edit_amount_'))
    dp.callback_query.register(start_edit_promocode_days, F.data.startswith('promo_edit_days_'))
    dp.callback_query.register(start_edit_promocode_uses, F.data.startswith('promo_edit_uses_'))
    dp.callback_query.register(show_general_promocode_stats, F.data == 'admin_promo_general_stats')

    dp.callback_query.register(show_promocode_edit_menu, F.data.regexp(r'^promo_edit_\d+$'))

    dp.callback_query.register(delete_promocode_confirmed, F.data.startswith('promo_delete_confirm_'))
    dp.callback_query.register(confirm_delete_promocode, F.data.startswith('promo_delete_'))

    dp.message.register(process_promocode_code, AdminStates.creating_promocode)
    dp.message.register(process_promocode_value, AdminStates.setting_promocode_value)
    dp.message.register(process_promocode_combo_days, AdminStates.setting_promocode_combo_days)
    dp.message.register(process_promocode_uses, AdminStates.setting_promocode_uses)
    dp.message.register(process_promocode_expiry, AdminStates.setting_promocode_expiry)
    dp.message.register(process_discount_hours, AdminStates.setting_discount_hours)
