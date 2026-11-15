import html
import re

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.promo_group import get_promo_groups_with_counts
from app.database.crud.server_squad import (
    delete_server_squad,
    get_all_server_squads,
    get_available_server_squads,
    get_server_connected_users,
    get_server_squad_by_id,
    get_server_statistics,
    sync_with_remnawave,
    update_server_squad,
    update_server_squad_promo_groups,
)
from app.database.models import User
from app.localization.texts import get_texts
from app.services.remnawave_service import RemnaWaveService
from app.states import AdminStates
from app.utils.admin_ui import (
    admin_back_button as _admin_back_button,
    admin_button,
    admin_heading,
    admin_icon,
    admin_quote,
    admin_screen,
)
from app.utils.cache import cache
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)

# Локали хранят следы прежнего оформления: декоративные глифы и метку NEW.
# Иконку и отступы даёт admin_screen, поэтому префиксы строк срезаем.
_LEGACY_PREFIX_RE = re.compile(r'^[ \t]*(?:[▤▣▥◷↻↑↓]\s*|NEW\s+)+', re.MULTILINE)
_LEGACY_BULLET_RE = re.compile(r'(?<=• )(?:[▤▣▥◷↻↑↓]\s*|NEW\s+)+')
_BACK_ARROWS = ('←', '◀', '⬅', '⏪')


def _clean_legacy(value: str) -> str:
    return _LEGACY_BULLET_RE.sub('', _LEGACY_PREFIX_RE.sub('', value)).strip()


def admin_back_button(texts, callback_data: str, *, label: str | None = None):
    """Кнопка возврата: стрелка в тексте, без premium-иконки и цвета."""
    text = label or texts.BACK
    if not text.lstrip().startswith(_BACK_ARROWS):
        text = f'← {text}'
    return _admin_back_button(texts, callback_data, label=text)


def _build_server_edit_view(server, language: str = 'ru'):
    texts = get_texts(language)
    status_text = (
        texts.t('ADMIN_SERVER_UI_005', 'Доступен')
        if server.is_available
        else texts.t('ADMIN_SERVER_UI_006', 'Недоступен')
    )
    status_icon = admin_icon('success') if server.is_available else admin_icon('error')
    price_text = (
        f'{int(server.price_rubles)} ₽' if server.price_kopeks > 0 else texts.t('ADMIN_SERVER_UI_007', 'Бесплатно')
    )
    promo_groups_text = (
        ', '.join(sorted(pg.name for pg in server.allowed_promo_groups))
        if server.allowed_promo_groups
        else texts.t('ADMIN_SERVER_UI_008', 'Не выбраны')
    )

    trial_status = (
        texts.t('ADMIN_SERVER_UI_009', 'Да') if server.is_trial_eligible else texts.t('ADMIN_SERVER_UI_010', 'Нет')
    )
    original_name = (
        html.escape(server.original_name) if server.original_name else texts.t('ADMIN_SERVER_UI_026', 'Не указано')
    )
    description = (
        html.escape(server.description) if server.description else texts.t('ADMIN_SERVER_UI_026', 'Не указано')
    )

    info_block = '\n'.join(
        [
            f'{admin_icon("id")} <b>Информация</b>',
            f'• ID: <code>{server.id}</code>',
            f'• UUID: <code>{server.squad_uuid}</code>',
            f'• Название: {html.escape(server.display_name)}',
            f'• Оригинальное: {original_name}',
            f'• Статус: {status_icon} {status_text}',
        ]
    )

    settings_block = '\n'.join(
        [
            f'{admin_icon("settings")} <b>Настройки</b>',
            f'• Цена: {admin_icon("pricing")} {price_text}',
            (f'• Код страны: {admin_icon("map")} {server.country_code or texts.t("ADMIN_SERVER_UI_024", "Не указан")}'),
            (
                f'• Лимит пользователей: {admin_icon("users")} '
                f'{server.max_users or texts.t("ADMIN_SERVER_UI_025", "Без лимита")}'
            ),
            f'• Текущих пользователей: {server.current_users}',
            f'• Промогруппы: {html.escape(promo_groups_text)}',
            f'• Выдача триала: {admin_icon("trials")} {trial_status}',
        ]
    )

    text = '\n\n'.join(
        [
            admin_heading('servers', 'Редактирование сервера'),
            admin_quote(info_block),
            admin_quote(settings_block),
            f'{admin_icon("document")} <b>Описание</b>\n{description}',
        ]
    )

    keyboard = [
        [
            admin_button(
                texts.t('ADMIN_SERVER_UI_060', 'Название'),
                callback_data=f'admin_server_edit_name_{server.id}',
                icon='edit',
            ),
            admin_button(
                texts.t('ADMIN_SERVER_UI_061', 'Цена'),
                callback_data=f'admin_server_edit_price_{server.id}',
                icon='pricing',
            ),
        ],
        [
            admin_button(
                texts.t('ADMIN_SERVER_UI_062', 'Страна'),
                callback_data=f'admin_server_edit_country_{server.id}',
                icon='map',
            ),
            admin_button(
                texts.t('ADMIN_SERVER_UI_063', 'Лимит'),
                callback_data=f'admin_server_edit_limit_{server.id}',
                icon='users',
            ),
        ],
        [
            admin_button(
                texts.t('ADMIN_SERVER_UI_064', 'Юзеры'),
                callback_data=f'admin_server_users_{server.id}',
                icon='users',
            ),
        ],
        [
            admin_button(
                texts.t('ADMIN_SERVER_UI_094', 'Выдавать в триал')
                if not server.is_trial_eligible
                else texts.t('ADMIN_SERVER_UI_095', 'Не выдавать в триал'),
                callback_data=f'admin_server_trial_{server.id}',
                icon='trials',
            ),
        ],
        [
            admin_button(
                texts.t('ADMIN_SERVER_UI_065', 'Промогруппы'),
                callback_data=f'admin_server_edit_promo_{server.id}',
                icon='users_group',
            ),
            admin_button(
                texts.t('ADMIN_SERVER_UI_066', 'Описание'),
                callback_data=f'admin_server_edit_desc_{server.id}',
                icon='edit',
            ),
        ],
        [
            admin_button(
                texts.t('ADMIN_SERVER_UI_096', 'Отключить')
                if server.is_available
                else texts.t('ADMIN_SERVER_UI_097', 'Включить'),
                callback_data=f'admin_server_toggle_{server.id}',
                icon='lock' if server.is_available else 'unlock',
            )
        ],
        [
            admin_button(
                'Удалить',
                callback_data=f'admin_server_delete_{server.id}',
                icon='delete',
            ),
            admin_back_button(texts, 'admin_servers_list'),
        ],
    ]

    return (
        admin_screen(_clean_legacy(text), icon='servers', compact=True),
        types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


def _build_server_promo_groups_keyboard(server_id: int, promo_groups, selected_ids, language: str = 'ru'):
    keyboard = []
    for group in promo_groups:
        marker = '✅' if group['id'] in selected_ids else '▫️'
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=f'{marker} {group["name"]}',
                    callback_data=f'admin_server_promo_toggle_{server_id}_{group["id"]}',
                )
            ]
        )

    keyboard.append(
        [
            admin_button(
                get_texts(language).t('ADMIN_SERVER_UI_069', 'Сохранить'),
                callback_data=f'admin_server_promo_save_{server_id}',
                icon='save',
            )
        ]
    )
    keyboard.append([admin_back_button(get_texts(language), f'admin_server_edit_{server_id}')])

    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


