import html
import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.server_squad import (
    count_active_users_for_squad,
    get_all_server_squads,
    get_server_squad_by_uuid,
)
from app.database.models import User
from app.external.remnawave_api import (
    INTERNAL_SQUAD_NAME_MAX_LENGTH,
    INTERNAL_SQUAD_NAME_MIN_LENGTH,
    is_valid_internal_squad_name,
)
from app.keyboards.admin import (
    get_admin_remnawave_keyboard,
    get_node_management_keyboard,
    get_squad_edit_keyboard,
    get_squad_management_keyboard,
)
from app.localization.texts import get_texts
from app.services.remnawave_service import RemnaWaveConfigurationError, RemnaWaveService
from app.services.remnawave_sync_service import (
    FullSyncAlreadyRunning,
    RemnaWaveAutoSyncStatus,
    is_full_sync_running,
    perform_full_sync,
    remnawave_sync_service,
)
from app.services.system_settings_service import bot_configuration_service
from app.states import (
    RemnaWaveSyncStates,
    SquadCreateStates,
    SquadMigrationStates,
    SquadRenameStates,
)
from app.utils.admin_ui import (
    admin_back_button as _admin_back_button,
    admin_button,
    admin_heading,
    admin_icon,
    admin_quote,
    admin_screen,
)
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_bytes, format_datetime


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


def _quote_after_title(value: str) -> str:
    """Оставляет заголовок вне цитаты, а параметры после него — в blockquote."""
    title, separator, body = value.partition('\n\n')
    if not separator or not body.strip():
        return value
    return f'{title}\n\n{admin_quote(body)}'


squad_inbound_selections = {}
squad_create_data = {}

MIGRATION_PAGE_SIZE = 8


def _localize_panel_value(texts, value: Any) -> Any:
    """Localize the service's closed display vocabulary, preserving unknown API data."""
    if not isinstance(value, str):
        return value
    known = {
        'ACTIVE': ('ACTIVE', 'Активен'),
        'DISABLED': ('DISABLED', 'Отключён'),
        'LIMITED': ('LIMITED', 'Лимит исчерпан'),
        'EXPIRED': ('EXPIRED', 'Истёк'),
        'high': ('HIGH', 'Высокий'),
        'medium': ('MEDIUM', 'Средний'),
        'low': ('LOW', 'Низкий'),
        'Подключение успешно': ('CONNECTED', 'Подключение успешно'),
        'RemnaWave API не настроен': ('NOT_CONFIGURED', 'RemnaWave API не настроен'),
        'REMNAWAVE_API_URL не настроен': ('URL_NOT_CONFIGURED', 'REMNAWAVE_API_URL не настроен'),
        'REMNAWAVE_API_KEY не настроен': ('KEY_NOT_CONFIGURED', 'REMNAWAVE_API_KEY не настроен'),
        '1-2 минуты': ('TIME_SHORT', '1-2 минуты'),
        '3-5 минут': ('TIME_LONG', '3-5 минут'),
        'Рекомендуется регулярная синхронизация данных': (
            'REGULAR_SYNC',
            'Рекомендуется регулярная синхронизация данных',
        ),
        'Ошибка анализа - рекомендуется полная синхронизация': (
            'ANALYSIS_ERROR',
            'Ошибка анализа - рекомендуется полная синхронизация',
        ),
    }
    if value in known:
        key, source = known[value]
        return texts.t(f'ADMIN_PANEL_VALUE_{key}', source)
    for prefix, key, source in (
        ('Ошибка API: ', 'API_ERROR', 'Ошибка API: {detail}'),
        ('Ошибка подключения: ', 'CONNECTION_ERROR', 'Ошибка подключения: {detail}'),
    ):
        if value.startswith(prefix):
            return texts.t(f'ADMIN_PANEL_VALUE_{key}', source).format(detail=value[len(prefix) :])
    patterns = (
        (
            r'Найдено ([0-9]+) пользователей без связи с Remnawave',
            'NO_PANEL_LINK',
            'Найдено {count} пользователей без связи с Remnawave',
        ),
        (
            r'Найдено ([0-9]+) активных подписок с истекшим сроком',
            'ACTIVE_EXPIRED',
            'Найдено {count} активных подписок с истекшим сроком',
        ),
    )
    for pattern, key, source in patterns:
        match = re.fullmatch(pattern, value)
        if match:
            return texts.t(f'ADMIN_PANEL_VALUE_{key}', source).format(count=match[1])
    return value


def _format_duration(seconds: float, language: str = 'ru') -> str:
    texts = get_texts(language)
    if seconds < 1:
        return texts.t('ADMIN_REMNAWAVE_UI_FORMAT_DURATION_000', 'менее 1с')

    minutes, sec = divmod(int(seconds), 60)
    if minutes:
        if sec:
            return texts.t('ADMIN_REMNAWAVE_UI_FORMAT_DURATION_001', '{value1} мин {value2} с').format(
                value1=minutes, value2=sec
            )
        return texts.t('ADMIN_REMNAWAVE_UI_FORMAT_DURATION_002', '{value1} мин').format(value1=minutes)
    return texts.t('ADMIN_REMNAWAVE_UI_FORMAT_DURATION_003', '{value1} с').format(value1=sec)


def _format_user_stats(stats: dict[str, Any] | None, language: str = 'ru') -> str:
    texts = get_texts(language)
    if not stats:
        return '—'

    created = stats.get('created', 0)
    updated = stats.get('updated', 0)
    deleted = stats.get('deleted', stats.get('deactivated', 0))
    errors = stats.get('errors', 0)

    return texts.t(
        'ADMIN_REMNAWAVE_UI_FORMAT_USER_STATS_004',
        '• Создано: {value1}\n• Обновлено: {value2}\n• Деактивировано: {value3}\n• Ошибок: {value4}',
    ).format(value1=created, value2=updated, value3=deleted, value4=errors)


def _format_server_stats(stats: dict[str, Any] | None, language: str = 'ru') -> str:
    texts = get_texts(language)
    if not stats:
        return '—'

    created = stats.get('created', 0)
    updated = stats.get('updated', 0)
    removed = stats.get('removed', 0)
    total = stats.get('total', 0)

    return texts.t(
        'ADMIN_REMNAWAVE_UI_FORMAT_SERVER_STATS_005',
        '• Создано: {value1}\n• Обновлено: {value2}\n• Удалено: {value3}\n• Всего в панели: {value4}',
    ).format(value1=created, value2=updated, value3=removed, value4=total)


def _build_auto_sync_view(
    status: RemnaWaveAutoSyncStatus, language: str = 'ru'
) -> tuple[str, types.InlineKeyboardMarkup]:
    texts = get_texts(language)
    times_text = ', '.join(t.strftime('%H:%M') for t in status.times) if status.times else '—'
    next_run_text = format_datetime(status.next_run) if status.next_run else '—'

    if status.last_run_finished_at:
        finished_text = format_datetime(status.last_run_finished_at)
        started_text = format_datetime(status.last_run_started_at) if status.last_run_started_at else '—'
        duration = status.last_run_finished_at - status.last_run_started_at if status.last_run_started_at else None
        duration_text = f' ({_format_duration(duration.total_seconds(), language=language)})' if duration else ''
        reason_map = {
            'manual': texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_006', 'вручную'),
            'auto': texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_007', 'по расписанию'),
            'immediate': texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_008', 'при включении'),
        }
        reason_text = reason_map.get(status.last_run_reason or '', '—')
        result_icon = admin_icon('success') if status.last_run_success else admin_icon('error')
        result_label = (
            texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_009', 'успешно')
            if status.last_run_success
            else texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_010', 'с ошибками')
        )
        error_block = (
            texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_011', '\nОшибка: {value1}').format(
                value1=html.escape(str(status.last_run_error))
            )
            if status.last_run_error
            else ''
        )
        last_run_text = texts.t(
            'ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_012',
            '{value1} {value2}\n• Старт: {value3}\n• Завершено: {value4}{value5}\n• Причина запуска: {value6}{value7}',
        ).format(
            value1=result_icon,
            value2=result_label,
            value3=started_text,
            value4=finished_text,
            value5=duration_text,
            value6=reason_text,
            value7=error_block,
        )
    elif status.last_run_started_at:
        last_run_text = (
            texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_013', 'Синхронизация началась, но еще не завершилась')
            if status.is_running
            else texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_014', 'ℹ Последний запуск: {value1}').format(
                value1=format_datetime(status.last_run_started_at)
            )
        )
    else:
        last_run_text = '—'

    running_text = (
        texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_015', 'Выполняется сейчас')
        if status.is_running
        else texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_016', 'Ожидание')
    )
    toggle_text = (
        texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_017', 'Отключить')
        if status.enabled
        else texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_018', 'Включить')
    )

    status_block = '\n'.join(
        [
            (
                f'{admin_icon("settings")} <b>Статус:</b> '
                f'{texts.t("ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_020", "Включена") if status.enabled else texts.t("ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_021", "Отключена")}'
            ),
            f'{admin_icon("calendar")} <b>Расписание:</b> {times_text}',
            f'{admin_icon("clock")} <b>Следующий запуск:</b> {next_run_text if status.enabled else "—"}',
            f'{admin_icon("sync")} <b>Состояние:</b> {running_text}',
        ]
    )

    text = '\n\n'.join(
        [
            admin_heading('sync', 'Автосинхронизация RemnaWave'),
            admin_quote(status_block),
            f'{admin_icon("refresh")} <b>Последний запуск</b>\n{last_run_text}',
            admin_quote(
                f'{admin_icon("users")} <b>Пользователи</b>\n'
                f'{_format_user_stats(status.last_user_stats, language=language)}'
            ),
            admin_quote(
                f'{admin_icon("servers")} <b>Серверы</b>\n'
                f'{_format_server_stats(status.last_server_stats, language=language)}'
            ),
        ]
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    _clean_legacy(texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_022', 'Запустить сейчас')),
                    callback_data='remnawave_auto_sync_run',
                    icon='refresh',
                )
            ],
            [
                admin_button(
                    toggle_text,
                    callback_data='remnawave_auto_sync_toggle',
                    icon='lock' if status.enabled else 'unlock',
                )
            ],
            [
                admin_button(
                    texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_023', 'Изменить расписание'),
                    callback_data='remnawave_auto_sync_times',
                    icon='clock',
                )
            ],
            [admin_back_button(texts, 'admin_rw_sync')],
        ]
    )

    return admin_screen(_clean_legacy(text), icon='sync', compact=True), keyboard


def _format_migration_server_label(texts, server) -> str:
    status = (
        texts.t('ADMIN_SQUAD_MIGRATION_STATUS_AVAILABLE', 'Доступен')
        if getattr(server, 'is_available', True)
        else texts.t('ADMIN_SQUAD_MIGRATION_STATUS_UNAVAILABLE', 'Недоступен')
    )
    return texts.t(
        'ADMIN_SQUAD_MIGRATION_SERVER_LABEL',
        '{name} — {users} ({status})',
    ).format(name=html.escape(server.display_name), users=server.current_users, status=status)