@admin_required
@error_handler
async def show_servers_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    stats = await get_server_statistics(db)

    texts = get_texts(db_user.language)

    overview = '\n'.join(
        [
            f'{admin_icon("servers")} <b>Статистика</b>',
            f'• Всего серверов: {stats["total_servers"]}',
            f'• Доступные: {admin_icon("success")} {stats["available_servers"]}',
            f'• Недоступные: {admin_icon("error")} {stats["unavailable_servers"]}',
            f'• С подключениями: {stats["servers_with_connections"]}',
        ]
    )
    revenue = '\n'.join(
        [
            f'{admin_icon("money")} <b>Выручка от серверов</b>',
            f'• Общая: {int(stats["total_revenue_rubles"])} ₽',
        ]
    )

    text = '\n\n'.join(
        [
            admin_heading('servers', 'Управление серверами'),
            admin_quote(overview),
            admin_quote(revenue),
        ]
    )

    keyboard = [
        [
            admin_button(
                texts.t('ADMIN_SERVER_UI_070', 'Список серверов'),
                callback_data='admin_servers_list',
                icon='servers',
            ),
            admin_button(
                texts.t('ADMIN_SERVER_UI_071', 'Синхронизация'),
                callback_data='admin_servers_sync',
                icon='sync',
            ),
        ],
        [
            admin_button(
                texts.t('ADMIN_SERVER_UI_072', 'Синхронизировать счетчики'),
                callback_data='admin_servers_sync_counts',
                icon='refresh',
            ),
            admin_button(
                texts.t('ADMIN_SERVER_UI_073', 'Подробная статистика'),
                callback_data='admin_servers_stats',
                icon='chart',
            ),
        ],
        [admin_back_button(texts, 'admin_panel')],
    ]

    await callback.message.edit_text(
        admin_screen(text, icon='servers', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_servers_list(callback: types.CallbackQuery, db_user: User, db: AsyncSession, page: int = 1):
    texts = get_texts(db_user.language)
    servers, total_count = await get_all_server_squads(db, page=page, limit=10)
    total_pages = (total_count + 9) // 10

    if not servers:
        text = '\n\n'.join(
            [
                admin_heading('servers', 'Список серверов'),
                admin_quote(f'{admin_icon("warning")} Серверы не найдены.'),
            ]
        )
    else:
        text = admin_heading('servers', 'Список серверов')
        text += '\n\n' + admin_quote(
            '\n'.join(
                [
                    f'• Всего: {total_count}',
                    f'• Страница: {page}/{total_pages}',
                ]
            )
        )
        text += '\n\n'

        for i, server in enumerate(servers, 1 + (page - 1) * 10):
            price_text = (
                f'{int(server.price_rubles)} ₽'
                if server.price_kopeks > 0
                else texts.t('ADMIN_SERVER_UI_007', 'Бесплатно')
            )

            users_text = f' | {server.current_users}/{server.max_users}' if server.max_users else ''
            text += f'{admin_icon("servers")} <b>{i}. {html.escape(server.display_name)}</b>\n'
            text += f'{admin_icon("pricing")} {price_text}{users_text}\n'
            text += f'{admin_icon("key")} UUID: <code>{server.squad_uuid}</code>\n\n'

    keyboard = []

    for i, server in enumerate(servers):
        row_num = i // 2
        if len(keyboard) <= row_num:
            keyboard.append([])

        label = server.display_name if len(server.display_name) <= 15 else f'{server.display_name[:15]}…'
        keyboard[row_num].append(
            admin_button(
                label,
                callback_data=f'admin_server_edit_{server.id}',
                icon='edit',
            )
        )

    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(admin_button('←', callback_data=f'admin_servers_list_page_{page - 1}'))

        nav_row.append(admin_button(f'{page}/{total_pages}', callback_data='current_page'))

        if page < total_pages:
            nav_row.append(admin_button('→', callback_data=f'admin_servers_list_page_{page + 1}'))

        keyboard.append(nav_row)

    keyboard.append([admin_back_button(texts, 'admin_servers')])

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='servers', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def sync_servers_with_remnawave(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        admin_screen(
            texts.t('ADMIN_SERVER_UI_031', 'Синхронизация с Remnawave...\n\nПодождите, это может занять время.'),
            icon='sync',
            compact=True,
        ),
        reply_markup=None,
        parse_mode='HTML',
    )

    try:
        remnawave_service = RemnaWaveService()
        squads = await remnawave_service.get_all_squads()

        if not squads:
            await callback.message.edit_text(
                admin_screen(
                    texts.t(
                        'ADMIN_SERVER_UI_074',
                        'Не удалось получить данные о сквадах из Remnawave.\n\nПроверьте настройки API.',
                    ),
                    icon='warning',
                    compact=True,
                ),
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[admin_back_button(texts, 'admin_servers')]]),
                parse_mode='HTML',
            )
            return

        created, updated, removed = await sync_with_remnawave(db, squads)

        await cache.delete_pattern('available_countries*')

        results_block = '\n'.join(
            [
                f'{admin_icon("stats")} <b>Результаты</b>',
                f'• Создано новых серверов: {created}',
                f'• Обновлено существующих: {updated}',
                f'• Удалено отсутствующих: {removed}',
                f'• Всего обработано: {len(squads)}',
            ]
        )
        text = '\n\n'.join(
            [
                admin_heading('success', 'Синхронизация завершена'),
                admin_quote(results_block),
                (f'{admin_icon("info")} Новые серверы созданы как недоступные.\nНастройте их в списке серверов.'),
            ]
        )

        keyboard = [
            [
                admin_button(
                    texts.t('ADMIN_SERVER_UI_070', 'Список серверов'),
                    callback_data='admin_servers_list',
                    icon='servers',
                ),
                admin_button(
                    texts.t('ADMIN_SERVER_UI_098', 'Повторить'),
                    callback_data='admin_servers_sync',
                    icon='refresh',
                ),
            ],
            [admin_back_button(texts, 'admin_servers')],
        ]

        await callback.message.edit_text(
            admin_screen(_clean_legacy(text), icon='success', compact=True),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode='HTML',
        )

    except Exception as e:
        logger.error('Ошибка синхронизации серверов', error=e)
        await callback.message.edit_text(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_075', 'Ошибка синхронизации: {v0!s}').format(v0=e),
                icon='error',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[admin_back_button(texts, 'admin_servers')]]),
            parse_mode='HTML',
        )

    await callback.answer()