def _build_migration_keyboard(
    texts,
    squads,
    page: int,
    total_pages: int,
    stage: str,
    *,
    exclude_uuid: str = None,
):
    prefix = 'admin_migration_source' if stage == 'source' else 'admin_migration_target'
    rows = []
    has_items = False

    button_template = texts.t(
        'ADMIN_SQUAD_MIGRATION_SQUAD_BUTTON',
        '{name} — {users} ({status})',
    )

    for squad in squads:
        if exclude_uuid and squad.squad_uuid == exclude_uuid:
            continue

        has_items = True
        status = (
            texts.t('ADMIN_SQUAD_MIGRATION_STATUS_AVAILABLE_SHORT', '')
            if getattr(squad, 'is_available', True)
            else texts.t('ADMIN_SQUAD_MIGRATION_STATUS_UNAVAILABLE_SHORT', '')
        )
        label = f'{squad.display_name} — {squad.current_users}'
        if status:
            label = button_template.format(
                name=squad.display_name,
                users=squad.current_users,
                status=status,
            )

        rows.append(
            [
                types.InlineKeyboardButton(
                    text=label,
                    callback_data=f'{prefix}_{squad.squad_uuid}',
                )
            ]
        )

    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                admin_button(
                    '←',
                    callback_data=f'{prefix}_page_{page - 1}',
                )
            )
        nav_buttons.append(
            admin_button(
                texts.t(
                    'ADMIN_SQUAD_MIGRATION_PAGE',
                    'Стр. {page}/{pages}',
                ).format(page=page, pages=total_pages),
                callback_data='admin_migration_page_info',
            )
        )
        if page < total_pages:
            nav_buttons.append(
                admin_button(
                    '→',
                    callback_data=f'{prefix}_page_{page + 1}',
                )
            )
        rows.append(nav_buttons)

    rows.append(
        [
            admin_back_button(texts, 'admin_migration_cancel', label=texts.CANCEL),
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows), has_items


async def _fetch_migration_page(
    db: AsyncSession,
    page: int,
):
    squads, total = await get_all_server_squads(
        db,
        page=max(1, page),
        limit=MIGRATION_PAGE_SIZE,
    )
    total_pages = max(1, math.ceil(total / MIGRATION_PAGE_SIZE))

    page = max(page, 1)
    if page > total_pages:
        page = total_pages
        squads, total = await get_all_server_squads(
            db,
            page=page,
            limit=MIGRATION_PAGE_SIZE,
        )
        total_pages = max(1, math.ceil(total / MIGRATION_PAGE_SIZE))

    return squads, page, total_pages


@admin_required
@error_handler
async def show_squad_migration_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)

    await state.clear()

    squads, page, total_pages = await _fetch_migration_page(db, page=1)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        'source',
    )

    message = admin_heading('sync', 'Переезд сквадов')
    message += '\n\n' + admin_quote(
        texts.t(
            'ADMIN_SQUAD_MIGRATION_SELECT_SOURCE',
            'Выберите сквад, из которого нужно переехать:',
        )
    )

    if not has_items:
        message += '\n\n' + (
            f'{admin_icon("warning")} '
            + texts.t(
                'ADMIN_SQUAD_MIGRATION_NO_OPTIONS',
                'Нет доступных сквадов. Добавьте новые или отмените операцию.',
            )
        )

    await state.set_state(SquadMigrationStates.selecting_source)

    await callback.message.edit_text(
        admin_screen(_clean_legacy(message), icon='sync', compact=True),
        reply_markup=keyboard,
        disable_web_page_preview=True,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def paginate_migration_source(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    if await state.get_state() != SquadMigrationStates.selecting_source:
        await callback.answer()
        return

    try:
        page = int(callback.data.split('_page_')[-1])
    except (ValueError, IndexError):
        await callback.answer()
        return

    squads, page, total_pages = await _fetch_migration_page(db, page=page)
    texts = get_texts(db_user.language)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        'source',
    )

    message = admin_heading('sync', 'Переезд сквадов')
    message += '\n\n' + admin_quote(
        texts.t(
            'ADMIN_SQUAD_MIGRATION_SELECT_SOURCE',
            'Выберите сквад, из которого нужно переехать:',
        )
    )

    if not has_items:
        message += '\n\n' + (
            f'{admin_icon("warning")} '
            + texts.t(
                'ADMIN_SQUAD_MIGRATION_NO_OPTIONS',
                'Нет доступных сквадов. Добавьте новые или отмените операцию.',
            )
        )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(message), icon='sync', compact=True),
        reply_markup=keyboard,
        disable_web_page_preview=True,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_migration_source_selection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    if await state.get_state() != SquadMigrationStates.selecting_source:
        await callback.answer()
        return

    if '_page_' in callback.data:
        await callback.answer()
        return

    source_uuid = callback.data.replace('admin_migration_source_', '', 1)

    texts = get_texts(db_user.language)
    server = await get_server_squad_by_uuid(db, source_uuid)

    if not server:
        await callback.answer(
            texts.t(
                'ADMIN_SQUAD_MIGRATION_SQUAD_NOT_FOUND',
                'Сквад не найден или недоступен.',
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        source_uuid=server.squad_uuid,
        source_display=_format_migration_server_label(texts, server),
    )

    squads, page, total_pages = await _fetch_migration_page(db, page=1)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        'target',
        exclude_uuid=server.squad_uuid,
    )

    message = admin_heading('sync', 'Переезд сквадов')
    message += '\n\n' + admin_quote(
        '\n'.join(
            [
                # Источник и цель — параметры переезда.
                texts.t(
                    'ADMIN_SQUAD_MIGRATION_SELECTED_SOURCE',
                    'Источник: {source}',
                ).format(source=_format_migration_server_label(texts, server)),
                f'{admin_icon("arrow_down")} '
                + texts.t(
                    'ADMIN_SQUAD_MIGRATION_SELECT_TARGET',
                    'Выберите сквад, в который нужно переехать:',
                ),
            ]
        )
    )

    if not has_items:
        message += '\n\n' + (
            f'{admin_icon("warning")} '
            + texts.t(
                'ADMIN_SQUAD_MIGRATION_TARGET_EMPTY',
                'Нет других сквадов для переезда. Отмените операцию или создайте новые сквады.',
            )
        )

    await state.set_state(SquadMigrationStates.selecting_target)

    await callback.message.edit_text(
        admin_screen(_clean_legacy(message), icon='sync', compact=True),
        reply_markup=keyboard,
        disable_web_page_preview=True,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def paginate_migration_target(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    if await state.get_state() != SquadMigrationStates.selecting_target:
        await callback.answer()
        return

    try:
        page = int(callback.data.split('_page_')[-1])
    except (ValueError, IndexError):
        await callback.answer()
        return

    data = await state.get_data()
    source_uuid = data.get('source_uuid')
    if not source_uuid:
        await callback.answer()
        return

    texts = get_texts(db_user.language)

    squads, page, total_pages = await _fetch_migration_page(db, page=page)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        'target',
        exclude_uuid=source_uuid,
    )

    source_display = data.get('source_display') or source_uuid

    message = admin_heading('sync', 'Переезд сквадов')
    message += '\n\n' + admin_quote(
        '\n'.join(
            [
                texts.t(
                    'ADMIN_SQUAD_MIGRATION_SELECTED_SOURCE',
                    'Источник: {source}',
                ).format(source=source_display),
                f'{admin_icon("arrow_down")} '
                + texts.t(
                    'ADMIN_SQUAD_MIGRATION_SELECT_TARGET',
                    'Выберите сквад, в который нужно переехать:',
                ),
            ]
        )
    )

    if not has_items:
        message += '\n\n' + (
            f'{admin_icon("warning")} '
            + texts.t(
                'ADMIN_SQUAD_MIGRATION_TARGET_EMPTY',
                'Нет других сквадов для переезда. Отмените операцию или создайте новые сквады.',
            )
        )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(message), icon='sync', compact=True),
        reply_markup=keyboard,
        disable_web_page_preview=True,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_migration_target_selection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    current_state = await state.get_state()
    if current_state != SquadMigrationStates.selecting_target:
        await callback.answer()
        return

    if '_page_' in callback.data:
        await callback.answer()
        return

    data = await state.get_data()
    source_uuid = data.get('source_uuid')

    if not source_uuid:
        await callback.answer()
        return

    target_uuid = callback.data.replace('admin_migration_target_', '', 1)

    texts = get_texts(db_user.language)

    if target_uuid == source_uuid:
        await callback.answer(
            texts.t(
                'ADMIN_SQUAD_MIGRATION_SAME_SQUAD',
                'Нельзя выбрать тот же сквад.',
            ),
            show_alert=True,
        )
        return

    target_server = await get_server_squad_by_uuid(db, target_uuid)
    if not target_server:
        await callback.answer(
            texts.t(
                'ADMIN_SQUAD_MIGRATION_SQUAD_NOT_FOUND',
                'Сквад не найден или недоступен.',
            ),
            show_alert=True,
        )
        return

    source_display = data.get('source_display') or source_uuid

    users_to_move = await count_active_users_for_squad(db, source_uuid)

    await state.update_data(
        target_uuid=target_server.squad_uuid,
        target_display=_format_migration_server_label(texts, target_server),
        migration_count=users_to_move,
    )

    await state.set_state(SquadMigrationStates.confirming)

    details_block = '\n'.join(
        [
            texts.t(
                'ADMIN_SQUAD_MIGRATION_CONFIRM_DETAILS',
                'Проверьте параметры переезда:',
            ),
            texts.t('ADMIN_SQUAD_MIGRATION_CONFIRM_SOURCE', '• Из: {source}').format(source=source_display),
            texts.t('ADMIN_SQUAD_MIGRATION_CONFIRM_TARGET', '• В: {target}').format(
                target=_format_migration_server_label(texts, target_server)
            ),
            texts.t('ADMIN_SQUAD_MIGRATION_CONFIRM_COUNT', '• Пользователей к переносу: {count}').format(
                count=users_to_move
            ),
        ]
    )
    message_lines = [
        admin_heading('sync', 'Переезд сквадов'),
        '',
        admin_quote(details_block),
        '',
        f'{admin_icon("question")} '
        + texts.t('ADMIN_SQUAD_MIGRATION_CONFIRM_PROMPT', 'Подтвердите выполнение операции.'),
    ]

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    texts.t(
                        'ADMIN_SQUAD_MIGRATION_CONFIRM_BUTTON',
                        'Подтвердить',
                    ),
                    callback_data='admin_migration_confirm',
                    icon='check',
                )
            ],
            [
                admin_button(
                    texts.t(
                        'ADMIN_SQUAD_MIGRATION_CHANGE_TARGET',
                        'Изменить сервер назначения',
                    ),
                    callback_data='admin_migration_change_target',
                    icon='edit',
                )
            ],
            [
                admin_back_button(texts, 'admin_migration_cancel', label=texts.CANCEL),
            ],
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy('\n'.join(message_lines)), icon='sync', compact=True),
        reply_markup=keyboard,
        disable_web_page_preview=True,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def change_migration_target(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    data = await state.get_data()
    source_uuid = data.get('source_uuid')

    if not source_uuid:
        await callback.answer()
        return

    await state.set_state(SquadMigrationStates.selecting_target)

    texts = get_texts(db_user.language)
    squads, page, total_pages = await _fetch_migration_page(db, page=1)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        'target',
        exclude_uuid=source_uuid,
    )

    source_display = data.get('source_display') or source_uuid

    message = admin_heading('sync', 'Переезд сквадов')
    message += '\n\n' + admin_quote(
        '\n'.join(
            [
                texts.t(
                    'ADMIN_SQUAD_MIGRATION_SELECTED_SOURCE',
                    'Источник: {source}',
                ).format(source=source_display),
                f'{admin_icon("arrow_down")} '
                + texts.t(
                    'ADMIN_SQUAD_MIGRATION_SELECT_TARGET',
                    'Выберите сквад, в который нужно переехать:',
                ),
            ]
        )
    )

    if not has_items:
        message += '\n\n' + (
            f'{admin_icon("warning")} '
            + texts.t(
                'ADMIN_SQUAD_MIGRATION_TARGET_EMPTY',
                'Нет других сквадов для переезда. Отмените операцию или создайте новые сквады.',
            )
        )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(message), icon='sync', compact=True),
        reply_markup=keyboard,
        disable_web_page_preview=True,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_squad_migration(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    current_state = await state.get_state()
    if current_state != SquadMigrationStates.confirming:
        await callback.answer()
        return

    data = await state.get_data()
    source_uuid = data.get('source_uuid')
    target_uuid = data.get('target_uuid')

    if not source_uuid or not target_uuid:
        await callback.answer()
        return

    texts = get_texts(db_user.language)
    remnawave_service = RemnaWaveService()

    await callback.answer(texts.t('ADMIN_SQUAD_MIGRATION_IN_PROGRESS', 'Запускаю переезд...'))

    try:
        result = await remnawave_service.migrate_squad_users(
            db,
            source_uuid=source_uuid,
            target_uuid=target_uuid,
        )
    except RemnaWaveConfigurationError as error:
        message = texts.t(
            'ADMIN_SQUAD_MIGRATION_API_ERROR',
            'RemnaWave API не настроен: {error}',
        ).format(error=html.escape(str(error)))
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_back_button(
                        texts,
                        'admin_remnawave',
                        label=texts.t(
                            'ADMIN_SQUAD_MIGRATION_BACK_BUTTON',
                            'В Remnawave',
                        ),
                    )
                ]
            ]
        )
        await callback.message.edit_text(
            admin_screen(_clean_legacy(message), icon='error', compact=True),
            reply_markup=reply_markup,
            parse_mode='HTML',
        )
        await state.clear()
        return

    source_display = data.get('source_display') or source_uuid
    target_display = data.get('target_display') or target_uuid

    if not result.get('success'):
        error_message = result.get('message') or ''
        error_code = result.get('error') or 'unexpected'
        message = texts.t(
            'ADMIN_SQUAD_MIGRATION_ERROR',
            'Не удалось выполнить переезд (код: {code}). {details}',
        ).format(code=html.escape(str(error_code)), details=html.escape(str(error_message)))
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_back_button(
                        texts,
                        'admin_remnawave',
                        label=texts.t(
                            'ADMIN_SQUAD_MIGRATION_BACK_BUTTON',
                            'В Remnawave',
                        ),
                    )
                ],
                [
                    admin_button(
                        texts.t(
                            'ADMIN_SQUAD_MIGRATION_NEW_BUTTON',
                            'Новый переезд',
                        ),
                        callback_data='admin_rw_migration',
                        icon='sync',
                    )
                ],
            ]
        )
        await callback.message.edit_text(
            admin_screen(_clean_legacy(message), icon='error', compact=True),
            reply_markup=reply_markup,
            parse_mode='HTML',
        )
        await state.clear()
        return

    message_lines = [
        texts.t('ADMIN_SQUAD_MIGRATION_SUCCESS_TITLE', 'Переезд завершен'),
        '',
        texts.t('ADMIN_SQUAD_MIGRATION_CONFIRM_SOURCE', '• Из: {source}').format(source=source_display),
        texts.t('ADMIN_SQUAD_MIGRATION_CONFIRM_TARGET', '• В: {target}').format(target=target_display),
        '',
        texts.t(
            'ADMIN_SQUAD_MIGRATION_RESULT_TOTAL',
            'Найдено подписок: {count}',
        ).format(count=result.get('total', 0)),
        texts.t(
            'ADMIN_SQUAD_MIGRATION_RESULT_UPDATED',
            'Перенесено: {count}',
        ).format(count=result.get('updated', 0)),
    ]

    panel_updated = result.get('panel_updated', 0)
    panel_failed = result.get('panel_failed', 0)

    if panel_updated:
        message_lines.append(
            texts.t(
                'ADMIN_SQUAD_MIGRATION_RESULT_PANEL_UPDATED',
                'Обновлено в панели: {count}',
            ).format(count=panel_updated)
        )
    if panel_failed:
        message_lines.append(
            texts.t(
                'ADMIN_SQUAD_MIGRATION_RESULT_PANEL_FAILED',
                'Не удалось обновить в панели: {count}',
            ).format(count=panel_failed)
        )

    reply_markup = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    texts.t(
                        'ADMIN_SQUAD_MIGRATION_NEW_BUTTON',
                        'Новый переезд',
                    ),
                    callback_data='admin_rw_migration',
                    icon='sync',
                )
            ],
            [
                admin_back_button(
                    texts,
                    'admin_remnawave',
                    label=texts.t(
                        'ADMIN_SQUAD_MIGRATION_BACK_BUTTON',
                        'В Remnawave',
                    ),
                )
            ],
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy('\n'.join(message_lines)), icon='success', compact=True),
        reply_markup=reply_markup,
        disable_web_page_preview=True,
        parse_mode='HTML',
    )
    await state.clear()