@admin_required
@error_handler
async def show_server_edit_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True
        )
        return

    text, keyboard = _build_server_edit_view(server, language=db_user.language)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()


@admin_required
@error_handler
async def show_server_users(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    payload = callback.data.split('admin_server_users_', 1)[-1]
    payload_parts = payload.split('_')

    server_id = int(payload_parts[0])
    page = int(payload_parts[1]) if len(payload_parts) > 1 else 1
    page = max(page, 1)
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True
        )
        return

    users = await get_server_connected_users(db, server_id)
    total_users = len(users)

    page_size = 10
    total_pages = max((total_users + page_size - 1) // page_size, 1)

    page = min(page, total_pages)

    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    page_users = users[start_index:end_index]

    texts = get_texts(db_user.language)
    safe_name = html.escape(server.display_name or '—')
    safe_uuid = html.escape(server.squad_uuid or '—')

    header_meta = [
        f'{admin_icon("servers")} <b>Сервер:</b> {safe_name}',
        f'{admin_icon("key")} <b>UUID:</b> <code>{safe_uuid}</code>',
        f'{admin_icon("users")} <b>Подключений:</b> {total_users}',
    ]

    if total_pages > 1:
        header_meta.append(f'{admin_icon("document")} <b>Страница:</b> {page}/{total_pages}')

    text = admin_heading('users', 'Пользователи сервера')
    text += '\n\n' + admin_quote('\n'.join(header_meta))
    text += '\n\n'

    def _get_status_icon(status_text: str) -> str:
        if not status_text:
            return ''

        parts = status_text.split(' ', 1)
        return parts[0] if parts else status_text

    if users:
        lines = []
        for index, user in enumerate(page_users, start=start_index + 1):
            safe_user_name = html.escape(user.full_name)
            if user.telegram_id:
                user_link = f'<a href="tg://user?id={user.telegram_id}">{safe_user_name}</a>'
            else:
                user_link = f'<b>{safe_user_name}</b>'
            lines.append(f'{admin_icon("user")} {index}. {user_link}')

        text += '\n'.join(lines)
    else:
        text += f'{admin_icon("warning")} {texts.t("ADMIN_SERVER_UI_018", "Пользователи не найдены.")}'

    keyboard: list[list[types.InlineKeyboardButton]] = []

    for user in page_users:
        display_name = user.full_name
        if len(display_name) > 30:
            display_name = display_name[:27] + '…'

        if settings.is_multi_tariff_enabled() and hasattr(user, 'subscriptions') and user.subscriptions:
            status_parts = []
            for sub in user.subscriptions:
                marker = '✅' if sub.is_active else '▫️'
                name = sub.tariff.name if sub.tariff else f'#{sub.id}'
                status_parts.append(f'{marker}{name}')
            subscription_status = ', '.join(status_parts)
        elif user.subscription:
            subscription_status = user.subscription.status_display
        else:
            subscription_status = texts.t('ADMIN_SERVER_UI_045', 'Нет подписки')
        status_icon = _get_status_icon(subscription_status)

        if status_icon:
            button_text = f'{status_icon} {display_name}'
        else:
            button_text = display_name

        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=button_text,
                    callback_data=f'admin_user_manage_{user.id}',
                )
            ]
        )

    if total_pages > 1:
        navigation_buttons: list[types.InlineKeyboardButton] = []

        if page > 1:
            navigation_buttons.append(
                admin_button(
                    '←',
                    callback_data=f'admin_server_users_{server_id}_{page - 1}',
                )
            )

        navigation_buttons.append(
            admin_button(
                texts.t('ADMIN_SERVER_UI_076', 'Стр. {v0}/{v1}').format(v0=page, v1=total_pages),
                callback_data=f'admin_server_users_{server_id}_{page}',
            )
        )

        if page < total_pages:
            navigation_buttons.append(
                admin_button(
                    '→',
                    callback_data=f'admin_server_users_{server_id}_{page + 1}',
                )
            )

        keyboard.append(navigation_buttons)

    keyboard.append([admin_back_button(texts, f'admin_server_edit_{server_id}', label='К серверу')])

    keyboard.append([admin_back_button(texts, 'admin_servers_list', label='К списку')])

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='users', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )

    await callback.answer()