@admin_required
@error_handler
async def cancel_squad_migration(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await state.clear()

    message = texts.t(
        'ADMIN_SQUAD_MIGRATION_CANCELLED',
        'Переезд отменен.',
    )

    reply_markup = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_back_button(
                    texts,
                    'admin_remnawave',
                    label=texts.t(
                        'ADMIN_SQUAD_MIGRATION_BACK_BUTTON',
                        'В Remnawave',
                    ),
                )
            ]
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(message), icon='warning', compact=True),
        reply_markup=reply_markup,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_migration_page_info(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await callback.answer(
        texts.t('ADMIN_SQUAD_MIGRATION_PAGE_HINT', 'Это текущая страница.'),
        show_alert=False,
    )


@admin_required
@error_handler
async def show_remnawave_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    remnawave_service = RemnaWaveService()
    connection_test = await remnawave_service.test_api_connection()

    status = connection_test.get('status')
    if status == 'connected':
        status_icon = admin_icon('success')
    elif status == 'not_configured':
        status_icon = admin_icon('info')
    else:
        status_icon = admin_icon('error')

    api_url_display = settings.REMNAWAVE_API_URL or '—'

    text = '\n\n'.join(
        [
            admin_heading('servers', 'Управление Remnawave'),
            admin_quote(
                '\n'.join(
                    [
                        (
                            f'{status_icon} <b>Соединение:</b> '
                            f'{html.escape(str(connection_test.get("message", "Нет данных")))}'
                        ),
                        f'{admin_icon("link")} <b>URL:</b> <code>{html.escape(api_url_display)}</code>',
                    ]
                )
            ),
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='servers', compact=True),
        reply_markup=get_admin_remnawave_keyboard(db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_system_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.get_system_statistics()

    if 'error' in stats:
        await callback.message.edit_text(
            admin_screen(
                texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYSTEM_STATS_027', 'Ошибка получения статистики: {value1}').format(
                    value1=html.escape(str(stats['error']))
                ),
                icon='error',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[admin_back_button(texts, 'admin_remnawave')]]),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    system = stats.get('system', {})
    users_by_status = stats.get('users_by_status', {})
    server_info = stats.get('server_info', {})
    bandwidth = stats.get('bandwidth', {})
    traffic_periods = stats.get('traffic_periods', {})
    nodes_realtime = stats.get('nodes_realtime', [])
    nodes_weekly = stats.get('nodes_weekly', [])

    memory_total = server_info.get('memory_total', 1)
    memory_used_percent = (server_info.get('memory_used', 0) / memory_total * 100) if memory_total > 0 else 0

    uptime_seconds = server_info.get('uptime_seconds', 0)
    uptime_days = int(uptime_seconds // 86400)
    uptime_hours = int((uptime_seconds % 86400) // 3600)
    uptime_str = texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYSTEM_STATS_029', '{value1}д {value2}ч').format(
        value1=uptime_days, value2=uptime_hours
    )

    status_icons = {
        'ACTIVE': admin_icon('success'),
        'DISABLED': admin_icon('error'),
        'LIMITED': admin_icon('warning'),
        'EXPIRED': admin_icon('clock'),
    }
    users_status_text = ''
    for status, count in users_by_status.items():
        status_icon = status_icons.get(status, admin_icon('info'))
        users_status_text += f'{status_icon} {html.escape(_localize_panel_value(texts, status))}: {count}\n'

    top_nodes_text = ''
    for i, node in enumerate(nodes_weekly[:3], 1):
        top_nodes_text += f'{i}. {html.escape(str(node["name"]))}: {format_bytes(node["total_bytes"])}\n'

    realtime_nodes_text = ''
    for node in nodes_realtime[:3]:
        node_total = node.get('downloadBytes', 0) + node.get('uploadBytes', 0)
        if node_total > 0:
            node_name = node.get('nodeName', texts.t('ADMIN_PANEL_VALUE_UNKNOWN_NODE', 'Неизвестный'))
            realtime_nodes_text += f'{html.escape(str(node_name))}: {format_bytes(node_total)}\n'

    def format_traffic_change(difference_str):
        if not difference_str or difference_str == '0':
            return ''
        if difference_str.startswith('-'):
            return f' (↓ {difference_str[1:]})'
        return f' (↑ {difference_str})'

    server_block = '\n'.join(
        [
            f'{admin_icon("system")} <b>Сервер</b>',
            f'• CPU: {server_info.get("cpu_cores", 0)} ядер',
            (
                f'• RAM: {format_bytes(server_info.get("memory_used", 0))} / {format_bytes(memory_total)} '
                f'({memory_used_percent:.1f}%)'
            ),
            f'• Свободно: {format_bytes(server_info.get("memory_free", 0))}',
            f'• Uptime: {uptime_str}',
        ]
    )
    users_block = '\n'.join(
        [
            f'{admin_icon("users")} <b>Пользователи</b> ({system.get("total_users", 0)} всего)',
            f'• Онлайн сейчас: {system.get("users_online", 0)}',
            f'• За сутки: {system.get("users_last_day", 0)}',
            f'• За неделю: {system.get("users_last_week", 0)}',
            f'• Никогда не заходили: {system.get("users_never_online", 0)}',
        ]
    )
    statuses_block = f'{admin_icon("info")} <b>Статусы пользователей</b>\n{users_status_text}'.rstrip()
    nodes_block = f'{admin_icon("servers")} <b>Ноды</b> ({system.get("nodes_online", 0)} онлайн)'

    parts = [
        admin_heading('chart', 'Детальная статистика Remnawave'),
        admin_quote(server_block),
        admin_quote(users_block),
        admin_quote(statuses_block),
        nodes_block,
    ]

    if realtime_nodes_text:
        parts.append(
            admin_quote(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_SHOW_SYSTEM_STATS_031',
                    '<b>Реалтайм активность:</b>\n{value1}',
                ).format(value1=realtime_nodes_text)
            )
        )

    if top_nodes_text:
        parts.append(
            admin_quote(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_SHOW_SYSTEM_STATS_032',
                    '<b>Топ нод за неделю:</b>\n{value1}',
                ).format(value1=top_nodes_text)
            )
        )

    traffic_block = '\n'.join(
        [
            (
                f'{admin_icon("chart")} <b>Общий трафик пользователей:</b> '
                f'{format_bytes(system.get("total_user_traffic", 0))}'
            ),
            (
                '• 2 дня: '
                f'{format_bytes(traffic_periods.get("last_2_days", {}).get("current", 0))}'
                f'{format_traffic_change(traffic_periods.get("last_2_days", {}).get("difference", ""))}'
            ),
            (
                '• 7 дней: '
                f'{format_bytes(traffic_periods.get("last_7_days", {}).get("current", 0))}'
                f'{format_traffic_change(traffic_periods.get("last_7_days", {}).get("difference", ""))}'
            ),
            (
                '• 30 дней: '
                f'{format_bytes(traffic_periods.get("last_30_days", {}).get("current", 0))}'
                f'{format_traffic_change(traffic_periods.get("last_30_days", {}).get("difference", ""))}'
            ),
            (
                '• Месяц: '
                f'{format_bytes(traffic_periods.get("current_month", {}).get("current", 0))}'
                f'{format_traffic_change(traffic_periods.get("current_month", {}).get("difference", ""))}'
            ),
            (
                '• Год: '
                f'{format_bytes(traffic_periods.get("current_year", {}).get("current", 0))}'
                f'{format_traffic_change(traffic_periods.get("current_year", {}).get("difference", ""))}'
            ),
        ]
    )
    parts.append(admin_quote(traffic_block))

    if bandwidth.get('realtime_total', 0) > 0:
        parts.append(
            admin_quote(
                '\n'.join(
                    [
                        f'{admin_icon("battery")} <b>Реалтайм трафик</b>',
                        f'• Скачивание: {format_bytes(bandwidth.get("realtime_download", 0))}',
                        f'• Загрузка: {format_bytes(bandwidth.get("realtime_upload", 0))}',
                        f'• Итого: {format_bytes(bandwidth.get("realtime_total", 0))}',
                    ]
                )
            )
        )

    parts.append(
        f'{admin_icon("clock")} <b>Обновлено:</b> {format_datetime(stats.get("last_updated", datetime.now(UTC)))}'
    )
    text = '\n\n'.join(parts)

    keyboard = [
        [
            admin_button(
                texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYSTEM_STATS_036', 'Обновить'),
                callback_data='admin_rw_system',
                icon='refresh',
            )
        ],
        [
            admin_button(
                texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYSTEM_STATS_037', 'Ноды'),
                callback_data='admin_rw_nodes',
                icon='servers',
            ),
            admin_button(
                texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYSTEM_STATS_038', 'Синхронизация'),
                callback_data='admin_rw_sync',
                icon='sync',
            ),
        ],
        [admin_back_button(texts, 'admin_remnawave')],
    ]

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='chart', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_traffic_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    remnawave_service = RemnaWaveService()

    try:
        async with remnawave_service.get_api_client() as api:
            bandwidth_stats = await api.get_bandwidth_stats()

            realtime_usage = await api.get_nodes_realtime_usage()

            nodes_stats = await api.get_nodes_statistics()

    except Exception as e:
        await callback.message.edit_text(
            admin_screen(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_SHOW_TRAFFIC_STATS_040', 'Ошибка получения статистики трафика: {value1}'
                ).format(value1=html.escape(str(e))),
                icon='error',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[admin_back_button(texts, 'admin_remnawave')]]),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    def parse_bandwidth(bandwidth_str):
        return remnawave_service._parse_bandwidth_string(bandwidth_str)

    total_realtime_download = sum(node.get('downloadBytes', 0) for node in realtime_usage)
    total_realtime_upload = sum(node.get('uploadBytes', 0) for node in realtime_usage)
    total_realtime = total_realtime_download + total_realtime_upload

    total_users_online = sum(node.get('usersOnline', 0) for node in realtime_usage)

    periods = {
        'last_2_days': bandwidth_stats.get('bandwidthLastTwoDays', {}),
        'last_7_days': bandwidth_stats.get('bandwidthLastSevenDays', {}),
        'last_30_days': bandwidth_stats.get('bandwidthLast30Days', {}),
        'current_month': bandwidth_stats.get('bandwidthCalendarMonth', {}),
        'current_year': bandwidth_stats.get('bandwidthCurrentYear', {}),
    }

    def format_change(diff_str):
        if not diff_str or diff_str == '0':
            return ''
        if diff_str.startswith('-'):
            return f'  ↓ {diff_str[1:]}'
        return f'  ↑ {diff_str}'

    realtime_block = '\n'.join(
        [
            f'{admin_icon("wifi")} <b>Трафик по inbounds</b>',
            f'• Скачивание: {format_bytes(total_realtime_download)}',
            f'• Загрузка: {format_bytes(total_realtime_upload)}',
            f'• Общий трафик: {format_bytes(total_realtime)}',
            f'• Пользователи онлайн: {total_users_online}',
        ]
    )

    period_lines = []
    for label, key in (
        ('2 дня', 'last_2_days'),
        ('7 дней', 'last_7_days'),
        ('30 дней', 'last_30_days'),
        ('Текущий месяц', 'current_month'),
        ('Текущий год', 'current_year'),
    ):
        period = periods[key]
        period_lines.extend(
            [
                f'{admin_icon("calendar")} <b>За {label}</b>',
                f'• Текущий: {format_bytes(parse_bandwidth(period.get("current", "0")))}',
                f'• Предыдущий: {format_bytes(parse_bandwidth(period.get("previous", "0")))}',
                f'• Изменение:{format_change(period.get("difference", ""))}',
            ]
        )

    parts = [
        admin_heading('chart', 'Статистика трафика Remnawave'),
        admin_quote(realtime_block),
        admin_quote('\n'.join(period_lines)),
    ]

    if realtime_usage:
        realtime_nodes = texts.t('ADMIN_REMNAWAVE_UI_SHOW_TRAFFIC_STATS_043', ' <b>Трафик по нодам (реалтайм):</b>\n')
        for node in sorted(realtime_usage, key=lambda x: x.get('totalBytes', 0), reverse=True):
            node_total = node.get('totalBytes', 0)
            if node_total > 0:
                node_name = node.get('nodeName', texts.t('ADMIN_PANEL_VALUE_UNKNOWN_NODE', 'Неизвестный'))
                realtime_nodes += f'• {html.escape(str(node_name))}: {format_bytes(node_total)}\n'

        parts.append(admin_quote(realtime_nodes.strip()))

    if nodes_stats.get('lastSevenDays'):
        weekly_nodes = texts.t('ADMIN_REMNAWAVE_UI_SHOW_TRAFFIC_STATS_044', ' <b>Топ нод за 7 дней:</b>\n')

        nodes_weekly = {}
        for day_data in nodes_stats['lastSevenDays']:
            node_name = day_data['nodeName']
            if node_name not in nodes_weekly:
                nodes_weekly[node_name] = 0
            nodes_weekly[node_name] += int(day_data['totalBytes'])

        sorted_nodes = sorted(nodes_weekly.items(), key=lambda x: x[1], reverse=True)
        for i, (node_name, total_bytes) in enumerate(sorted_nodes[:5], 1):
            weekly_nodes += f'• {i}. {html.escape(str(node_name))}: {format_bytes(total_bytes)}\n'

        parts.append(admin_quote(weekly_nodes.strip()))

    parts.append(f'{admin_icon("clock")} <b>Обновлено:</b> {format_datetime(datetime.now(UTC))}')
    text = '\n\n'.join(parts)

    keyboard = [
        [
            admin_button(
                texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYSTEM_STATS_036', 'Обновить'),
                callback_data='admin_rw_traffic',
                icon='refresh',
            )
        ],
        [
            admin_button(
                texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYSTEM_STATS_037', 'Ноды'),
                callback_data='admin_rw_nodes',
                icon='servers',
            ),
            admin_button(
                texts.t('ADMIN_REMNAWAVE_UI_SHOW_TRAFFIC_STATS_048', 'Система'),
                callback_data='admin_rw_system',
                icon='chart',
            ),
        ],
        [admin_back_button(texts, 'admin_remnawave')],
    ]

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='chart', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_nodes_management(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    remnawave_service = RemnaWaveService()
    nodes = await remnawave_service.get_all_nodes()

    if not nodes:
        await callback.message.edit_text(
            admin_screen(
                texts.t('ADMIN_REMNAWAVE_UI_SHOW_NODES_MANAGEMENT_050', 'Ноды не найдены или ошибка подключения'),
                icon='warning',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[admin_back_button(texts, 'admin_remnawave')]]),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    text = admin_heading('servers', 'Управление нодами')
    keyboard = []
    node_chunks = []

    for node in nodes:
        status_icon = admin_icon('success') if node['is_node_online'] else admin_icon('error')
        connection_icon = admin_icon('success') if node['is_connected'] else admin_icon('error')

        chunk = '\n'.join(
            [
                f'{status_icon} {connection_icon} <b>{html.escape(str(node["name"]))}</b>',
                (f'{admin_icon("map")} {html.escape(str(node["country_code"]))} • {html.escape(str(node["address"]))}'),
                f'{admin_icon("users")} Онлайн: {node["users_online"] or 0}',
            ]
        )
        node_chunks.append(chunk)

        keyboard.append(
            [
                admin_button(
                    str(node['name']),
                    callback_data=f'admin_node_manage_{node["uuid"]}',
                    icon='servers',
                )
            ]
        )

    if node_chunks:
        text += '\n\n' + '\n\n'.join(admin_quote(chunk) for chunk in node_chunks)

    keyboard.extend(
        [
            [
                admin_button(
                    'Перезагрузить все',
                    callback_data='admin_restart_all_nodes',
                    icon='refresh',
                )
            ],
            [admin_back_button(texts, 'admin_remnawave')],
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='servers', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_node_details(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    node_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    node = await remnawave_service.get_node_details(node_uuid)

    if not node:
        await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_SHOW_NODE_DETAILS_056', 'Нода не найдена'), show_alert=True)
        return

    status_icon = admin_icon('success') if node['is_node_online'] else admin_icon('error')
    xray_icon = admin_icon('success') if node['is_xray_running'] else admin_icon('error')

    status_change = format_datetime(node['last_status_change']) if node.get('last_status_change') else '—'
    created_at = format_datetime(node['created_at']) if node.get('created_at') else '—'
    updated_at = format_datetime(node['updated_at']) if node.get('updated_at') else '—'
    notify_percent = f'{node["notify_percent"]}%' if node.get('notify_percent') is not None else '—'
    sys_info = (node.get('system') or {}).get('info', {})
    cpu_model = html.escape(str(sys_info.get('cpuModel') or '—'))
    cpu_count = sys_info.get('cpus', 0)
    cpu_info = f'{cpu_count}x {cpu_model}' if cpu_count else cpu_model
    memory_total = sys_info.get('memoryTotal', 0)
    total_ram = format_bytes(memory_total) if memory_total else '—'
    versions = node.get('versions') or {}
    xray_ver = html.escape(str(versions.get('xray') or '—'))
    node_ver = html.escape(str(versions.get('node') or '—'))
    xray_uptime_sec = node.get('xray_uptime', 0)
    if xray_uptime_sec:
        days, rem = divmod(int(xray_uptime_sec), 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        xray_uptime_str = f'{days}d {hours}h {mins}m' if days else (f'{hours}h {mins}m' if hours else f'{mins}m')
    else:
        xray_uptime_str = '—'

    status_block = '\n'.join(
        [
            f'{admin_icon("info")} <b>Статус</b>',
            f'• Онлайн: {status_icon} {"Да" if node["is_node_online"] else "Нет"}',
            f'• Xray: {xray_icon} {"Запущен" if node["is_xray_running"] else "Остановлен"}',
            f'• Подключена: {"Да" if node["is_connected"] else "Нет"}',
            f'• Отключена: {"Да" if node["is_disabled"] else "Нет"}',
            f'• Изменение статуса: {status_change}',
            f'• Сообщение: {html.escape(str(node.get("last_status_message") or "—"))}',
            f'• Uptime Xray: {xray_uptime_str}',
        ]
    )
    versions_block = '\n'.join(
        [
            f'{admin_icon("settings")} <b>Версии</b>',
            f'• Xray: {xray_ver}',
            f'• Node: {node_ver}',
        ]
    )
    info_block = '\n'.join(
        [
            f'{admin_icon("servers")} <b>Информация</b>',
            f'• Адрес: {html.escape(str(node["address"]))}',
            f'• Страна: {html.escape(str(node["country_code"]))}',
            f'• Пользователей онлайн: {node["users_online"]}',
            f'• CPU: {cpu_info}',
            f'• RAM: {total_ram}',
            f'• Провайдер: {html.escape(str(node.get("provider_uuid") or "—"))}',
        ]
    )
    traffic_block = '\n'.join(
        [
            f'{admin_icon("chart")} <b>Трафик</b>',
            f'• Использовано: {format_bytes(node["traffic_used_bytes"])}',
            f'• Лимит: {format_bytes(node["traffic_limit_bytes"]) if node["traffic_limit_bytes"] else "Без лимита"}',
            f'• Трекинг: {"Активен" if node.get("is_traffic_tracking_active") else "Отключен"}',
            f'• День сброса: {node.get("traffic_reset_day") or "—"}',
            f'• Уведомления: {notify_percent}',
            f'• Множитель: {node.get("consumption_multiplier") or 1}',
        ]
    )
    meta_block = '\n'.join(
        [
            f'{admin_icon("calendar")} <b>Метаданные</b>',
            f'• Создана: {created_at}',
            f'• Обновлена: {updated_at}',
        ]
    )

    text = '\n\n'.join(
        [
            admin_heading('servers', f'Нода: {html.escape(str(node["name"]))}'),
            admin_quote(status_block),
            admin_quote(versions_block),
            admin_quote(info_block),
            admin_quote(traffic_block),
            admin_quote(meta_block),
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='servers', compact=True),
        reply_markup=get_node_management_keyboard(node_uuid, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def manage_node(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    action, node_uuid = callback.data.split('_')[1], callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    success = await remnawave_service.manage_node(node_uuid, action)

    if success:
        action_text = {
            'enable': texts.t('ADMIN_REMNAWAVE_UI_MANAGE_NODE_069', 'включена'),
            'disable': texts.t('ADMIN_REMNAWAVE_UI_MANAGE_NODE_070', 'отключена'),
            'restart': texts.t('ADMIN_REMNAWAVE_UI_MANAGE_NODE_071', 'перезагружена'),
        }
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_MANAGE_NODE_072', 'Нода {value1}').format(
                value1=action_text.get(action, texts.t('ADMIN_REMNAWAVE_UI_MANAGE_NODE_073', 'обработана'))
            )
        )
    else:
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_MANAGE_NODE_074', 'Ошибка выполнения действия'), show_alert=True
        )

    await show_node_details(callback, db_user, db)


@admin_required
@error_handler
async def show_node_statistics(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    node_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()

    node = await remnawave_service.get_node_details(node_uuid)

    if not node:
        await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_SHOW_NODE_DETAILS_056', 'Нода не найдена'), show_alert=True)
        return

    status_icon = admin_icon('success') if node['is_node_online'] else admin_icon('error')
    xray_icon = admin_icon('success') if node['is_xray_running'] else admin_icon('error')
    xray_uptime_sec = node.get('xray_uptime', 0)
    if xray_uptime_sec:
        days, rem = divmod(int(xray_uptime_sec), 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        xray_uptime_str = f'{days}d {hours}h {mins}m' if days else (f'{hours}h {mins}m' if hours else f'{mins}m')
    else:
        xray_uptime_str = '—'

    try:
        end_date = datetime.now(UTC)
        start_date = end_date - timedelta(days=7)

        node_usage = await remnawave_service.get_node_user_usage_by_range(node_uuid, start_date, end_date)

        realtime_stats = await remnawave_service.get_nodes_realtime_usage()

        node_realtime = None
        for stats in realtime_stats:
            if stats.get('nodeUuid') == node_uuid:
                node_realtime = stats
                break

        status_change = format_datetime(node['last_status_change']) if node.get('last_status_change') else '—'
        notify_percent = f'{node["notify_percent"]}%' if node.get('notify_percent') is not None else '—'
        sys_info = (node.get('system') or {}).get('info', {})
        cpu_model = html.escape(str(sys_info.get('cpuModel') or '—'))
        cpu_count = sys_info.get('cpus', 0)
        cpu_info = f'{cpu_count}x {cpu_model}' if cpu_count else cpu_model
        memory_total = sys_info.get('memoryTotal', 0)
        total_ram = format_bytes(memory_total) if memory_total else '—'
        sys_stats = (node.get('system') or {}).get('stats', {})
        load_avg = sys_stats.get('loadAvg', [])
        load_str = ' / '.join(f'{v:.2f}' for v in load_avg[:3]) if load_avg else '—'
        versions = node.get('versions') or {}
        xray_ver = html.escape(str(versions.get('xray') or '—'))
        node_ver = html.escape(str(versions.get('node') or '—'))

        status_block = '\n'.join(
            [
                f'{admin_icon("info")} <b>Статус</b>',
                f'• Онлайн: {status_icon} {"Да" if node["is_node_online"] else "Нет"}',
                f'• Xray: {xray_icon} {"Запущен" if node["is_xray_running"] else "Остановлен"} (v{xray_ver})',
                f'• Node: v{node_ver}',
                f'• Пользователей онлайн: {node["users_online"]}',
                f'• Изменение статуса: {status_change}',
                f'• Сообщение: {html.escape(str(node.get("last_status_message") or "—"))}',
                f'• Uptime Xray: {xray_uptime_str}',
            ]
        )
        resources_block = '\n'.join(
            [
                f'{admin_icon("system")} <b>Ресурсы</b>',
                f'• CPU: {cpu_info}',
                f'• RAM: {total_ram}',
                f'• Load: {load_str}',
                f'• Провайдер: {html.escape(str(node.get("provider_uuid") or "—"))}',
            ]
        )
        traffic_block = '\n'.join(
            [
                f'{admin_icon("chart")} <b>Трафик</b>',
                f'• Использовано: {format_bytes(node["traffic_used_bytes"] or 0)}',
                f'• Лимит: {format_bytes(node["traffic_limit_bytes"]) if node["traffic_limit_bytes"] else "Без лимита"}',
                f'• Трекинг: {"Активен" if node.get("is_traffic_tracking_active") else "Отключен"}',
                f'• День сброса: {node.get("traffic_reset_day") or "—"}',
                f'• Уведомления: {notify_percent}',
                f'• Множитель: {node.get("consumption_multiplier") or 1}',
            ]
        )
        text = '\n\n'.join(
            [
                admin_heading('chart', f'Статистика ноды: {html.escape(str(node["name"]))}'),
                admin_quote(status_block),
                admin_quote(resources_block),
                admin_quote(traffic_block),
            ]
        )

        if node_realtime:
            text += '\n\n' + admin_quote(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_SHOW_NODE_STATISTICS_084',
                    '<b>Трафик по inbounds:</b>\n- Скачано: {value1}\n- Загружено: {value2}\n- Общий трафик: {value3}\n- Онлайн: {value4}\n',
                )
                .format(
                    value1=format_bytes(node_realtime.get('downloadBytes', 0)),
                    value2=format_bytes(node_realtime.get('uploadBytes', 0)),
                    value3=format_bytes(node_realtime.get('totalBytes', 0)),
                    value4=node_realtime.get('usersOnline', 0),
                )
                .strip()
            )

        if node_usage:
            # Remnawave 3.0.0 удалил суточную разбивку по ноде: POST
            # /api/bandwidth-stats/nodes/usage отдаёт строку НА ПОЛЬЗОВАТЕЛЯ
            # ({id, totalBytes}) за весь период, без дат. Поэтому показываем
            # топ потребителей, а не «по дням», и суммируем ВЕСЬ список —
            # срез для показа не должен влиять на итог.
            total_usage = sum(entry.get('total', 0) or 0 for entry in node_usage)
            top_consumers = sorted(node_usage, key=lambda e: e.get('total', 0) or 0, reverse=True)[:5]

            usage_block = texts.t('ADMIN_REMNAWAVE_UI_SHOW_NODE_STATISTICS_085', '<b>Топ потребителей за 7 дней:</b>\n')
            for usage in top_consumers:
                panel_user_id = usage.get('userId')
                label = (
                    f'ID {panel_user_id}'
                    if panel_user_id is not None
                    else texts.t('ADMIN_REMNAWAVE_UI_SHOW_NODE_STATISTICS_086', 'неизвестный')
                )
                usage_block += f'• {html.escape(str(label))}: {format_bytes(usage.get("total", 0) or 0)}\n'

            if len(node_usage) > len(top_consumers):
                usage_block += texts.t('ADMIN_REMNAWAVE_UI_SHOW_NODE_STATISTICS_087', '- …и ещё {value1}\n').format(
                    value1=len(node_usage) - len(top_consumers)
                )

            usage_block += texts.t(
                'ADMIN_REMNAWAVE_UI_SHOW_NODE_STATISTICS_088', '<b>Общий трафик за 7 дней:</b> {value1}'
            ).format(value1=format_bytes(total_usage))
            text += '\n\n' + admin_quote(usage_block.strip())
        else:
            text += '\n\n' + admin_quote(
                texts.t('ADMIN_REMNAWAVE_UI_SHOW_NODE_STATISTICS_089', '<b>Статистика за 7 дней:</b> Данные недоступны')
            )

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_button(
                        'Обновить',
                        callback_data=f'node_stats_{node_uuid}',
                        icon='refresh',
                    )
                ],
                [admin_back_button(texts, f'admin_node_manage_{node_uuid}')],
            ]
        )

        await callback.message.edit_text(
            admin_screen(_clean_legacy(text), icon='chart', compact=True),
            reply_markup=keyboard,
            parse_mode='HTML',
        )
        await callback.answer()

    except Exception as e:
        logger.error('Ошибка получения статистики ноды', node_uuid=node_uuid, error=e)

        fallback_status = '\n'.join(
            [
                f'{admin_icon("info")} <b>Статус</b>',
                f'• Онлайн: {status_icon} {"Да" if node["is_node_online"] else "Нет"}',
                f'• Xray: {xray_icon} {"Запущен" if node["is_xray_running"] else "Остановлен"}',
                f'• Пользователей онлайн: {node["users_online"]}',
                (
                    f'• Изменение статуса: '
                    f'{format_datetime(node.get("last_status_change")) if node.get("last_status_change") else "—"}'
                ),
                f'• Сообщение: {html.escape(str(node.get("last_status_message") or "—"))}',
                f'• Uptime Xray: {xray_uptime_str}',
            ]
        )
        fallback_traffic = '\n'.join(
            [
                f'{admin_icon("chart")} <b>Трафик</b>',
                f'• Использовано: {format_bytes(node["traffic_used_bytes"] or 0)}',
                f'• Лимит: {format_bytes(node["traffic_limit_bytes"]) if node["traffic_limit_bytes"] else "Без лимита"}',
                f'• Трекинг: {"Активен" if node.get("is_traffic_tracking_active") else "Отключен"}',
                f'• День сброса: {node.get("traffic_reset_day") or "—"}',
                f'• Уведомления: {node.get("notify_percent") or "—"}',
                f'• Множитель: {node.get("consumption_multiplier") or 1}',
            ]
        )
        text = '\n\n'.join(
            [
                admin_heading('warning', f'Статистика ноды: {html.escape(str(node["name"]))}'),
                admin_quote(fallback_status),
                admin_quote(fallback_traffic),
                (
                    f'{admin_icon("warning")} <b>Детальная статистика временно недоступна</b>\n'
                    'Возможные причины:\n'
                    '• Проблемы с подключением к API\n'
                    '• Нода недавно добавлена\n'
                    '• Недостаточно данных для отображения'
                ),
                f'{admin_icon("clock")} <b>Обновлено:</b> {format_datetime(datetime.now(UTC))}',
            ]
        )

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_button(
                        'Попробовать снова',
                        callback_data=f'node_stats_{node_uuid}',
                        icon='refresh',
                    )
                ],
                [admin_back_button(texts, f'admin_node_manage_{node_uuid}')],
            ]
        )

        await callback.message.edit_text(
            admin_screen(_clean_legacy(text), icon='warning', compact=True),
            reply_markup=keyboard,
            parse_mode='HTML',
        )
        await callback.answer()


@admin_required
@error_handler
async def show_squad_details(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)

    if not squad:
        await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_DETAILS_102', 'Сквад не найден'), show_alert=True)
        return

    info_block = '\n'.join(
        [
            f'{admin_icon("id")} <b>Информация</b>',
            f'• UUID: <code>{html.escape(str(squad["uuid"]))}</code>',
            f'• Участников: {admin_icon("users")} {squad["members_count"]}',
            f'• Инбаундов: {admin_icon("network")} {squad["inbounds_count"]}',
        ]
    )

    inbounds_lines = [f'{admin_icon("link")} <b>Инбаунды</b>']
    if squad.get('inbounds'):
        for inbound in squad['inbounds']:
            inbounds_lines.append(f'• {html.escape(str(inbound["tag"]))} ({html.escape(str(inbound["type"]))})')
    else:
        inbounds_lines.append(
            f'{admin_icon("warning")} {texts.t("ADMIN_REMNAWAVE_UI_SHOW_SQUAD_DETAILS_104", "Нет активных инбаундов")}'
        )

    text = '\n\n'.join(
        [
            admin_heading('servers', f'Сквад: {html.escape(str(squad["name"]))}'),
            admin_quote(info_block),
            admin_quote('\n'.join(inbounds_lines)),
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='servers', compact=True),
        reply_markup=get_squad_management_keyboard(squad_uuid, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def manage_squad_action(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    parts = callback.data.split('_')
    action = parts[1]
    squad_uuid = parts[-1]

    remnawave_service = RemnaWaveService()

    if action == 'add_users':
        success = await remnawave_service.add_all_users_to_squad(squad_uuid)
        if success:
            await callback.answer(
                texts.t('ADMIN_REMNAWAVE_UI_MANAGE_SQUAD_ACTION_105', 'Задача добавления пользователей в очередь')
            )
        else:
            await callback.answer(
                texts.t('ADMIN_REMNAWAVE_UI_MANAGE_SQUAD_ACTION_106', 'Ошибка добавления пользователей'),
                show_alert=True,
            )

    elif action == 'remove_users':
        success = await remnawave_service.remove_all_users_from_squad(squad_uuid)
        if success:
            await callback.answer(
                texts.t('ADMIN_REMNAWAVE_UI_MANAGE_SQUAD_ACTION_107', 'Задача удаления пользователей в очередь')
            )
        else:
            await callback.answer(
                texts.t('ADMIN_REMNAWAVE_UI_MANAGE_SQUAD_ACTION_108', 'Ошибка удаления пользователей'),
                show_alert=True,
            )

    elif action == 'delete':
        success = await remnawave_service.delete_squad(squad_uuid)
        if success:
            await callback.message.edit_text(
                admin_screen(
                    texts.t('ADMIN_REMNAWAVE_UI_MANAGE_SQUAD_ACTION_109', 'Сквад успешно удален'),
                    icon='success',
                    compact=True,
                ),
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            admin_back_button(
                                texts,
                                'admin_rw_squads',
                                label=texts.t('ADMIN_REMNAWAVE_UI_FINISH_SQUAD_CREATION_170', 'Список сквадов'),
                            )
                        ]
                    ]
                ),
                parse_mode='HTML',
            )
        else:
            await callback.answer(
                texts.t('ADMIN_REMNAWAVE_UI_MANAGE_SQUAD_ACTION_111', 'Ошибка удаления сквада'), show_alert=True
            )
        return

    refreshed_callback = callback.model_copy(update={'data': f'admin_squad_manage_{squad_uuid}'}).as_(callback.bot)

    await show_squad_details(refreshed_callback, db_user, db)