@admin_required
@error_handler
async def toggle_server_availability(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True
        )
        return

    new_status = not server.is_available
    await update_server_squad(db, server_id, is_available=new_status)

    await cache.delete_pattern('available_countries*')

    status_text = (
        get_texts(db_user.language).t('ADMIN_SERVER_UI_019', 'включен')
        if new_status
        else get_texts(db_user.language).t('ADMIN_SERVER_UI_020', 'отключен')
    )
    await callback.answer(get_texts(db_user.language).t('ADMIN_SERVER_UI_033', 'Сервер {v0}!').format(v0=status_text))

    server = await get_server_squad_by_id(db, server_id)

    text, keyboard = _build_server_edit_view(server, language=db_user.language)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')


@admin_required
@error_handler
async def toggle_server_trial_assignment(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True
        )
        return

    new_status = not server.is_trial_eligible
    await update_server_squad(db, server_id, is_trial_eligible=new_status)

    status_text = (
        get_texts(db_user.language).t('ADMIN_SERVER_UI_021', 'будет выдаваться')
        if new_status
        else get_texts(db_user.language).t('ADMIN_SERVER_UI_022', 'перестанет выдаваться')
    )
    await callback.answer(
        get_texts(db_user.language).t('ADMIN_SERVER_UI_034', 'Сквад {v0} в триал').format(v0=status_text)
    )

    server = await get_server_squad_by_id(db, server_id)

    text, keyboard = _build_server_edit_view(server, language=db_user.language)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')