@admin_required
@error_handler
async def show_squad_edit_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)

    if not squad:
        await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_DETAILS_102', 'Сквад не найден'), show_alert=True)
        return

    inbounds_lines = [f'{admin_icon("link")} <b>Текущие инбаунды</b>']
    if squad.get('inbounds'):
        for inbound in squad['inbounds']:
            inbounds_lines.append(f'• {html.escape(str(inbound["tag"]))} ({html.escape(str(inbound["type"]))})')
    else:
        inbounds_lines.append(
            f'{admin_icon("warning")} {texts.t("ADMIN_REMNAWAVE_UI_SHOW_SQUAD_EDIT_MENU_114", "Нет активных инбаундов")}'
        )

    text = '\n\n'.join(
        [
            admin_heading('edit', f'Редактирование сквада: {html.escape(str(squad["name"]))}'),
            admin_quote('\n'.join(inbounds_lines)),
            f'{admin_icon("settings")} <b>Доступные действия</b>',
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='edit', compact=True),
        reply_markup=get_squad_edit_keyboard(squad_uuid, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_squad_inbounds_selection(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()

    squad = await remnawave_service.get_squad_details(squad_uuid)
    all_inbounds = await remnawave_service.get_all_inbounds()

    if not squad:
        await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_DETAILS_102', 'Сквад не найден'), show_alert=True)
        return

    if not all_inbounds:
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_INBOUNDS_SELECTION_117', 'Нет доступных инбаундов'),
            show_alert=True,
        )
        return

    if squad_uuid not in squad_inbound_selections:
        squad_inbound_selections[squad_uuid] = {inbound['uuid'] for inbound in squad.get('inbounds', [])}

    text = '\n\n'.join(
        [
            admin_heading('settings', 'Изменение инбаундов'),
            admin_quote(
                '\n'.join(
                    [
                        f'{admin_icon("servers")} <b>Сквад:</b> {html.escape(str(squad["name"]))}',
                        (f'{admin_icon("link")} <b>Текущих инбаундов:</b> {len(squad_inbound_selections[squad_uuid])}'),
                    ]
                )
            ),
            f'{admin_icon("network")} <b>Доступные инбаунды</b>',
        ]
    )

    keyboard = []

    for i, inbound in enumerate(all_inbounds[:15]):
        is_selected = inbound['uuid'] in squad_inbound_selections[squad_uuid]
        marker = '✅' if is_selected else '▫️'

        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=f'{marker} {inbound["tag"]} ({inbound["type"]})',
                    callback_data=f'sqd_tgl_{i}_{squad_uuid[:8]}',
                )
            ]
        )

    if len(all_inbounds) > 15:
        text += texts.t(
            'ADMIN_REMNAWAVE_UI_SHOW_SQUAD_INBOUNDS_SELECTION_119', '\nПоказано первые 15 из {value1} инбаундов'
        ).format(value1=len(all_inbounds))

    keyboard.extend(
        [
            [
                admin_button(
                    'Сохранить изменения',
                    callback_data=f'sqd_save_{squad_uuid[:8]}',
                    icon='save',
                )
            ],
            [admin_back_button(texts, f'sqd_edit_{squad_uuid[:8]}')],
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='settings', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_squad_rename_form(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    texts = get_texts(db_user.language)
    squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)

    if not squad:
        await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_DETAILS_102', 'Сквад не найден'), show_alert=True)
        return

    await state.update_data(squad_uuid=squad_uuid, squad_name=squad['name'])
    await state.set_state(SquadRenameStates.waiting_for_new_name)

    text = _quote_after_title(
        texts.t(
            'ADMIN_REMNAWAVE_UI_SHOW_SQUAD_RENAME_FORM_123',
            '<b>Переименование сквада</b>\n\n<b>Текущее название:</b> {value1}\n\n<b>Введите новое название сквада:</b>\n\n<i>Требования к названию:</i>\n• От 2 до 20 символов\n• Только буквы, цифры, дефисы и подчеркивания\n• Без пробелов и специальных символов\n\nОтправьте сообщение с новым названием или нажмите "Отмена" для выхода.\n',
        ).format(value1=html.escape(str(squad['name'])))
    )

    keyboard = [
        [
            admin_back_button(
                texts,
                f'cancel_rename_{squad_uuid}',
                label=texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_RENAME_FORM_124', 'Отмена'),
            )
        ]
    ]

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='edit', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def cancel_squad_rename(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    squad_uuid = callback.data.split('_')[-1]

    await state.clear()

    refreshed_callback = callback.model_copy(update={'data': f'squad_edit_{squad_uuid}'}).as_(callback.bot)

    await show_squad_edit_menu(refreshed_callback, db_user, db)


@admin_required
@error_handler
async def process_squad_new_name(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    texts = get_texts(db_user.language)
    data = await state.get_data()
    squad_uuid = data.get('squad_uuid')
    old_name = data.get('squad_name')

    if not squad_uuid:
        await message.answer(
            admin_screen(
                texts.t('ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_125', 'Ошибка: сквад не найден'),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )
        await state.clear()
        return

    new_name = message.text.strip()

    if not new_name:
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_126',
                    'Название не может быть пустым. Попробуйте еще раз:',
                ),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    # Правила панели (POST/PATCH /api/internal-squads): иначе она ответит 400.
    if not is_valid_internal_squad_name(new_name):
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_127',
                    'Название: от {value1} до {value2} символов, только латиница, цифры, пробел, дефис и подчёркивание. Попробуйте еще раз:',
                ).format(value1=INTERNAL_SQUAD_NAME_MIN_LENGTH, value2=INTERNAL_SQUAD_NAME_MAX_LENGTH),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    if new_name == old_name:
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_128',
                    'Новое название совпадает с текущим. Введите другое название:',
                ),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    remnawave_service = RemnaWaveService()
    success = await remnawave_service.rename_squad(squad_uuid, new_name)

    if success:
        await message.answer(
            admin_screen(
                _quote_after_title(
                    texts.t(
                        'ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_129',
                        '<b>Сквад успешно переименован!</b>\n\n<b>Старое название:</b> {value1}\n<b>Новое название:</b> {value2}',
                    ).format(value1=html.escape(str(old_name)), value2=html.escape(str(new_name)))
                ),
                icon='success',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_button(
                            texts.t('ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_130', 'Детали сквада'),
                            callback_data=f'admin_squad_manage_{squad_uuid}',
                            icon='servers',
                        )
                    ],
                    [
                        admin_back_button(
                            texts,
                            'admin_rw_squads',
                            label=texts.t('ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_131', 'К сквадам'),
                        )
                    ],
                ]
            ),
            parse_mode='HTML',
        )
        await state.clear()
    else:
        await message.answer(
            admin_screen(
                _quote_after_title(
                    texts.t(
                        'ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_132',
                        '<b>Ошибка переименования сквада</b>\n\nВозможные причины:\n• Сквад с таким названием уже существует\n• Проблемы с подключением к API\n• Недостаточно прав\n\nПопробуйте другое название:',
                    )
                ),
                icon='error',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            f'cancel_rename_{squad_uuid}',
                            label=texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_RENAME_FORM_124', 'Отмена'),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def toggle_squad_inbound(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    parts = callback.data.split('_')
    inbound_index = int(parts[2])
    short_squad_uuid = parts[3]

    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()

    full_squad_uuid = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            break

    if not full_squad_uuid:
        await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_DETAILS_102', 'Сквад не найден'), show_alert=True)
        return

    all_inbounds = await remnawave_service.get_all_inbounds()
    if inbound_index >= len(all_inbounds):
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_TOGGLE_SQUAD_INBOUND_135', 'Инбаунд не найден'), show_alert=True
        )
        return

    selected_inbound = all_inbounds[inbound_index]

    if full_squad_uuid not in squad_inbound_selections:
        squad_inbound_selections[full_squad_uuid] = set()

    if selected_inbound['uuid'] in squad_inbound_selections[full_squad_uuid]:
        squad_inbound_selections[full_squad_uuid].remove(selected_inbound['uuid'])
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_TOGGLE_SQUAD_INBOUND_136', 'Убран: {value1}').format(
                value1=selected_inbound['tag']
            )
        )
    else:
        squad_inbound_selections[full_squad_uuid].add(selected_inbound['uuid'])
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_TOGGLE_SQUAD_INBOUND_137', 'Добавлен: {value1}').format(
                value1=selected_inbound['tag']
            )
        )

    text = '\n\n'.join(
        [
            admin_heading('settings', 'Изменение инбаундов'),
            admin_quote(
                '\n'.join(
                    [
                        (
                            f'{admin_icon("servers")} <b>Сквад:</b> '
                            + (
                                html.escape(str(squads[0]['name']))
                                if squads
                                else texts.t('ADMIN_REMNAWAVE_UI_TOGGLE_SQUAD_INBOUND_139', 'Неизвестно')
                            )
                        ),
                        (
                            f'{admin_icon("link")} <b>Выбрано инбаундов:</b> '
                            f'{len(squad_inbound_selections[full_squad_uuid])}'
                        ),
                    ]
                )
            ),
            f'{admin_icon("network")} <b>Доступные инбаунды</b>',
        ]
    )

    keyboard = []
    for i, inbound in enumerate(all_inbounds[:15]):
        is_selected = inbound['uuid'] in squad_inbound_selections[full_squad_uuid]
        marker = '✅' if is_selected else '▫️'

        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=f'{marker} {inbound["tag"]} ({inbound["type"]})',
                    callback_data=f'sqd_tgl_{i}_{short_squad_uuid}',
                )
            ]
        )

    keyboard.extend(
        [
            [
                admin_button(
                    'Сохранить изменения',
                    callback_data=f'sqd_save_{short_squad_uuid}',
                    icon='save',
                )
            ],
            [admin_back_button(texts, f'sqd_edit_{short_squad_uuid}')],
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='settings', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def save_squad_inbounds(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    short_squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()

    full_squad_uuid = None
    squad_name = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            squad_name = squad['name']
            break

    if not full_squad_uuid:
        await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_DETAILS_102', 'Сквад не найден'), show_alert=True)
        return

    selected_inbounds = squad_inbound_selections.get(full_squad_uuid, set())

    try:
        success = await remnawave_service.update_squad_inbounds(full_squad_uuid, list(selected_inbounds))

        if success:
            squad_inbound_selections.pop(full_squad_uuid, None)

            await callback.message.edit_text(
                admin_screen(
                    _quote_after_title(
                        texts.t(
                            'ADMIN_REMNAWAVE_UI_SAVE_SQUAD_INBOUNDS_143',
                            '<b>Инбаунды сквада обновлены</b>\n\n<b>Сквад:</b> {value1}\n<b>Количество инбаундов:</b> {value2}',
                        ).format(value1=html.escape(str(squad_name)), value2=len(selected_inbounds))
                    ),
                    icon='success',
                    compact=True,
                ),
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            admin_back_button(
                                texts,
                                'admin_rw_squads',
                                label=texts.t('ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_131', 'К сквадам'),
                            )
                        ],
                        [
                            admin_button(
                                texts.t('ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_130', 'Детали сквада'),
                                callback_data=f'admin_squad_manage_{full_squad_uuid}',
                                icon='servers',
                            )
                        ],
                    ]
                ),
                parse_mode='HTML',
            )
            await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_SAVE_SQUAD_INBOUNDS_146', 'Изменения сохранены!'))
        else:
            await callback.answer(
                texts.t('ADMIN_REMNAWAVE_UI_SAVE_SQUAD_INBOUNDS_147', 'Ошибка сохранения изменений'), show_alert=True
            )

    except Exception as e:
        logger.error('Error saving squad inbounds', error=e)
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_SAVE_SQUAD_INBOUNDS_148', 'Ошибка при сохранении'), show_alert=True
        )


@admin_required
@error_handler
async def show_squad_edit_menu_short(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    short_squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()

    full_squad_uuid = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            break

    if not full_squad_uuid:
        await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_DETAILS_102', 'Сквад не найден'), show_alert=True)
        return

    refreshed_callback = callback.model_copy(update={'data': f'squad_edit_{full_squad_uuid}'}).as_(callback.bot)

    await show_squad_edit_menu(refreshed_callback, db_user, db)


@admin_required
@error_handler
async def start_squad_creation(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    await state.set_state(SquadCreateStates.waiting_for_name)

    text = '\n\n'.join(
        [
            admin_heading('plus', 'Создание нового сквада'),
            admin_quote(
                f'{admin_icon("edit")} <b>Шаг 1 из 2: Название сквада</b>\n'
                f'{admin_icon("question")} Введите название для нового сквада.'
            ),
            (
                f'{admin_icon("warning")} <b>Требования к названию</b>\n'
                '• От 2 до 20 символов\n'
                '• Только буквы, цифры, дефисы и подчеркивания\n'
                '• Без пробелов и специальных символов'
            ),
            'Отправьте сообщение с названием или нажмите «Отмена» для выхода.',
        ]
    )

    texts = get_texts(db_user.language)
    keyboard = [
        [
            admin_back_button(
                texts,
                'cancel_squad_create',
                label=texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_RENAME_FORM_124', 'Отмена'),
            )
        ]
    ]

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='plus', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_squad_name(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    texts = get_texts(db_user.language)
    squad_name = message.text.strip()

    if not squad_name:
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_126',
                    'Название не может быть пустым. Попробуйте еще раз:',
                ),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    # Правила панели (POST/PATCH /api/internal-squads): иначе она ответит 400.
    if not is_valid_internal_squad_name(squad_name):
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NEW_NAME_127',
                    'Название: от {value1} до {value2} символов, только латиница, цифры, пробел, дефис и подчёркивание. Попробуйте еще раз:',
                ).format(value1=INTERNAL_SQUAD_NAME_MIN_LENGTH, value2=INTERNAL_SQUAD_NAME_MAX_LENGTH),
                icon='warning',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    await state.update_data(squad_name=squad_name)
    await state.set_state(SquadCreateStates.selecting_inbounds)

    user_id = message.from_user.id
    squad_create_data[user_id] = {'name': squad_name, 'selected_inbounds': set()}

    remnawave_service = RemnaWaveService()
    all_inbounds = await remnawave_service.get_all_inbounds()

    if not all_inbounds:
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NAME_154',
                    '<b>Нет доступных инбаундов</b>\n\nДля создания сквада необходимо иметь хотя бы один инбаунд.',
                ),
                icon='warning',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            'admin_rw_squads',
                            label=texts.t('ADMIN_REMNAWAVE_UI_FINISH_SQUAD_CREATION_170', 'Список сквадов'),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )
        await state.clear()
        return

    text = '\n\n'.join(
        [
            admin_heading('plus', f'Создание сквада: {html.escape(str(squad_name))}'),
            admin_quote(
                f'{admin_icon("edit")} <b>Шаг 2 из 2: Выбор инбаундов</b>\n'
                f'{admin_icon("link")} <b>Выбрано инбаундов:</b> 0'
            ),
            f'{admin_icon("network")} <b>Доступные инбаунды</b>',
        ]
    )

    keyboard = []

    for i, inbound in enumerate(all_inbounds[:15]):
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=f'{inbound["tag"]} ({inbound["type"]})',
                    callback_data=f'create_tgl_{i}',
                )
            ]
        )

    if len(all_inbounds) > 15:
        text += texts.t(
            'ADMIN_REMNAWAVE_UI_SHOW_SQUAD_INBOUNDS_SELECTION_119', '\nПоказано первые 15 из {value1} инбаундов'
        ).format(value1=len(all_inbounds))

    keyboard.extend(
        [
            [
                admin_button(
                    texts.t('ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NAME_158', 'Создать сквад'),
                    callback_data='create_squad_finish',
                    icon='plus',
                )
            ],
            [
                admin_back_button(
                    texts,
                    'cancel_squad_create',
                    label=texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_RENAME_FORM_124', 'Отмена'),
                )
            ],
        ]
    )

    await message.answer(
        admin_screen(_clean_legacy(text), icon='plus', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def toggle_create_inbound(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    texts = get_texts(db_user.language)
    inbound_index = int(callback.data.split('_')[-1])
    user_id = callback.from_user.id

    if user_id not in squad_create_data:
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_TOGGLE_CREATE_INBOUND_160', 'Ошибка: данные сессии не найдены'),
            show_alert=True,
        )
        await state.clear()
        return

    remnawave_service = RemnaWaveService()
    all_inbounds = await remnawave_service.get_all_inbounds()

    if inbound_index >= len(all_inbounds):
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_TOGGLE_SQUAD_INBOUND_135', 'Инбаунд не найден'), show_alert=True
        )
        return

    selected_inbound = all_inbounds[inbound_index]
    selected_inbounds = squad_create_data[user_id]['selected_inbounds']

    if selected_inbound['uuid'] in selected_inbounds:
        selected_inbounds.remove(selected_inbound['uuid'])
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_TOGGLE_SQUAD_INBOUND_136', 'Убран: {value1}').format(
                value1=selected_inbound['tag']
            )
        )
    else:
        selected_inbounds.add(selected_inbound['uuid'])
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_TOGGLE_SQUAD_INBOUND_137', 'Добавлен: {value1}').format(
                value1=selected_inbound['tag']
            )
        )

    squad_name = squad_create_data[user_id]['name']

    text = '\n\n'.join(
        [
            admin_heading('plus', f'Создание сквада: {html.escape(str(squad_name))}'),
            admin_quote(
                f'{admin_icon("edit")} <b>Шаг 2 из 2: Выбор инбаундов</b>\n'
                f'{admin_icon("link")} <b>Выбрано инбаундов:</b> {len(selected_inbounds)}'
            ),
            f'{admin_icon("network")} <b>Доступные инбаунды</b>',
        ]
    )

    keyboard = []

    for i, inbound in enumerate(all_inbounds[:15]):
        is_selected = inbound['uuid'] in selected_inbounds
        marker = '✅' if is_selected else '▫️'

        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=f'{marker} {inbound["tag"]} ({inbound["type"]})',
                    callback_data=f'create_tgl_{i}',
                )
            ]
        )

    keyboard.extend(
        [
            [
                admin_button(
                    texts.t('ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NAME_158', 'Создать сквад'),
                    callback_data='create_squad_finish',
                    icon='plus',
                )
            ],
            [
                admin_back_button(
                    texts,
                    'cancel_squad_create',
                    label=texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_RENAME_FORM_124', 'Отмена'),
                )
            ],
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='plus', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def finish_squad_creation(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    texts = get_texts(db_user.language)
    user_id = callback.from_user.id

    if user_id not in squad_create_data:
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_TOGGLE_CREATE_INBOUND_160', 'Ошибка: данные сессии не найдены'),
            show_alert=True,
        )
        await state.clear()
        return

    squad_name = squad_create_data[user_id]['name']
    selected_inbounds = list(squad_create_data[user_id]['selected_inbounds'])

    if not selected_inbounds:
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_FINISH_SQUAD_CREATION_168', 'Необходимо выбрать хотя бы один инбаунд'),
            show_alert=True,
        )
        return

    remnawave_service = RemnaWaveService()
    success = await remnawave_service.create_squad(squad_name, selected_inbounds)

    squad_create_data.pop(user_id, None)
    await state.clear()

    if success:
        await callback.message.edit_text(
            admin_screen(
                _quote_after_title(
                    texts.t(
                        'ADMIN_REMNAWAVE_UI_FINISH_SQUAD_CREATION_169',
                        '<b>Сквад успешно создан!</b>\n\n<b>Название:</b> {value1}\n<b>Количество инбаундов:</b> {value2}\n\nСквад готов к использованию!',
                    ).format(value1=html.escape(str(squad_name)), value2=len(selected_inbounds))
                ),
                icon='success',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_button(
                            texts.t('ADMIN_REMNAWAVE_UI_FINISH_SQUAD_CREATION_170', 'Список сквадов'),
                            callback_data='admin_rw_squads',
                            icon='servers',
                        )
                    ],
                    [admin_back_button(texts, 'admin_remnawave')],
                ]
            ),
            parse_mode='HTML',
        )
        await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_FINISH_SQUAD_CREATION_172', 'Сквад создан!'))
    else:
        await callback.message.edit_text(
            admin_screen(
                _quote_after_title(
                    texts.t(
                        'ADMIN_REMNAWAVE_UI_FINISH_SQUAD_CREATION_173',
                        '<b>Ошибка создания сквада</b>\n\n<b>Название:</b> {value1}\n\nВозможные причины:\n• Сквад с таким названием уже существует\n• Проблемы с подключением к API\n• Недостаточно прав\n• Некорректные инбаунды',
                    ).format(value1=html.escape(str(squad_name)))
                ),
                icon='error',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_button(
                            'Попробовать снова',
                            callback_data='admin_squad_create',
                            icon='plus',
                        )
                    ],
                    [
                        admin_back_button(
                            texts,
                            'admin_rw_squads',
                            label=texts.t('ADMIN_REMNAWAVE_UI_FINISH_SQUAD_CREATION_170', 'Список сквадов'),
                        )
                    ],
                ]
            ),
            parse_mode='HTML',
        )
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_FINISH_SQUAD_CREATION_176', 'Ошибка создания сквада'), show_alert=True
        )