@admin_required
@error_handler
async def start_server_edit_price(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    texts = get_texts(db_user.language)

    if not server:
        await callback.answer(texts.t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True)
        return

    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_price)

    current_price = (
        f'{int(server.price_rubles)} ₽' if server.price_kopeks > 0 else texts.t('ADMIN_SERVER_UI_007', 'Бесплатно')
    )

    await callback.message.edit_text(
        admin_screen(
            texts.t(
                'ADMIN_SERVER_UI_035',
                '<b>Редактирование цены</b>\n\nТекущая цена: <b>{v0}</b>\n\nОтправьте новую цену в рублях (например: 15.50) или 0 для бесплатного доступа:',
            ).format(v0=current_price),
            icon='pricing',
            compact=True,
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_back_button(
                        texts,
                        f'admin_server_edit_{server_id}',
                        label=texts.t('ADMIN_SERVER_UI_084', 'Отмена'),
                    )
                ]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_price_edit(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    data = await state.get_data()
    server_id = data.get('server_id')
    texts = get_texts(db_user.language)

    try:
        price_rubles = float(message.text.replace(',', '.'))

        if price_rubles < 0:
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_SERVER_UI_079', 'Цена не может быть отрицательной'),
                    icon='error',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        if price_rubles > 10000:
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_SERVER_UI_080', 'Слишком высокая цена (максимум 10,000 ₽)'),
                    icon='error',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        price_kopeks = int(price_rubles * 100)

        server = await update_server_squad(db, server_id, price_kopeks=price_kopeks)

        if server:
            await state.clear()

            await cache.delete_pattern('available_countries*')

            price_text = f'{int(price_rubles)} ₽' if price_kopeks > 0 else texts.t('ADMIN_SERVER_UI_007', 'Бесплатно')
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_SERVER_UI_081', 'Цена сервера изменена на: <b>{v0}</b>').format(v0=price_text),
                    icon='success',
                    compact=True,
                ),
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[admin_back_button(texts, f'admin_server_edit_{server_id}', label='К серверу')]]
                ),
                parse_mode='HTML',
            )
        else:
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_SERVER_UI_049', 'Ошибка при обновлении сервера'),
                    icon='error',
                    compact=True,
                ),
                parse_mode='HTML',
            )

    except ValueError:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_082', 'Неверный формат цены. Используйте числа (например: 15.50)'),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def start_server_edit_name(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    texts = get_texts(db_user.language)

    if not server:
        await callback.answer(texts.t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True)
        return

    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_name)

    await callback.message.edit_text(
        admin_screen(
            texts.t(
                'ADMIN_SERVER_UI_036',
                '<b>Редактирование названия</b>\n\nТекущее название: <b>{v0}</b>\n\nОтправьте новое название для сервера:',
            ).format(v0=html.escape(server.display_name)),
            icon='edit',
            compact=True,
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_back_button(
                        texts,
                        f'admin_server_edit_{server_id}',
                        label=texts.t('ADMIN_SERVER_UI_084', 'Отмена'),
                    )
                ]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_name_edit(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    data = await state.get_data()
    server_id = data.get('server_id')
    texts = get_texts(db_user.language)

    new_name = message.text.strip()

    if len(new_name) > 255:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_046', 'Название слишком длинное (максимум 255 символов)'),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    if len(new_name) < 3:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_047', 'Название слишком короткое (минимум 3 символа)'),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    server = await update_server_squad(db, server_id, display_name=new_name)

    if server:
        await state.clear()

        await cache.delete_pattern('available_countries*')

        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_048', 'Название сервера изменено на: <b>{v0}</b>').format(
                    v0=html.escape(new_name)
                ),
                icon='success',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            f'admin_server_edit_{server_id}',
                            label=texts.t('ADMIN_SERVER_UI_101', 'К серверу'),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )
    else:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_049', 'Ошибка при обновлении сервера'),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def delete_server_confirm(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    texts = get_texts(db_user.language)

    if not server:
        await callback.answer(texts.t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True)
        return

    text = '\n\n'.join(
        [
            admin_heading('warning', 'Удаление сервера'),
            admin_quote(
                f'{admin_icon("servers")} <b>Сервер:</b> {html.escape(server.display_name)}\n'
                f'{admin_icon("warning")} Сервер можно удалить только если к нему нет активных подключений.'
            ),
            f'{admin_icon("prohibit")} <b>Это действие нельзя отменить!</b>',
        ]
    )

    keyboard = [
        [
            admin_button(
                texts.t('ADMIN_SERVER_UI_083', 'Да, удалить'),
                callback_data=f'admin_server_delete_confirm_{server_id}',
                icon='delete',
            ),
            admin_back_button(
                texts,
                f'admin_server_edit_{server_id}',
                label=texts.t('ADMIN_SERVER_UI_084', 'Отмена'),
            ),
        ]
    ]

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='warning', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_server_execute(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    texts = get_texts(db_user.language)

    if not server:
        await callback.answer(texts.t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True)
        return

    success = await delete_server_squad(db, server_id)

    if success:
        await cache.delete_pattern('available_countries*')

        await callback.message.edit_text(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_050', 'Сервер <b>{v0}</b> успешно удален!').format(
                    v0=html.escape(server.display_name)
                ),
                icon='success',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            'admin_servers_list',
                            label=texts.t('ADMIN_SERVER_UI_102', 'К списку серверов'),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )
    else:
        await callback.message.edit_text(
            admin_screen(
                texts.t(
                    'ADMIN_SERVER_UI_051',
                    'Не удалось удалить сервер <b>{v0}</b>\n\nВозможно, к нему есть активные подключения.',
                ).format(v0=html.escape(server.display_name)),
                icon='error',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            f'admin_server_edit_{server_id}',
                            label=texts.t('ADMIN_SERVER_UI_101', 'К серверу'),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )

    await callback.answer()


@admin_required
@error_handler
async def show_server_detailed_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    stats = await get_server_statistics(db)
    available_servers = await get_available_server_squads(db)

    texts = get_texts(db_user.language)

    overview = '\n'.join(
        [
            f'{admin_icon("servers")} <b>Общая информация</b>',
            f'• Всего серверов: {stats["total_servers"]}',
            f'• Доступные: {admin_icon("success")} {stats["available_servers"]}',
            f'• Недоступные: {admin_icon("error")} {stats["unavailable_servers"]}',
            f'• С активными подключениями: {stats["servers_with_connections"]}',
        ]
    )
    finance = '\n'.join(
        [
            f'{admin_icon("money")} <b>Финансовая статистика</b>',
            f'• Общая выручка: {int(stats["total_revenue_rubles"])} ₽',
            (
                f'• Средняя цена за сервер: '
                f'{int(stats["total_revenue_rubles"] / max(stats["servers_with_connections"], 1))} ₽'
            ),
        ]
    )

    top_block = f'{admin_icon("star")} <b>Топ серверов по цене</b>\n'
    sorted_servers = sorted(available_servers, key=lambda x: x.price_kopeks, reverse=True)

    for i, server in enumerate(sorted_servers[:5], 1):
        price_text = (
            f'{int(server.price_rubles)} ₽' if server.price_kopeks > 0 else texts.t('ADMIN_SERVER_UI_007', 'Бесплатно')
        )
        top_block += f'• {i}. {html.escape(server.display_name)} — {price_text}\n'

    if not sorted_servers:
        top_block += f'{admin_icon("warning")} {texts.t("ADMIN_SERVER_UI_023", "Нет доступных серверов")}'

    text = '\n\n'.join(
        [
            admin_heading('stats', 'Подробная статистика серверов'),
            admin_quote(overview),
            admin_quote(finance),
            top_block,
        ]
    )

    keyboard = [
        [
            admin_button(
                texts.t('ADMIN_SERVER_UI_085', 'Обновить'),
                callback_data='admin_servers_stats',
                icon='refresh',
            ),
            admin_button(
                texts.t('ADMIN_SERVER_UI_086', 'Список'),
                callback_data='admin_servers_list',
                icon='servers',
            ),
        ],
        [admin_back_button(texts, 'admin_servers')],
    ]

    await callback.message.edit_text(
        admin_screen(text, icon='stats', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def start_server_edit_country(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    texts = get_texts(db_user.language)

    if not server:
        await callback.answer(texts.t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True)
        return

    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_country)

    current_country = (
        html.escape(server.country_code) if server.country_code else texts.t('ADMIN_SERVER_UI_024', 'Не указан')
    )

    await callback.message.edit_text(
        admin_screen(
            texts.t(
                'ADMIN_SERVER_UI_037',
                "<b>Редактирование кода страны</b>\n\nТекущий код страны: <b>{v0}</b>\n\nОтправьте новый код страны (например: RU, US, DE) или '-' для удаления:",
            ).format(v0=current_country),
            icon='map',
            compact=True,
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_back_button(
                        texts,
                        f'admin_server_edit_{server_id}',
                        label=texts.t('ADMIN_SERVER_UI_084', 'Отмена'),
                    )
                ]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_country_edit(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    data = await state.get_data()
    server_id = data.get('server_id')
    texts = get_texts(db_user.language)

    new_country = message.text.strip().upper()

    if new_country == '-':
        new_country = None
    elif len(new_country) > 5:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_087', 'Код страны слишком длинный (максимум 5 символов)'),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    server = await update_server_squad(db, server_id, country_code=new_country)

    if server:
        await state.clear()

        await cache.delete_pattern('available_countries*')

        country_text = html.escape(new_country) if new_country else texts.t('ADMIN_SERVER_UI_038', 'Удален')
        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_052', 'Код страны изменен на: <b>{v0}</b>').format(v0=country_text),
                icon='success',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            f'admin_server_edit_{server_id}',
                            label=texts.t('ADMIN_SERVER_UI_101', 'К серверу'),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )
    else:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_049', 'Ошибка при обновлении сервера'),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def start_server_edit_limit(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    texts = get_texts(db_user.language)

    if not server:
        await callback.answer(texts.t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True)
        return

    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_limit)

    current_limit = server.max_users or texts.t('ADMIN_SERVER_UI_025', 'Без лимита')

    await callback.message.edit_text(
        admin_screen(
            texts.t(
                'ADMIN_SERVER_UI_039',
                '<b>Редактирование лимита пользователей</b>\n\nТекущий лимит: <b>{v0}</b>\n\nОтправьте новый лимит пользователей (число) или 0 для безлимитного доступа:',
            ).format(v0=current_limit),
            icon='users',
            compact=True,
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_back_button(
                        texts,
                        f'admin_server_edit_{server_id}',
                        label=texts.t('ADMIN_SERVER_UI_084', 'Отмена'),
                    )
                ]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_limit_edit(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    data = await state.get_data()
    server_id = data.get('server_id')
    texts = get_texts(db_user.language)

    try:
        limit = int(message.text.strip())

        if limit < 0:
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_SERVER_UI_088', 'Лимит не может быть отрицательным'),
                    icon='error',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        if limit > 10000:
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_SERVER_UI_089', 'Слишком большой лимит (максимум 10,000)'),
                    icon='error',
                    compact=True,
                ),
                parse_mode='HTML',
            )
            return

        max_users = limit if limit > 0 else None

        server = await update_server_squad(db, server_id, max_users=max_users)

        if server:
            await state.clear()

            limit_text = (
                texts.t('ADMIN_SERVER_UI_053', '{v0} пользователей').format(v0=limit)
                if limit > 0
                else texts.t('ADMIN_SERVER_UI_025', 'Без лимита')
            )
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_SERVER_UI_090', 'Лимит пользователей изменен на: <b>{v0}</b>').format(v0=limit_text),
                    icon='success',
                    compact=True,
                ),
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[admin_back_button(texts, f'admin_server_edit_{server_id}', label='К серверу')]]
                ),
                parse_mode='HTML',
            )
        else:
            await message.answer(
                admin_screen(
                    texts.t('ADMIN_SERVER_UI_049', 'Ошибка при обновлении сервера'),
                    icon='error',
                    compact=True,
                ),
                parse_mode='HTML',
            )

    except ValueError:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_091', 'Неверный формат числа. Введите целое число.'),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def start_server_edit_description(
    callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession
):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    texts = get_texts(db_user.language)

    if not server:
        await callback.answer(texts.t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True)
        return

    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_description)

    current_desc = (
        html.escape(server.description) if server.description else texts.t('ADMIN_SERVER_UI_026', 'Не указано')
    )

    await callback.message.edit_text(
        admin_screen(
            texts.t(
                'ADMIN_SERVER_UI_040',
                "<b>Редактирование описания</b>\n\nТекущее описание:\n<i>{v0}</i>\n\nОтправьте новое описание сервера или '-' для удаления:",
            ).format(v0=current_desc),
            icon='edit',
            compact=True,
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_back_button(
                        texts,
                        f'admin_server_edit_{server_id}',
                        label=texts.t('ADMIN_SERVER_UI_084', 'Отмена'),
                    )
                ]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_description_edit(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    data = await state.get_data()
    server_id = data.get('server_id')
    texts = get_texts(db_user.language)

    new_description = message.text.strip()

    if new_description == '-':
        new_description = None
    elif len(new_description) > 1000:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_092', 'Описание слишком длинное (максимум 1000 символов)'),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    server = await update_server_squad(db, server_id, description=new_description)

    if server:
        await state.clear()

        desc_text = html.escape(new_description) if new_description else texts.t('ADMIN_SERVER_UI_041', 'Удалено')
        await cache.delete_pattern('available_countries*')
        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_054', 'Описание сервера изменено:\n\n<i>{v0}</i>').format(v0=desc_text),
                icon='success',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            f'admin_server_edit_{server_id}',
                            label=texts.t('ADMIN_SERVER_UI_101', 'К серверу'),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )
    else:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_049', 'Ошибка при обновлении сервера'),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def start_server_edit_promo_groups(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    texts = get_texts(db_user.language)

    if not server:
        await callback.answer(texts.t('ADMIN_SERVER_UI_044', 'Сервер не найден!'), show_alert=True)
        return

    promo_groups_data = await get_promo_groups_with_counts(db)
    promo_groups = [
        {'id': group.id, 'name': group.name, 'is_default': group.is_default} for group, _ in promo_groups_data
    ]

    if not promo_groups:
        await callback.answer(texts.t('ADMIN_SERVER_UI_055', 'Не найдены промогруппы'), show_alert=True)
        return

    selected_ids = {pg.id for pg in server.allowed_promo_groups}
    if not selected_ids:
        default_group = next((pg for pg in promo_groups if pg['is_default']), None)
        if default_group:
            selected_ids.add(default_group['id'])

    await state.set_state(AdminStates.editing_server_promo_groups)
    await state.set_data(
        {
            'server_id': server_id,
            'promo_groups': promo_groups,
            'selected_promo_groups': list(selected_ids),
            'server_name': server.display_name,
        }
    )

    text = texts.t(
        'ADMIN_SERVER_UI_004',
        '<b>Настройка промогрупп</b>\n\nСервер: <b>{v0}</b>\n\nВыберите промогруппы, которым будет доступен этот сервер.\nДолжна быть выбрана минимум одна промогруппа.',
    ).format(v0=html.escape(server.display_name))

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='users_group', compact=True),
        reply_markup=_build_server_promo_groups_keyboard(
            server_id, promo_groups, selected_ids, language=db_user.language
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_server_promo_group(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split('_')
    server_id = int(parts[4])
    group_id = int(parts[5])

    data = await state.get_data()
    if not data or data.get('server_id') != server_id:
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_SERVER_UI_056', 'Сессия редактирования устарела'), show_alert=True
        )
        return

    selected = {int(pg_id) for pg_id in data.get('selected_promo_groups', [])}
    promo_groups = data.get('promo_groups', [])

    if group_id in selected:
        if len(selected) == 1:
            await callback.answer(
                get_texts(db_user.language).t('ADMIN_SERVER_UI_093', 'Нельзя отключить последнюю промогруппу'),
                show_alert=True,
            )
            return
        selected.remove(group_id)
        message = get_texts(db_user.language).t('ADMIN_SERVER_UI_027', 'Промогруппа отключена')
    else:
        selected.add(group_id)
        message = get_texts(db_user.language).t('ADMIN_SERVER_UI_028', 'Промогруппа добавлена')

    await state.update_data(selected_promo_groups=list(selected))

    await callback.message.edit_reply_markup(
        reply_markup=_build_server_promo_groups_keyboard(server_id, promo_groups, selected, language=db_user.language)
    )
    await callback.answer(message)


@admin_required
@error_handler
async def save_server_promo_groups(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    data = await state.get_data()
    if not data:
        await callback.answer(texts.t('ADMIN_SERVER_UI_057', 'Нет данных для сохранения'), show_alert=True)
        return

    server_id = data.get('server_id')
    selected = data.get('selected_promo_groups', [])

    if not selected:
        await callback.answer(
            texts.t('ADMIN_SERVER_UI_058', 'Выберите хотя бы одну промогруппу'),
            show_alert=True,
        )
        return

    try:
        server = await update_server_squad_promo_groups(db, server_id, selected)
    except ValueError as exc:
        await callback.answer(f'{exc}', show_alert=True)
        return

    if not server:
        await callback.answer(texts.t('ADMIN_SERVER_UI_059', 'Сервер не найден'), show_alert=True)
        return

    await cache.delete_pattern('available_countries*')
    await state.clear()

    text, keyboard = _build_server_edit_view(server, language=db_user.language)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer(texts.t('ADMIN_SERVER_UI_042', 'Промогруппы обновлены!'))


@admin_required
@error_handler
async def sync_server_user_counts_handler(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        admin_screen(
            texts.t('ADMIN_SERVER_UI_043', 'Синхронизация счетчиков пользователей...'),
            icon='sync',
            compact=True,
        ),
        reply_markup=None,
        parse_mode='HTML',
    )

    try:
        from app.database.crud.server_squad import sync_server_user_counts

        updated_count = await sync_server_user_counts(db)

        text = '\n\n'.join(
            [
                admin_heading('success', 'Синхронизация завершена'),
                admin_quote(f'{admin_icon("stats")} <b>Обновлено серверов:</b> {updated_count}'),
                f'{admin_icon("info")} Счетчики пользователей синхронизированы с реальными данными.',
            ]
        )

        keyboard = [
            [
                admin_button(
                    texts.t('ADMIN_SERVER_UI_070', 'Список серверов'),
                    callback_data='admin_servers_list',
                    icon='servers',
                ),
                admin_button(
                    texts.t('ADMIN_SERVER_UI_098', 'Повторить'),
                    callback_data='admin_servers_sync_counts',
                    icon='refresh',
                ),
            ],
            [admin_back_button(texts, 'admin_servers')],
        ]

        await callback.message.edit_text(
            admin_screen(_clean_legacy(text), icon='success', compact=True),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode='HTML',
        )

    except Exception as e:
        logger.error('Ошибка синхронизации счетчиков', error=e)
        await callback.message.edit_text(
            admin_screen(
                texts.t('ADMIN_SERVER_UI_075', 'Ошибка синхронизации: {v0!s}').format(v0=e),
                icon='error',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[admin_back_button(texts, 'admin_servers')]]),
            parse_mode='HTML',
        )

    await callback.answer()


@admin_required
@error_handler
async def handle_servers_pagination(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    page = int(callback.data.split('_')[-1])
    await show_servers_list(callback, db_user, db, page)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_servers_menu, F.data == 'admin_servers')
    dp.callback_query.register(show_servers_list, F.data == 'admin_servers_list')
    dp.callback_query.register(sync_servers_with_remnawave, F.data == 'admin_servers_sync')
    dp.callback_query.register(sync_server_user_counts_handler, F.data == 'admin_servers_sync_counts')
    dp.callback_query.register(show_server_detailed_stats, F.data == 'admin_servers_stats')

    dp.callback_query.register(
        show_server_edit_menu,
        F.data.startswith('admin_server_edit_')
        & ~F.data.contains('name')
        & ~F.data.contains('price')
        & ~F.data.contains('country')
        & ~F.data.contains('limit')
        & ~F.data.contains('desc')
        & ~F.data.contains('promo'),
    )
    dp.callback_query.register(toggle_server_availability, F.data.startswith('admin_server_toggle_'))
    dp.callback_query.register(toggle_server_trial_assignment, F.data.startswith('admin_server_trial_'))
    dp.callback_query.register(show_server_users, F.data.startswith('admin_server_users_'))

    dp.callback_query.register(start_server_edit_name, F.data.startswith('admin_server_edit_name_'))
    dp.callback_query.register(start_server_edit_price, F.data.startswith('admin_server_edit_price_'))
    dp.callback_query.register(start_server_edit_country, F.data.startswith('admin_server_edit_country_'))
    dp.callback_query.register(start_server_edit_promo_groups, F.data.startswith('admin_server_edit_promo_'))
    dp.callback_query.register(start_server_edit_limit, F.data.startswith('admin_server_edit_limit_'))
    dp.callback_query.register(start_server_edit_description, F.data.startswith('admin_server_edit_desc_'))

    dp.message.register(process_server_name_edit, AdminStates.editing_server_name)
    dp.message.register(process_server_price_edit, AdminStates.editing_server_price)
    dp.message.register(process_server_country_edit, AdminStates.editing_server_country)
    dp.message.register(process_server_limit_edit, AdminStates.editing_server_limit)
    dp.message.register(process_server_description_edit, AdminStates.editing_server_description)
    dp.callback_query.register(toggle_server_promo_group, F.data.startswith('admin_server_promo_toggle_'))
    dp.callback_query.register(save_server_promo_groups, F.data.startswith('admin_server_promo_save_'))

    dp.callback_query.register(
        delete_server_confirm, F.data.startswith('admin_server_delete_') & ~F.data.contains('confirm')
    )
    dp.callback_query.register(delete_server_execute, F.data.startswith('admin_server_delete_confirm_'))

    dp.callback_query.register(handle_servers_pagination, F.data.startswith('admin_servers_list_page_'))