@admin_required
@error_handler
async def cancel_squad_creation(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    user_id = callback.from_user.id

    squad_create_data.pop(user_id, None)
    await state.clear()

    await show_squads_management(callback, db_user, db)


@admin_required
@error_handler
async def restart_all_nodes(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    remnawave_service = RemnaWaveService()
    success = await remnawave_service.restart_all_nodes()

    if success:
        await callback.message.edit_text(
            admin_screen(
                texts.t('ADMIN_REMNAWAVE_UI_RESTART_ALL_NODES_177', 'Команда перезагрузки всех нод отправлена'),
                icon='success',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            'admin_rw_nodes',
                            label=texts.t('ADMIN_REMNAWAVE_UI_SHOW_NODES_MANAGEMENT_052', 'Управление нодами'),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )
    else:
        await callback.message.edit_text(
            admin_screen(
                texts.t('ADMIN_REMNAWAVE_UI_RESTART_ALL_NODES_179', 'Ошибка перезагрузки нод'),
                icon='error',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_back_button(
                            texts,
                            'admin_rw_nodes',
                            label=texts.t('ADMIN_REMNAWAVE_UI_SHOW_NODES_MANAGEMENT_052', 'Управление нодами'),
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )

    await callback.answer()


@admin_required
@error_handler
async def show_sync_options(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    status = remnawave_sync_service.get_status()
    times_text = ', '.join(t.strftime('%H:%M') for t in status.times) if status.times else '—'
    next_run_text = format_datetime(status.next_run) if status.next_run else '—'
    last_result = '—'

    if status.last_run_finished_at:
        result_icon = admin_icon('success') if status.last_run_success else admin_icon('error')
        result_label = (
            texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_009', 'успешно')
            if status.last_run_success
            else texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_010', 'с ошибками')
        )
        finished_text = format_datetime(status.last_run_finished_at)
        last_result = f'{result_icon} {result_label} ({finished_text})'
    elif status.last_run_started_at:
        last_result = texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_183', 'Запущено {value1}').format(
            value1=format_datetime(status.last_run_started_at)
        )

    status_lines = [
        texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_184', 'Статус: {value1}').format(
            value1=texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_020', 'Включена')
            if status.enabled
            else texts.t('ADMIN_REMNAWAVE_UI_BUILD_AUTO_SYNC_VIEW_021', 'Отключена')
        ),
        texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_187', 'Расписание: {value1}').format(value1=times_text),
        texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_188', 'Следующий запуск: {value1}').format(
            value1=next_run_text if status.enabled else '—'
        ),
        texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_189', 'Последний запуск: {value1}').format(value1=last_result),
    ]

    status_block = '\n'.join(
        [f'{admin_icon("settings")} {line}' if index == 0 else f'• {line}' for index, line in enumerate(status_lines)]
    )

    instructions = texts.t(
        'ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_190',
        '<b>Полная синхронизация выполняет:</b>\n• Создание новых пользователей из панели в боте\n• Обновление данных существующих пользователей\n• Деактивация подписок пользователей, отсутствующих в панели\n• Сохранение балансов пользователей\n• Время выполнения: 2-5 минут\n\n<b>Важно:</b>\n• Во время синхронизации не выполняйте другие операции\n• При полной синхронизации подписки пользователей, отсутствующих в панели, будут деактивированы\n• Рекомендуется делать полную синхронизацию ежедневно\n• Баланс пользователей НЕ удаляется\n\n<b>Обратная синхронизация:</b>\n• Отправляет активных пользователей из бота в панель\n• Используйте при сбоях панели или для восстановления данных',
    )
    if instructions.lstrip().startswith('<b>Синхронизация с Remnawave</b>'):
        instructions = instructions.split('\n\n', 1)[-1]

    text = '\n\n'.join(
        [
            admin_heading('sync', 'Синхронизация с Remnawave'),
            admin_quote(status_block),
            admin_quote(instructions),
        ]
    )

    keyboard = [
        [
            admin_button(
                _clean_legacy(texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_191', 'Запустить полную синхронизацию')),
                callback_data='sync_all_users',
                icon='refresh',
            )
        ],
        [
            admin_button(
                texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_192', 'Синхронизация в панель'),
                callback_data='sync_to_panel',
                icon='upload',
            )
        ],
        [
            admin_button(
                texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_193', 'Настройки автосинхронизации'),
                callback_data='admin_rw_auto_sync',
                icon='settings',
            )
        ],
        [admin_back_button(texts, 'admin_remnawave')],
    ]

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='sync', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_auto_sync_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    status = remnawave_sync_service.get_status()
    text, keyboard = _build_auto_sync_view(status, language=db_user.language)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_auto_sync_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await state.clear()
    new_value = not bool(settings.REMNAWAVE_AUTO_SYNC_ENABLED)
    await bot_configuration_service.set_value(
        db,
        'REMNAWAVE_AUTO_SYNC_ENABLED',
        new_value,
    )
    await db.commit()

    status = remnawave_sync_service.get_status()
    text, keyboard = _build_auto_sync_view(status, language=db_user.language)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer(
        texts.t('ADMIN_REMNAWAVE_UI_TOGGLE_AUTO_SYNC_SETTING_195', 'Автосинхронизация {value1}').format(
            value1=texts.t('ADMIN_REMNAWAVE_UI_MANAGE_NODE_069', 'включена')
            if new_value
            else texts.t('ADMIN_REMNAWAVE_UI_MANAGE_NODE_070', 'отключена')
        )
    )


@admin_required
@error_handler
async def prompt_auto_sync_schedule(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    status = remnawave_sync_service.get_status()
    current_schedule = ', '.join(t.strftime('%H:%M') for t in status.times) if status.times else '—'

    instructions = texts.t(
        'ADMIN_REMNAWAVE_UI_PROMPT_AUTO_SYNC_SCHEDULE_198',
        '<b>Настройка расписания автосинхронизации</b>\n\nУкажите время запуска через запятую или с новой строки в формате HH:MM.\nТекущее расписание: <code>{value1}</code>\n\nПримеры: <code>03:00, 15:30</code> или <code>00:15\n06:00\n18:45</code>\n\nОтправьте <b>отмена</b>, чтобы вернуться без изменений.',
    ).format(value1=current_schedule)

    await state.set_state(RemnaWaveSyncStates.waiting_for_schedule)
    await state.update_data(
        auto_sync_message_id=callback.message.message_id,
        auto_sync_message_chat_id=callback.message.chat.id,
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(instructions), icon='clock', compact=True),
        parse_mode='HTML',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_back_button(
                        texts,
                        'remnawave_auto_sync_cancel',
                        label=texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_RENAME_FORM_124', 'Отмена'),
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def cancel_auto_sync_schedule(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await state.clear()
    status = remnawave_sync_service.get_status()
    text, keyboard = _build_auto_sync_view(status, language=db_user.language)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_CANCEL_AUTO_SYNC_SCHEDULE_200', 'Изменение расписания отменено'))


@admin_required
@error_handler
async def run_auto_sync_now(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    if remnawave_sync_service.get_status().is_running:
        await callback.answer(
            texts.t('ADMIN_REMNAWAVE_UI_RUN_AUTO_SYNC_NOW_201', 'Синхронизация уже выполняется'), show_alert=True
        )
        return

    await state.clear()
    await callback.message.edit_text(
        admin_screen(
            texts.t(
                'ADMIN_REMNAWAVE_UI_RUN_AUTO_SYNC_NOW_202',
                'Запуск автосинхронизации...\n\nПодождите, это может занять несколько минут.',
            ),
            icon='sync',
            compact=True,
        ),
        parse_mode='HTML',
    )
    await callback.answer(texts.t('ADMIN_REMNAWAVE_UI_RUN_AUTO_SYNC_NOW_203', 'Автосинхронизация запущена'))

    result = await remnawave_sync_service.run_sync_now(reason='manual')
    status = remnawave_sync_service.get_status()
    base_text, keyboard = _build_auto_sync_view(status, language=db_user.language)

    if not result.get('started'):
        await callback.message.edit_text(
            admin_screen(
                texts.t('ADMIN_REMNAWAVE_UI_RUN_AUTO_SYNC_NOW_204', '<b>Синхронизация уже выполняется</b>'),
                icon='warning',
                compact=True,
            )
            + '\n\n'
            + base_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
        return

    if result.get('success'):
        user_stats = result.get('user_stats') or {}
        server_stats = result.get('server_stats') or {}
        summary = texts.t(
            'ADMIN_REMNAWAVE_UI_RUN_AUTO_SYNC_NOW_205',
            '<b>Синхронизация завершена</b>\n• Пользователи: создано {value1}, обновлено {value2}, деактивировано {value3}, ошибок {value4}\n• Серверы: создано {value5}, обновлено {value6}, удалено {value7}',
        ).format(
            value1=user_stats.get('created', 0),
            value2=user_stats.get('updated', 0),
            value3=user_stats.get('deleted', user_stats.get('deactivated', 0)),
            value4=user_stats.get('errors', 0),
            value5=server_stats.get('created', 0),
            value6=server_stats.get('updated', 0),
            value7=server_stats.get('removed', 0),
        )
        final_text = admin_screen(_clean_legacy(summary), icon='success', compact=True) + '\n\n' + base_text
        await callback.message.edit_text(
            final_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
    else:
        error_text = result.get('error') or texts.t('ADMIN_REMNAWAVE_UI_RUN_AUTO_SYNC_NOW_206', 'Неизвестная ошибка')
        summary = texts.t(
            'ADMIN_REMNAWAVE_UI_RUN_AUTO_SYNC_NOW_207',
            '<b>Синхронизация завершилась с ошибкой</b>\nПричина: {value1}',
        ).format(value1=html.escape(str(error_text)))
        await callback.message.edit_text(
            admin_screen(_clean_legacy(summary), icon='error', compact=True) + '\n\n' + base_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def save_auto_sync_schedule(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    text = (message.text or '').strip()
    data = await state.get_data()

    if text.lower() in {'отмена', 'cancel'}:
        await state.clear()
        status = remnawave_sync_service.get_status()
        view_text, keyboard = _build_auto_sync_view(status, language=db_user.language)
        message_id = data.get('auto_sync_message_id')
        chat_id = data.get('auto_sync_message_chat_id', message.chat.id)
        if message_id:
            await message.bot.edit_message_text(
                view_text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboard,
                parse_mode='HTML',
            )
        else:
            await message.answer(
                view_text,
                reply_markup=keyboard,
                parse_mode='HTML',
            )
        await message.answer(texts.t('ADMIN_REMNAWAVE_UI_SAVE_AUTO_SYNC_SCHEDULE_209', 'Настройка расписания отменена'))
        return

    parsed_times = settings.parse_daily_time_list(text)

    if not parsed_times:
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_SAVE_AUTO_SYNC_SCHEDULE_210',
                    'Не удалось распознать время. Используйте формат HH:MM, например 03:00 или 18:45.',
                ),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    normalized_value = ', '.join(t.strftime('%H:%M') for t in parsed_times)
    await bot_configuration_service.set_value(
        db,
        'REMNAWAVE_AUTO_SYNC_TIMES',
        normalized_value,
    )
    await db.commit()

    status = remnawave_sync_service.get_status()
    view_text, keyboard = _build_auto_sync_view(status, language=db_user.language)
    message_id = data.get('auto_sync_message_id')
    chat_id = data.get('auto_sync_message_chat_id', message.chat.id)

    if message_id:
        await message.bot.edit_message_text(
            view_text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
    else:
        await message.answer(
            view_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )

    await state.clear()
    await message.answer(
        admin_screen(
            texts.t('ADMIN_REMNAWAVE_UI_SAVE_AUTO_SYNC_SCHEDULE_211', 'Расписание автосинхронизации обновлено'),
            icon='success',
            compact=True,
        ),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def sync_all_users(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Выполняет полную синхронизацию всех пользователей"""

    # Проход идёт десятки минут; повторное нажатие не должно запускать второй
    # проход параллельно первому (двойная нагрузка на панель и её лимит частоты).
    texts = get_texts(db_user.language)
    if is_full_sync_running():
        await callback.answer(
            texts.t(
                'ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_212',
                'Полная синхронизация уже выполняется — дождитесь её окончания',
            ),
            show_alert=True,
        )
        return

    progress_text = """<b>Выполняется полная синхронизация...</b>

Этапы:
• Из панели в бота: загрузка ВСЕХ пользователей панели, создание и обновление,
  деактивация подписок отсутствующих (балансы сохраняются)
• Из бота в панель: статусы, даты, сквады и теги тарифов всех подписок
• Серверы: сквады панели → серверы бота

Пожалуйста, подождите..."""

    await callback.message.edit_text(
        admin_screen(_clean_legacy(progress_text), icon='sync', compact=True),
        reply_markup=None,
        parse_mode='HTML',
    )

    remnawave_service = RemnaWaveService()
    # Одна функция на бота, кабинет и расписание: из панели в бота → серверы.
    try:
        stats, server_stats = await perform_full_sync(db, remnawave_service)
    except FullSyncAlreadyRunning:
        await callback.message.edit_text(
            admin_screen(
                texts.t(
                    'ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_214',
                    '<b>Полная синхронизация уже выполняется</b>\n\nДождитесь её окончания и запустите снова.',
                ),
                icon='warning',
                compact=True,
            ),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[admin_back_button(texts, 'admin_remnawave')]]),
            parse_mode='HTML',
        )
        await callback.answer()
        return
    errors_total = stats['errors']
    total_operations = stats['created'] + stats['updated'] + stats.get('deleted', 0)

    if errors_total == 0:
        status_text = texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_216', 'успешно завершена')
    elif errors_total < total_operations:
        status_text = texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_217', 'завершена с предупреждениями')
    else:
        status_text = 'завершена с ошибками'

    panel_block = '\n'.join(
        [
            f'{admin_icon("users")} <b>Из панели в бота</b>',
            f'• Создано: {stats["created"]}',
            f'• Обновлено: {stats["updated"]}',
            f'• Деактивировано: {stats.get("deleted", 0)}',
            f'• Ошибок: {stats["errors"]}',
        ]
    )

    parts = [
        admin_heading('sync', f'Полная синхронизация {status_text}'),
        admin_quote(panel_block),
        admin_quote(
            f'{admin_icon("servers")} <b>Серверы</b>\n'
            f'• Создано: {server_stats.get("created", 0)}\n'
            f'• Обновлено: {server_stats.get("updated", 0)}\n'
            f'• Удалено: {server_stats.get("removed", 0)} из {server_stats.get("total", 0)}'
        ),
    ]

    if stats.get('deleted', 0) > 0:
        parts.append(
            admin_quote(
                f'{admin_icon("warning")} <b>Деактивированные подписки</b>\n'
                'Деактивированы подписки пользователей, которые отсутствуют в панели Remnawave.\n'
                f'{admin_icon("info")} Балансы пользователей сохранены.'
            )
        )

    if errors_total > 0:
        parts.append(
            f'{admin_icon("warning")} <b>Внимание:</b> некоторые операции завершились с ошибками.\n'
            'Проверьте логи для получения подробной информации.'
        )

    parts.append(
        admin_quote(
            f'{admin_icon("info")} <b>Как это работает</b>\n'
            '• Панель — источник истины: бот забрал из неё сроки, статусы и лимиты\n'
            '• В панель бот пишет только при покупке, продлении и действиях админа\n'
            '• По расписанию выполняется эта же синхронизация'
        )
    )
    text = '\n\n'.join(parts)

    keyboard = []

    if errors_total > 0:
        keyboard.append(
            [
                admin_button(
                    _clean_legacy(texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_223', 'Повторить синхронизацию')),
                    callback_data='sync_all_users',
                    icon='refresh',
                )
            ]
        )

    keyboard.extend(
        [
            [
                admin_button(
                    texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_224', 'Статистика системы'),
                    callback_data='admin_rw_system',
                    icon='chart',
                ),
                admin_button(
                    texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_225', 'Ноды'),
                    callback_data='admin_rw_nodes',
                    icon='servers',
                ),
            ],
            [admin_back_button(texts, 'admin_remnawave')],
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='sync', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def sync_users_to_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    await callback.message.edit_text(
        admin_screen(
            admin_heading('sync', 'Синхронизация в панель')
            + '\n\n'
            + admin_quote(
                f'{admin_icon("info")} Выполняется синхронизация данных бота в панель Remnawave.\n'
                'Это может занять несколько минут.'
            ),
            compact=True,
        ),
        parse_mode='HTML',
        reply_markup=None,
    )

    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.sync_users_to_panel(db)

    if stats['errors'] == 0:
        status_text = texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_216', 'успешно завершена')
    elif (stats['created'] + stats['updated']) > 0:
        status_text = texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_217', 'завершена с предупреждениями')
    else:
        status_text = texts.t('ADMIN_REMNAWAVE_UI_VALIDATE_SUBSCRIPTIONS_247', 'завершена с ошибками')

    text = '\n\n'.join(
        [
            admin_heading('sync', f'Синхронизация в панель: {status_text}'),
            admin_quote(
                '\n'.join(
                    [
                        f'{admin_icon("stats")} <b>Результаты</b>',
                        f'• Создано: {stats["created"]}',
                        f'• Обновлено: {stats["updated"]}',
                        f'• Ошибок: {stats["errors"]}',
                    ]
                )
            ),
        ]
    )

    keyboard = [
        [
            admin_button(
                'Повторить',
                callback_data='sync_to_panel',
                icon='refresh',
            )
        ],
        [
            admin_button(
                _clean_legacy(texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_191', 'Запустить полную синхронизацию')),
                callback_data='sync_all_users',
                icon='refresh',
            )
        ],
        [
            admin_back_button(
                texts,
                'admin_rw_sync',
                label='К синхронизации',
            )
        ],
    ]

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='sync', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_sync_recommendations(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    await callback.message.edit_text(
        admin_screen(
            texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_RECOMMENDATIONS_235', 'Анализируем состояние синхронизации...'),
            icon='sync',
            compact=True,
        ),
        reply_markup=None,
        parse_mode='HTML',
    )

    remnawave_service = RemnaWaveService()
    recommendations = await remnawave_service.get_sync_recommendations(db)

    priority_icons = {
        'low': admin_icon('battery'),
        'medium': admin_icon('warning'),
        'high': admin_icon('fire'),
    }

    if recommendations['sync_type'] == 'all':
        action_text = texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_RECOMMENDATIONS_237', 'Полная синхронизация')
    elif recommendations['sync_type'] == 'update_only':
        action_text = texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_RECOMMENDATIONS_238', 'Обновление данных')
    elif recommendations['sync_type'] == 'new_only':
        action_text = texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_RECOMMENDATIONS_239', 'Синхронизация новых')
    else:
        action_text = texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_RECOMMENDATIONS_240', 'Синхронизация не требуется')

    reasons_block = f'{admin_icon("question")} <b>Причины</b>\n'
    for reason in recommendations['reasons']:
        reasons_block += f'• {html.escape(str(_localize_panel_value(texts, reason)))}\n'

    text = '\n\n'.join(
        [
            admin_heading('chart', 'Рекомендации по синхронизации'),
            admin_quote(
                '\n'.join(
                    [
                        (
                            f'{priority_icons.get(recommendations["priority"], admin_icon("info"))} '
                            f'<b>Приоритет:</b> {recommendations["priority"].upper()}'
                        ),
                        f'{admin_icon("clock")} <b>Время выполнения:</b> {recommendations["estimated_time"]}',
                        f'{admin_icon("refresh")} <b>Рекомендуемое действие:</b> {action_text}',
                    ]
                )
            ),
            admin_quote(reasons_block.strip()),
        ]
    )

    keyboard = []

    if recommendations['should_sync'] and recommendations['sync_type'] != 'none':
        keyboard.append(
            [
                admin_button(
                    texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_RECOMMENDATIONS_242', 'Выполнить рекомендацию'),
                    callback_data=f'sync_{recommendations["sync_type"]}_users'
                    if recommendations['sync_type'] != 'update_only'
                    else 'sync_update_data',
                    icon='refresh',
                )
            ]
        )

    keyboard.extend(
        [
            [
                admin_button(
                    'Другие опции',
                    callback_data='admin_rw_sync',
                    icon='settings',
                )
            ],
            [admin_back_button(texts, 'admin_remnawave')],
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='chart', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def validate_subscriptions(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    await callback.message.edit_text(
        admin_screen(
            texts.t(
                'ADMIN_REMNAWAVE_UI_VALIDATE_SUBSCRIPTIONS_245',
                'Выполняется валидация подписок...\n\nПроверяем данные, может занять несколько минут.',
            ),
            icon='flask',
            compact=True,
        ),
        reply_markup=None,
        parse_mode='HTML',
    )

    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.validate_and_fix_subscriptions(db)

    if stats['errors'] == 0:
        status_text = texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_216', 'успешно завершена')
    else:
        status_text = 'завершена с ошибками'

    results_block = '\n'.join(
        [
            f'{admin_icon("stats")} <b>Результаты</b>',
            f'• Проверено подписок: {stats["checked"]}',
            f'• Исправлено подписок: {stats["fixed"]}',
            f'• Найдено проблем: {stats["issues_found"]}',
            f'• Ошибок: {stats["errors"]}',
        ]
    )
    parts = [
        admin_heading('flask', f'Валидация {status_text}'),
        admin_quote(results_block),
    ]

    if stats['fixed'] > 0:
        fixed_block = f'{admin_icon("tools")} <b>Исправленные проблемы</b>\n'
        fixed_block += texts.t('ADMIN_REMNAWAVE_UI_VALIDATE_SUBSCRIPTIONS_250', '• Статусы просроченных подписок\n')
        fixed_block += texts.t('ADMIN_REMNAWAVE_UI_VALIDATE_SUBSCRIPTIONS_251', '• Отсутствующие данные Remnawave\n')
        fixed_block += texts.t('ADMIN_REMNAWAVE_UI_VALIDATE_SUBSCRIPTIONS_252', '• Некорректные лимиты трафика\n')
        fixed_block += texts.t('ADMIN_REMNAWAVE_UI_VALIDATE_SUBSCRIPTIONS_253', '• Настройки устройств\n')
        parts.append(admin_quote(fixed_block.strip()))

    if stats['errors'] > 0:
        parts.append(
            f'{admin_icon("warning")} '
            + texts.t(
                'ADMIN_REMNAWAVE_UI_VALIDATE_SUBSCRIPTIONS_254',
                'Обнаружены ошибки при обработке.\nПроверьте логи для подробной информации.',
            ).strip()
        )

    text = '\n\n'.join(parts)

    keyboard = [
        [
            admin_button(
                'Повторить валидацию',
                callback_data='sync_validate',
                icon='refresh',
            )
        ],
        [
            admin_button(
                _clean_legacy(texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_191', 'Запустить полную синхронизацию')),
                callback_data='sync_all_users',
                icon='refresh',
            )
        ],
        [admin_back_button(texts, 'admin_rw_sync', label='К синхронизации')],
    ]

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='flask', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def cleanup_subscriptions(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    await callback.message.edit_text(
        admin_screen(
            texts.t(
                'ADMIN_REMNAWAVE_UI_CLEANUP_SUBSCRIPTIONS_258',
                'Выполняется очистка неактуальных подписок...\n\nУдаляем подписки пользователей, отсутствующих в панели.',
            ),
            icon='broom',
            compact=True,
        ),
        reply_markup=None,
        parse_mode='HTML',
    )

    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.cleanup_orphaned_subscriptions(db)

    if stats['errors'] == 0:
        status_text = texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_216', 'успешно завершена')
    else:
        status_text = 'завершена с ошибками'

    results_block = '\n'.join(
        [
            f'{admin_icon("stats")} <b>Результаты</b>',
            f'• Проверено подписок: {stats["checked"]}',
            f'• Деактивировано: {stats["deactivated"]}',
            f'• Ошибок: {stats["errors"]}',
        ]
    )
    parts = [
        admin_heading('broom', f'Очистка {status_text}'),
        admin_quote(results_block),
    ]

    if stats['deactivated'] > 0:
        parts.append(
            admin_quote(
                f'{admin_icon("warning")} <b>Деактивированные подписки</b>\n'
                + texts.t('ADMIN_REMNAWAVE_UI_CLEANUP_SUBSCRIPTIONS_263', 'Отключены подписки пользователей, которые\n')
                + texts.t('ADMIN_REMNAWAVE_UI_CLEANUP_SUBSCRIPTIONS_264', 'отсутствуют в панели Remnawave.')
            )
        )
    else:
        parts.append(
            f'{admin_icon("success")} '
            + texts.t(
                'ADMIN_REMNAWAVE_UI_CLEANUP_SUBSCRIPTIONS_265',
                'Все подписки актуальны!\nНеактуальных подписок не найдено.',
            ).strip()
        )

    if stats['errors'] > 0:
        parts.append(
            f'{admin_icon("warning")} '
            + texts.t(
                'ADMIN_REMNAWAVE_UI_VALIDATE_SUBSCRIPTIONS_254',
                'Обнаружены ошибки при обработке.\nПроверьте логи для подробной информации.',
            ).strip()
        )

    text = '\n\n'.join(parts)

    keyboard = [
        [
            admin_button(
                'Повторить очистку',
                callback_data='sync_cleanup',
                icon='broom',
            )
        ],
        [
            admin_button(
                'Валидация',
                callback_data='sync_validate',
                icon='flask',
            )
        ],
        [admin_back_button(texts, 'admin_rw_sync', label='К синхронизации')],
    ]

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='broom', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def force_cleanup_all_orphaned_users(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    await callback.message.edit_text(
        admin_screen(
            texts.t(
                'ADMIN_REMNAWAVE_UI_FORCE_CLEANUP_ALL_ORPHANED_USERS_270',
                'Выполняется принудительная очистка всех пользователей, отсутствующих в панели...\n\nВНИМАНИЕ: Это полностью удалит ВСЕ данные пользователей!\nВключая: транзакции, реферальные доходы, промокоды, серверы, балансы\n\nПожалуйста, подождите...',
            ),
            icon='warning',
            compact=True,
        ),
        reply_markup=None,
        parse_mode='HTML',
    )

    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.cleanup_orphaned_subscriptions(db)

    if stats['errors'] == 0:
        status_text = texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_216', 'успешно завершена')
    else:
        status_text = 'завершена с ошибками'

    results_block = '\n'.join(
        [
            f'{admin_icon("stats")} <b>Результаты</b>',
            f'• Проверено подписок: {stats["checked"]}',
            f'• Полностью очищено: {stats["deactivated"]}',
            f'• Ошибок: {stats["errors"]}',
        ]
    )
    parts = [
        admin_heading('warning', f'Принудительная очистка {status_text}'),
        admin_quote(results_block),
    ]

    if stats['deactivated'] > 0:
        parts.append(
            admin_quote(
                f'{admin_icon("delete")} <b>Полностью очищенные данные</b>\n'
                '• Подписки сброшены к начальному состоянию\n'
                '• Удалены ВСЕ транзакции пользователей\n'
                '• Удалены ВСЕ реферальные доходы\n'
                '• Удалены использования промокодов\n'
                '• Сброшены балансы к нулю\n'
                '• Удалены подключенные серверы\n'
                '• Сброшены HWID устройства в Remnawave\n'
                '• Очищены Remnawave UUID'
            )
        )
    else:
        parts.append(
            f'{admin_icon("success")} '
            + texts.t(
                'ADMIN_REMNAWAVE_UI_FORCE_CLEANUP_ALL_ORPHANED_USERS_275',
                'Неактуальных подписок не найдено!\nВсе пользователи синхронизированы с панелью.',
            ).strip()
        )

    if stats['errors'] > 0:
        parts.append(
            f'{admin_icon("warning")} '
            + texts.t(
                'ADMIN_REMNAWAVE_UI_VALIDATE_SUBSCRIPTIONS_254',
                'Обнаружены ошибки при обработке.\nПроверьте логи для подробной информации.',
            ).strip()
        )

    text = '\n\n'.join(parts)

    keyboard = [
        [
            admin_button(
                'Повторить очистку',
                callback_data='force_cleanup_orphaned',
                icon='broom',
            )
        ],
        [
            admin_button(
                _clean_legacy(texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_191', 'Запустить полную синхронизацию')),
                callback_data='sync_all_users',
                icon='refresh',
            )
        ],
        [admin_back_button(texts, 'admin_rw_sync', label='К синхронизации')],
    ]

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='warning', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_force_cleanup(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    text = '\n\n'.join(
        [
            admin_heading('prohibit', 'ВНИМАНИЕ! ОПАСНАЯ ОПЕРАЦИЯ!'),
            admin_quote(
                f'{admin_icon("delete")} <b>Принудительная очистка полностью удалит</b>\n'
                '• ВСЕ транзакции пользователей отсутствующих в панели\n'
                '• ВСЕ реферальные доходы и связи\n'
                '• ВСЕ использования промокодов\n'
                '• ВСЕ подключенные серверы подписок\n'
                '• ВСЕ балансы (сброс к нулю)\n'
                '• ВСЕ HWID устройства в Remnawave\n'
                '• ВСЕ Remnawave UUID и ссылки'
            ),
            f'{admin_icon("warning")} <b>Это действие НЕОБРАТИМО!</b>',
            admin_quote(
                f'{admin_icon("info")} <b>Используйте только если</b>\n'
                '• Обычная синхронизация не помогает\n'
                '• Нужно полностью очистить «мусорные» данные\n'
                '• После массового удаления пользователей из панели'
            ),
            f'{admin_icon("question")} <b>Вы действительно хотите продолжить?</b>',
        ]
    )

    keyboard = [
        [
            admin_button(
                texts.t('ADMIN_REMNAWAVE_UI_CONFIRM_FORCE_CLEANUP_281', 'ДА, ОЧИСТИТЬ ВСЕ'),
                callback_data='force_cleanup_orphaned',
                icon='delete',
            )
        ],
        [
            admin_back_button(
                texts,
                'admin_rw_sync',
                label=texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUAD_RENAME_FORM_124', 'Отмена'),
            )
        ],
    ]

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='warning', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def sync_users(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    sync_type = callback.data.split('_')[-2] + '_' + callback.data.split('_')[-1]

    progress_text = texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_283', 'Выполняется синхронизация...\n\n')

    if sync_type == 'all_users':
        progress_text += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_284', 'Тип: Полная синхронизация\n')
        progress_text += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_285', '• Создание новых пользователей\n')
        progress_text += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_286', '• Обновление существующих\n')
        progress_text += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_287', '• Удаление неактуальных подписок\n')
    elif sync_type == 'new_users':
        progress_text += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_288', 'Тип: Только новые пользователи\n')
        progress_text += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_289', '• Создание пользователей из панели\n')
    elif sync_type == 'update_data':
        progress_text += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_290', 'Тип: Обновление данных\n')
        progress_text += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_291', '• Обновление информации о трафике\n')
        progress_text += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_292', '• Синхронизация подписок\n')

    progress_text += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_293', '\nПожалуйста, подождите...')

    await callback.message.edit_text(
        admin_screen(_clean_legacy(progress_text), icon='sync', compact=True),
        reply_markup=None,
        parse_mode='HTML',
    )

    remnawave_service = RemnaWaveService()

    sync_map = {'all_users': 'all', 'new_users': 'new_only', 'update_data': 'update_only'}

    stats = await remnawave_service.sync_users_from_panel(db, sync_map.get(sync_type, 'all'))

    total_operations = stats['created'] + stats['updated'] + stats.get('deleted', 0)

    if stats['errors'] == 0:
        status_text = texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_216', 'успешно завершена')
    elif stats['errors'] < total_operations:
        status_text = texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_217', 'завершена с предупреждениями')
    else:
        status_text = texts.t('ADMIN_REMNAWAVE_UI_VALIDATE_SUBSCRIPTIONS_247', 'завершена с ошибками')

    text = admin_heading('sync', f'Синхронизация {status_text}')
    result_lines = [f'{admin_icon("stats")} <b>Результат</b>']

    if sync_type == 'all_users':
        result_lines.append(
            texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_298', '• Создано: {value1}\n')
            .format(value1=stats['created'])
            .strip()
        )
        result_lines.append(
            texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_299', '• Обновлено: {value1}\n')
            .format(value1=stats['updated'])
            .strip()
        )
        if 'deleted' in stats:
            result_lines.append(
                texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_300', '• Удалено: {value1}\n')
                .format(value1=stats['deleted'])
                .strip()
            )
        result_lines.append(
            texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_301', '• Ошибок: {value1}\n').format(value1=stats['errors']).strip()
        )
    elif sync_type == 'new_users':
        result_lines.append(
            texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_298', '• Создано: {value1}\n')
            .format(value1=stats['created'])
            .strip()
        )
        result_lines.append(
            texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_301', '• Ошибок: {value1}\n').format(value1=stats['errors']).strip()
        )
        if stats['created'] == 0 and stats['errors'] == 0:
            result_lines.append(
                f'{admin_icon("info")} '
                + texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_304', 'Новых пользователей не найдено').strip()
            )
    elif sync_type == 'update_data':
        result_lines.append(
            texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_299', '• Обновлено: {value1}\n')
            .format(value1=stats['updated'])
            .strip()
        )
        result_lines.append(
            texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_301', '• Ошибок: {value1}\n').format(value1=stats['errors']).strip()
        )
        if stats['updated'] == 0 and stats['errors'] == 0:
            result_lines.append(
                f'{admin_icon("info")} ' + texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_307', 'Все данные актуальны').strip()
            )

    text += '\n\n' + admin_quote('\n'.join(result_lines))

    if stats['errors'] > 0:
        text += '\n\n' + (
            f'{admin_icon("warning")} '
            + texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_308', 'Внимание:').strip()
            + '\n'
            + texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_309', 'Некоторые операции завершились с ошибками.').strip()
            + '\n'
            + texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_310', 'Проверьте логи для получения подробной информации.').strip()
        )

    if sync_type == 'all_users' and 'deleted' in stats and stats['deleted'] > 0:
        text += '\n\n' + admin_quote(
            f'{admin_icon("warning")} <b>Удаленные подписки</b>\n'
            + texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_312', 'Деактивированы подписки пользователей,\n').strip()
            + '\n'
            + texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_313', 'которые отсутствуют в панели Remnawave.').strip()
        )

    recommendations_block = f'{admin_icon("question")} <b>Рекомендации</b>\n'
    if sync_type == 'all_users':
        recommendations_block += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_315', '• Полная синхронизация выполнена\n')
        recommendations_block += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_316', '• Рекомендуется запускать раз в день\n')
    elif sync_type == 'new_users':
        recommendations_block += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_317', '• Синхронизация новых пользователей\n')
        recommendations_block += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_318', '• Используйте при массовом добавлении\n')
    elif sync_type == 'update_data':
        recommendations_block += texts.t('ADMIN_REMNAWAVE_UI_SYNC_USERS_319', '• Обновление данных о трафике\n')
        recommendations_block += texts.t(
            'ADMIN_REMNAWAVE_UI_SYNC_USERS_320', '• Запускайте для актуализации статистики\n'
        )

    text += '\n\n' + admin_quote(recommendations_block.strip())

    keyboard = []

    if stats['errors'] > 0:
        keyboard.append(
            [
                admin_button(
                    _clean_legacy(texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_223', 'Повторить синхронизацию')),
                    callback_data=callback.data,
                    icon='refresh',
                )
            ]
        )

    if sync_type != 'all_users':
        keyboard.append(
            [
                admin_button(
                    _clean_legacy(
                        texts.t('ADMIN_REMNAWAVE_UI_SHOW_SYNC_OPTIONS_191', 'Запустить полную синхронизацию')
                    ),
                    callback_data='sync_all_users',
                    icon='refresh',
                )
            ]
        )

    keyboard.extend(
        [
            [
                admin_button(
                    texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_224', 'Статистика системы'),
                    callback_data='admin_rw_system',
                    icon='chart',
                ),
                admin_button(
                    texts.t('ADMIN_REMNAWAVE_UI_SYNC_ALL_USERS_225', 'Ноды'),
                    callback_data='admin_rw_nodes',
                    icon='servers',
                ),
            ],
            [admin_back_button(texts, 'admin_remnawave')],
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='sync', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_squads_management(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()

    text = admin_heading('servers', 'Управление сквадами')
    keyboard = []
    squad_chunks = []

    if squads:
        for squad in squads:
            safe_name = html.escape(str(squad['name']))
            squad_chunks.append(
                '\n'.join(
                    [
                        f'{admin_icon("servers")} <b>{safe_name}</b>',
                        texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUADS_MANAGEMENT_327', '• Участников: {value1}').format(
                            value1=squad['members_count']
                        ),
                        texts.t('ADMIN_REMNAWAVE_UI_SHOW_SQUADS_MANAGEMENT_328', '• Инбаундов: {value1}').format(
                            value1=squad['inbounds_count']
                        ),
                    ]
                )
            )

            keyboard.append(
                [
                    admin_button(
                        str(squad['name']),
                        callback_data=f'admin_squad_manage_{squad["uuid"]}',
                        icon='edit',
                    )
                ]
            )

        text += '\n\n' + '\n\n'.join(admin_quote(chunk) for chunk in squad_chunks)
    else:
        text += f'\n\n{admin_icon("warning")} ' + texts.t(
            'ADMIN_REMNAWAVE_UI_SHOW_SQUADS_MANAGEMENT_329', 'Сквады не найдены'
        )

    keyboard.extend(
        [
            [
                admin_button(
                    texts.t('ADMIN_REMNAWAVE_UI_PROCESS_SQUAD_NAME_158', 'Создать сквад'),
                    callback_data='admin_squad_create',
                    icon='plus',
                )
            ],
            [admin_back_button(texts, 'admin_remnawave')],
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='servers', compact=True),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_remnawave_menu, F.data == 'admin_remnawave')
    dp.callback_query.register(show_system_stats, F.data == 'admin_rw_system')
    dp.callback_query.register(show_traffic_stats, F.data == 'admin_rw_traffic')
    dp.callback_query.register(show_nodes_management, F.data == 'admin_rw_nodes')
    dp.callback_query.register(show_node_details, F.data.startswith('admin_node_manage_'))
    dp.callback_query.register(show_node_statistics, F.data.startswith('node_stats_'))
    dp.callback_query.register(manage_node, F.data.startswith('node_enable_'))
    dp.callback_query.register(manage_node, F.data.startswith('node_disable_'))
    dp.callback_query.register(manage_node, F.data.startswith('node_restart_'))
    dp.callback_query.register(restart_all_nodes, F.data == 'admin_restart_all_nodes')
    dp.callback_query.register(show_sync_options, F.data == 'admin_rw_sync')
    dp.callback_query.register(show_auto_sync_settings, F.data == 'admin_rw_auto_sync')
    dp.callback_query.register(toggle_auto_sync_setting, F.data == 'remnawave_auto_sync_toggle')
    dp.callback_query.register(prompt_auto_sync_schedule, F.data == 'remnawave_auto_sync_times')
    dp.callback_query.register(cancel_auto_sync_schedule, F.data == 'remnawave_auto_sync_cancel')
    dp.callback_query.register(run_auto_sync_now, F.data == 'remnawave_auto_sync_run')
    dp.callback_query.register(sync_all_users, F.data == 'sync_all_users')
    dp.callback_query.register(sync_users_to_panel, F.data == 'sync_to_panel')
    dp.callback_query.register(show_squad_migration_menu, F.data == 'admin_rw_migration')
    dp.callback_query.register(paginate_migration_source, F.data.startswith('admin_migration_source_page_'))
    dp.callback_query.register(handle_migration_source_selection, F.data.startswith('admin_migration_source_'))
    dp.callback_query.register(paginate_migration_target, F.data.startswith('admin_migration_target_page_'))
    dp.callback_query.register(handle_migration_target_selection, F.data.startswith('admin_migration_target_'))
    dp.callback_query.register(change_migration_target, F.data == 'admin_migration_change_target')
    dp.callback_query.register(confirm_squad_migration, F.data == 'admin_migration_confirm')
    dp.callback_query.register(cancel_squad_migration, F.data == 'admin_migration_cancel')
    dp.callback_query.register(handle_migration_page_info, F.data == 'admin_migration_page_info')
    dp.callback_query.register(show_squads_management, F.data == 'admin_rw_squads')
    dp.callback_query.register(show_squad_details, F.data.startswith('admin_squad_manage_'))
    dp.callback_query.register(manage_squad_action, F.data.startswith('squad_add_users_'))
    dp.callback_query.register(manage_squad_action, F.data.startswith('squad_remove_users_'))
    dp.callback_query.register(manage_squad_action, F.data.startswith('squad_delete_'))
    dp.callback_query.register(
        show_squad_edit_menu, F.data.startswith('squad_edit_') & ~F.data.startswith('squad_edit_inbounds_')
    )
    dp.callback_query.register(show_squad_inbounds_selection, F.data.startswith('squad_edit_inbounds_'))
    dp.callback_query.register(show_squad_rename_form, F.data.startswith('squad_rename_'))
    dp.callback_query.register(cancel_squad_rename, F.data.startswith('cancel_rename_'))
    dp.callback_query.register(toggle_squad_inbound, F.data.startswith('sqd_tgl_'))
    dp.callback_query.register(save_squad_inbounds, F.data.startswith('sqd_save_'))
    dp.callback_query.register(show_squad_edit_menu_short, F.data.startswith('sqd_edit_'))
    dp.callback_query.register(start_squad_creation, F.data == 'admin_squad_create')
    dp.callback_query.register(cancel_squad_creation, F.data == 'cancel_squad_create')
    dp.callback_query.register(toggle_create_inbound, F.data.startswith('create_tgl_'))
    dp.callback_query.register(finish_squad_creation, F.data == 'create_squad_finish')

    dp.message.register(process_squad_new_name, SquadRenameStates.waiting_for_new_name, F.text)

    dp.message.register(process_squad_name, SquadCreateStates.waiting_for_name, F.text)

    dp.message.register(
        save_auto_sync_schedule,
        RemnaWaveSyncStates.waiting_for_schedule,
        F.text,
    )
