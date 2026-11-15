import html
import re
from datetime import datetime

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.backup_service import backup_service
from app.utils.admin_ui import admin_back_button, admin_button, admin_quote, admin_screen
from app.utils.decorators import admin_required, error_handler
from app.utils.miniapp_buttons import strip_leading_emoji


logger = structlog.get_logger(__name__)


def _back_label(value: str) -> str:
    """Подпись кнопки возврата: стрелка в тексте вместо premium-иконки."""

    label = (value or '').strip()
    if not label or label.lower().startswith(('←', '◀', '⬅', '⏪')):
        return label
    return f'← {label}'


# Следы прежнего оформления в локалях: декоративные глифы в начале строк.
_LEGACY_PREFIX_RE = re.compile(
    r'^[ \t]*(?:(?:[▤▣▥◷↻◀▫⚠ℹ→]|NEW(?=[ \t])|i(?=[ \t]))\ufe0f?[ \t]*)*',
    re.MULTILINE,
)


def _clean_legacy(value: str) -> str:
    """Убирает старые декоративные глифы и ведущие пробелы строк из локалей."""
    return _LEGACY_PREFIX_RE.sub('', value).strip()


def _label(value: str) -> str:
    """Подпись кнопки без старого ведущего глифа и лишних пробелов."""
    return strip_leading_emoji(value).strip()


_BACKUP_RESULT_KEYS = {
    '\u2717 Метаданные бекапа отсутствуют': 'ADMIN_BACKUP_RESULT_NO_METADATA',
    '\u2717 Недопустимое имя файла бекапа': 'ADMIN_BACKUP_RESULT_INVALID_FILENAME',
    '\u2717 Файл бекапа не содержит данных': 'ADMIN_BACKUP_RESULT_NO_DATA',
    'Файл пуст (0 байт)': 'ADMIN_BACKUP_RESULT_EMPTY_FILE',
    'неизвестно': 'ADMIN_BACKUP_RESULT_UNKNOWN_DATE',
}
_BACKUP_RESULT_PATTERNS = [
    (
        r'\u2713 Бекап успешно создан!\n▣ Файл: (?P<file>[\s\S]*)\n≡ Таблиц: (?P<tables>\d+)\n↑ Записей: (?P<records>[\d,]+)\n«Размер»: (?P<size>\d+\.\d+) MB',
        'ADMIN_BACKUP_RESULT_CREATED',
        '\u2713 Бекап успешно создан!\n▣ Файл: {file}\n≡ Таблиц: {tables}\n↑ Записей: {records}\n«Размер»: {size} MB',
    ),
    (
        r'\u2713 Восстановление завершено!\n≡ Таблиц: (?P<tables>\d+)\n↑ Записей: (?P<records>[\d,]+)\n▤ Дата бекапа: (?P<date>[\s\S]*)',
        'ADMIN_BACKUP_RESULT_RESTORED',
        '\u2713 Восстановление завершено!\n≡ Таблиц: {tables}\n↑ Записей: {records}\n▤ Дата бекапа: {date}',
    ),
    (r'\u2713 Бекап (?P<file>[\s\S]*) удален', 'ADMIN_BACKUP_RESULT_DELETED', '\u2713 Бекап {file} удален'),
    (
        r'\u2717 Ошибка создания бекапа: (?P<detail>[\s\S]*)',
        'ADMIN_BACKUP_RESULT_CREATE_ERROR',
        '\u2717 Ошибка создания бекапа: {detail}',
    ),
    (
        r'\u2717 Ошибка восстановления: (?P<detail>[\s\S]*)',
        'ADMIN_BACKUP_RESULT_RESTORE_ERROR',
        '\u2717 Ошибка восстановления: {detail}',
    ),
    (
        r'\u2717 Ошибка удаления бекапа: (?P<detail>[\s\S]*)',
        'ADMIN_BACKUP_RESULT_DELETE_ERROR',
        '\u2717 Ошибка удаления бекапа: {detail}',
    ),
    (
        r'\u2717 Файл бекапа не найден: (?P<file>[\s\S]*)',
        'ADMIN_BACKUP_RESULT_NOT_FOUND',
        '\u2717 Файл бекапа не найден: {file}',
    ),
    (r'Ошибка чтения: (?P<detail>[\s\S]*)', 'ADMIN_BACKUP_RESULT_READ_ERROR', 'Ошибка чтения: {detail}'),
    (
        r'pg_dump завершился с ошибкой: (?P<detail>[\s\S]*)',
        'ADMIN_BACKUP_RESULT_PG_DUMP_ERROR',
        'pg_dump завершился с ошибкой: {detail}',
    ),
    (
        r'Не удалось очистить схему: (?P<detail>[\s\S]*)',
        'ADMIN_BACKUP_RESULT_SCHEMA_ERROR',
        'Не удалось очистить схему: {detail}',
    ),
    (r'Ошибка psql: (?P<detail>[\s\S]*)', 'ADMIN_BACKUP_RESULT_PSQL_ERROR', 'Ошибка psql: {detail}'),
]


def _localize_backup_message(message: str, language: str) -> str:
    """Translate only known service reports without changing backup operations."""
    texts = get_texts(language)
    if key := _BACKUP_RESULT_KEYS.get(message):
        return texts.t(key, message)
    for pattern, key, default in _BACKUP_RESULT_PATTERNS:
        if match := re.fullmatch(pattern, message):
            values = match.groupdict()
            if values.get('date') == 'неизвестно':
                values['date'] = texts.t('ADMIN_BACKUP_RESULT_UNKNOWN_DATE', 'неизвестно')
            if 'detail' in values:
                values['detail'] = _localize_backup_message(values['detail'], language)
            return texts.t(key, default).format(**values)
    return message


class BackupStates(StatesGroup):
    waiting_backup_file = State()
    waiting_settings_update = State()


def get_backup_main_keyboard(language: str = 'ru'):
    texts = get_texts(language)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    _label(texts.t('ADMIN_BACKUP_UI_056', 'Создать бекап')),
                    callback_data='backup_create',
                    icon='plus',
                ),
                admin_button(
                    _label(texts.t('ADMIN_BACKUP_UI_057', 'Восстановить')),
                    callback_data='backup_restore',
                    icon='upload',
                ),
            ],
            [
                admin_button(
                    _label(texts.t('ADMIN_BACKUP_UI_058', 'Список бекапов')),
                    callback_data='backup_list',
                    icon='folder',
                ),
                admin_button(
                    _label(texts.t('ADMIN_BACKUP_UI_059', 'Настройки')),
                    callback_data='backup_settings',
                    icon='settings',
                ),
            ],
            [admin_back_button(texts, 'admin_panel')],
        ]
    )


def get_backup_list_keyboard(backups: list, page: int = 1, per_page: int = 5, language: str = 'ru'):
    texts = get_texts(language)
    keyboard = []

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_backups = backups[start_idx:end_idx]

    for backup in page_backups:
        try:
            if backup.get('timestamp'):
                dt = datetime.fromisoformat(backup['timestamp'].replace('Z', '+00:00'))
                date_str = dt.strftime('%d.%m %H:%M')
            else:
                date_str = '?'
        except:
            date_str = '?'

        size_str = f'{backup.get("file_size_mb", 0):.1f}MB'
        records_str = backup.get('total_records', '?')

        button_text = _label(
            texts.t('ADMIN_BACKUP_UI_006', '{v0} • {v1} • {v2} записей').format(
                v0=date_str, v1=size_str, v2=records_str
            )
        )
        callback_data = f'backup_manage_{backup["filename"]}'

        keyboard.append(
            [
                admin_button(
                    button_text,
                    callback_data=callback_data,
                    icon='backup',
                )
            ]
        )

    if len(backups) > per_page:
        total_pages = (len(backups) + per_page - 1) // per_page
        nav_row = []

        if page > 1:
            nav_row.append(admin_button('←', callback_data=f'backup_list_page_{page - 1}'))

        nav_row.append(InlineKeyboardButton(text=f'{page}/{total_pages}', callback_data='noop'))

        if page < total_pages:
            nav_row.append(admin_button('→', callback_data=f'backup_list_page_{page + 1}'))

        keyboard.append(nav_row)

    keyboard.append([admin_back_button(texts, 'backup_panel')])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_backup_manage_keyboard(backup_filename: str, language: str = 'ru'):
    texts = get_texts(language)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    _label(texts.t('ADMIN_BACKUP_UI_057', 'Восстановить')),
                    callback_data=f'backup_restore_file_{backup_filename}',
                    icon='upload',
                )
            ],
            [
                admin_button(
                    _label(texts.t('ADMIN_BACKUP_UI_060', 'Удалить')),
                    callback_data=f'backup_delete_{backup_filename}',
                    icon='delete',
                )
            ],
            [
                admin_back_button(
                    texts,
                    'backup_list',
                    label=_back_label(_label(texts.t('ADMIN_BACKUP_UI_061', 'К списку'))),
                )
            ],
        ]
    )


def get_backup_settings_keyboard(settings_obj, language: str = 'ru'):
    texts = get_texts(language)
    auto_status = (
        texts.t('ADMIN_BACKUP_UI_007', 'Включены')
        if settings_obj.auto_backup_enabled
        else texts.t('ADMIN_BACKUP_UI_008', 'Отключены')
    )
    compression_status = (
        texts.t('ADMIN_BACKUP_UI_009', 'Включено')
        if settings_obj.compression_enabled
        else texts.t('ADMIN_BACKUP_UI_010', 'Отключено')
    )
    logs_status = (
        texts.t('ADMIN_BACKUP_UI_007', 'Включены')
        if settings_obj.include_logs
        else texts.t('ADMIN_BACKUP_UI_008', 'Отключены')
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    _label(texts.t('ADMIN_BACKUP_UI_062', 'Автобекапы: {v0}').format(v0=auto_status)),
                    callback_data='backup_toggle_auto',
                    icon='calendar',
                )
            ],
            [
                admin_button(
                    _label(texts.t('ADMIN_BACKUP_UI_063', 'Сжатие: {v0}').format(v0=compression_status)),
                    callback_data='backup_toggle_compression',
                    icon='save',
                )
            ],
            [
                admin_button(
                    _label(texts.t('ADMIN_BACKUP_UI_064', 'Логи в бекапе: {v0}').format(v0=logs_status)),
                    callback_data='backup_toggle_logs',
                    icon='logs',
                )
            ],
            [admin_back_button(texts, 'backup_panel')],
        ]
    )


@admin_required
@error_handler
async def show_backup_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    settings_obj = await backup_service.get_backup_settings()

    status_auto = 'Включены' if settings_obj.auto_backup_enabled else 'Отключены'

    text = '\n'.join(
        [
            '<b>Система бекапов</b>',
            '',
            admin_quote(
                '\n'.join(
                    [
                        '<b>Статус:</b>',
                        f'• Автобекапы: {status_auto}',
                        f'• Интервал: {settings_obj.backup_interval_hours} часов',
                        f'• Хранить: {settings_obj.max_backups_keep} файлов',
                        f'• Сжатие: {"Да" if settings_obj.compression_enabled else "Нет"}',
                        '',
                        '<b>Расположение:</b> <code>/app/data/backups</code>',
                    ]
                )
            ),
            '',
            '<b>Доступные операции:</b>',
            '• Создание полного бекапа всех данных',
            '• Восстановление из файла бекапа',
            '• Управление автоматическими бекапами',
        ]
    )

    await callback.message.edit_text(
        admin_screen(text, icon='backup', compact=True),
        parse_mode='HTML',
        reply_markup=get_backup_main_keyboard(db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def create_backup_handler(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    await callback.answer(_label(texts.t('ADMIN_BACKUP_UI_026', 'Создание бекапа запущено...')))

    progress_msg = await callback.message.edit_text(
        admin_screen(
            _clean_legacy(
                texts.t(
                    'ADMIN_BACKUP_UI_027',
                    '<b>Создание бекапа...</b>\n\nЭкспортируем данные из базы...\nЭто может занять несколько минут.',
                )
            ),
            icon='refresh',
            compact=True,
        ),
        parse_mode='HTML',
    )

    # Создаем бекап
    created_by_id = db_user.telegram_id or db_user.email or f'#{db_user.id}'
    success, message, file_path = await backup_service.create_backup(created_by=created_by_id, compress=True)
    message = _localize_backup_message(message, db_user.language)

    if success:
        await progress_msg.edit_text(
            admin_screen(
                _clean_legacy(
                    texts.t('ADMIN_BACKUP_UI_036', '<b>Бекап создан успешно!</b>\n\n{v0}').format(v0=message)
                ),
                icon='success',
                compact=True,
            ),
            parse_mode='HTML',
            reply_markup=get_backup_main_keyboard(db_user.language),
        )
    else:
        await progress_msg.edit_text(
            admin_screen(
                _clean_legacy(
                    texts.t('ADMIN_BACKUP_UI_037', '<b>Ошибка создания бекапа</b>\n\n{v0}').format(
                        v0=html.escape(message)
                    )
                ),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
            reply_markup=get_backup_main_keyboard(db_user.language),
        )


@admin_required
@error_handler
async def show_backup_list(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    page = 1
    if callback.data.startswith('backup_list_page_'):
        try:
            page = int(callback.data.split('_')[-1])
        except:
            page = 1

    backups = await backup_service.get_backup_list()

    if not backups:
        text = texts.t('ADMIN_BACKUP_UI_011', '<b>Список бекапов пуст</b>\n\nБекапы еще не создавались.')
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_button(
                        _label(texts.t('ADMIN_BACKUP_UI_067', 'Создать первый бекап')),
                        callback_data='backup_create',
                        icon='plus',
                    )
                ],
                [admin_back_button(texts, 'backup_panel')],
            ]
        )
    else:
        text = texts.t('ADMIN_BACKUP_UI_012', '<b>Список бекапов</b> (всего: {v0})\n\n').format(v0=len(backups))
        text += texts.t('ADMIN_BACKUP_UI_013', 'Выберите бекап для управления:')
        keyboard = get_backup_list_keyboard(backups, page, language=db_user.language)

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='backup', compact=True),
        parse_mode='HTML',
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def manage_backup_file(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    filename = callback.data.replace('backup_manage_', '')

    backups = await backup_service.get_backup_list()
    backup_info = None

    for backup in backups:
        if backup['filename'] == filename:
            backup_info = backup
            break

    if not backup_info:
        await callback.answer(
            _clean_legacy(get_texts(db_user.language).t('ADMIN_BACKUP_UI_038', 'Файл бекапа не найден')),
            show_alert=True,
        )
        return

    try:
        if backup_info.get('timestamp'):
            dt = datetime.fromisoformat(backup_info['timestamp'].replace('Z', '+00:00'))
            date_str = dt.strftime('%d.%m.%Y %H:%M:%S')
        else:
            date_str = get_texts(db_user.language).t('ADMIN_BACKUP_UI_028', 'Неизвестно')
    except:
        date_str = 'Ошибка формата даты'

    text = '\n'.join(
        [
            '<b>Информация о бекапе</b>',
            '',
            admin_quote(
                '\n'.join(
                    [
                        f'<b>Файл:</b> <code>{html.escape(filename)}</code>',
                        f'<b>Создан:</b> {date_str}',
                        f'<b>Размер:</b> {backup_info.get("file_size_mb", 0):.2f} MB',
                        f'<b>Таблиц:</b> {backup_info.get("tables_count", "?")}',
                        f'<b>Записей:</b> {backup_info.get("total_records", "?"):,}',
                        f'<b>Сжатие:</b> {"Да" if backup_info.get("compressed") else "Нет"}',
                        f'<b>БД:</b> {html.escape(str(backup_info.get("database_type", "unknown")))}',
                    ]
                )
            ),
        ]
    )

    if backup_info.get('error'):
        text += (
            get_texts(db_user.language)
            .t('ADMIN_BACKUP_UI_014', '\n<b>Ошибка:</b> {v0}')
            .format(v0=_localize_backup_message(backup_info['error'], db_user.language))
        )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='backup', compact=True),
        parse_mode='HTML',
        reply_markup=get_backup_manage_keyboard(filename, language=db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_backup_confirm(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    filename = callback.data.replace('backup_delete_', '')
    texts = get_texts(db_user.language)

    text = texts.t('ADMIN_BACKUP_UI_002', '<b>Удаление бекапа</b>\n\n')
    text += texts.t('ADMIN_BACKUP_UI_003', 'Вы уверены, что хотите удалить бекап?\n\n')
    text += f'<code>{html.escape(filename)}</code>\n\n'
    text += texts.t('ADMIN_BACKUP_UI_004', '<b>Это действие нельзя отменить!</b>')

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    _label(texts.t('ADMIN_BACKUP_UI_065', 'Да, удалить')),
                    callback_data=f'backup_delete_confirm_{filename}',
                    icon='delete',
                ),
                admin_button(
                    _label(texts.t('ADMIN_BACKUP_UI_066', 'Отмена')),
                    callback_data=f'backup_manage_{filename}',
                    icon='cross',
                ),
            ]
        ]
    )

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='delete', compact=True),
        parse_mode='HTML',
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_backup_execute(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    filename = callback.data.replace('backup_delete_confirm_', '')
    texts = get_texts(db_user.language)

    success, message = await backup_service.delete_backup(filename)
    message = _localize_backup_message(message, db_user.language)

    if success:
        await callback.message.edit_text(
            admin_screen(
                _clean_legacy(texts.t('ADMIN_BACKUP_UI_039', '<b>Бекап удален</b>\n\n{v0}').format(v0=message)),
                icon='success',
                compact=True,
            ),
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_button(
                            _label(texts.t('ADMIN_BACKUP_UI_072', 'К списку бекапов')),
                            callback_data='backup_list',
                            icon='folder',
                        )
                    ]
                ]
            ),
        )
    else:
        await callback.message.edit_text(
            admin_screen(
                _clean_legacy(texts.t('ADMIN_BACKUP_UI_040', '<b>Ошибка удаления</b>\n\n{v0}').format(v0=message)),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
            reply_markup=get_backup_manage_keyboard(filename, language=db_user.language),
        )

    await callback.answer()


@admin_required
@error_handler
async def restore_backup_start(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    texts = get_texts(db_user.language)
    if callback.data.startswith('backup_restore_file_'):
        # Восстановление из конкретного файла
        filename = callback.data.replace('backup_restore_file_', '')

        text = texts.t('ADMIN_BACKUP_UI_015', '<b>Восстановление из бекапа</b>')
        file_block = admin_quote(
            texts.t('ADMIN_BACKUP_UI_016', '<b>Файл:</b> <code>{v0}</code>').format(v0=html.escape(filename))
        )
        text += f'\n\n{file_block}\n\n'
        text += texts.t('ADMIN_BACKUP_UI_017', '<b>Внимание!</b>\n')
        text += texts.t('ADMIN_BACKUP_UI_018', '• Процесс может занять несколько минут\n')
        text += texts.t('ADMIN_BACKUP_UI_019', '• Рекомендуется создать бекап перед восстановлением\n')
        text += texts.t('ADMIN_BACKUP_UI_020', '• Существующие данные будут дополнены\n\n')
        text += texts.t('ADMIN_BACKUP_UI_021', 'Продолжить восстановление?')

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_button(
                        _label(texts.t('ADMIN_BACKUP_UI_071', 'Восстановить')),
                        callback_data=f'backup_restore_execute_{filename}',
                        icon='upload',
                    ),
                    admin_button(
                        _label(texts.t('ADMIN_BACKUP_UI_069', 'Очистить и восстановить')),
                        callback_data=f'backup_restore_clear_{filename}',
                        icon='delete',
                    ),
                ],
                [
                    admin_button(
                        _label(texts.t('ADMIN_BACKUP_UI_066', 'Отмена')),
                        callback_data=f'backup_manage_{filename}',
                        icon='cross',
                    )
                ],
            ]
        )
    else:
        text = """<b>Восстановление из бекапа</b>

Отправьте файл бекапа (.json, .json.gz или .tar.gz)

<b>Важно:</b>
• Файл должен быть создан этой системой бекапов
• Процесс может занять несколько минут
• Рекомендуется создать бекап перед восстановлением

Или выберите из существующих бекапов ниже."""

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_button(
                        _label(texts.t('ADMIN_BACKUP_UI_070', 'Выбрать из списка')),
                        callback_data='backup_list',
                        icon='folder',
                    )
                ],
                [
                    admin_button(
                        _label(texts.t('ADMIN_BACKUP_UI_066', 'Отмена')),
                        callback_data='backup_panel',
                        icon='cross',
                    )
                ],
            ]
        )

        await state.set_state(BackupStates.waiting_backup_file)

    await callback.message.edit_text(
        admin_screen(_clean_legacy(text), icon='upload', compact=True),
        parse_mode='HTML',
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def restore_backup_execute(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    if callback.data.startswith('backup_restore_execute_'):
        filename = callback.data.replace('backup_restore_execute_', '')
        clear_existing = False
    elif callback.data.startswith('backup_restore_clear_'):
        filename = callback.data.replace('backup_restore_clear_', '')
        clear_existing = True
    else:
        await callback.answer(_label(texts.t('ADMIN_BACKUP_UI_051', 'Неверный формат команды')), show_alert=True)
        return

    await callback.answer(_label(texts.t('ADMIN_BACKUP_UI_030', 'Восстановление запущено...')))

    # Показываем прогресс
    action_text = (
        texts.t('ADMIN_BACKUP_UI_023', 'очисткой и восстановлением')
        if clear_existing
        else texts.t('ADMIN_BACKUP_UI_024', 'восстановлением')
    )
    progress_msg = await callback.message.edit_text(
        admin_screen(
            _clean_legacy(
                texts.t(
                    'ADMIN_BACKUP_UI_031',
                    '<b>Восстановление из бекапа...</b>\n\nРаботаем с {v0} данных...\nФайл: <code>{v1}</code>\n\nЭто может занять несколько минут.',
                ).format(v0=action_text, v1=html.escape(filename))
            ),
            icon='refresh',
            compact=True,
        ),
        parse_mode='HTML',
    )

    backup_path = backup_service.backup_dir / filename

    success, message = await backup_service.restore_backup(str(backup_path), clear_existing=clear_existing)
    message = _localize_backup_message(message, db_user.language)

    if success:
        await progress_msg.edit_text(
            admin_screen(
                _clean_legacy(
                    texts.t('ADMIN_BACKUP_UI_041', '<b>Восстановление завершено!</b>\n\n{v0}').format(v0=message)
                ),
                icon='success',
                compact=True,
            ),
            parse_mode='HTML',
            reply_markup=get_backup_main_keyboard(db_user.language),
        )
    else:
        await progress_msg.edit_text(
            admin_screen(
                _clean_legacy(
                    texts.t('ADMIN_BACKUP_UI_042', '<b>Ошибка восстановления</b>\n\n{v0}').format(v0=message)
                ),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
            reply_markup=get_backup_manage_keyboard(filename, language=db_user.language),
        )


@admin_required
@error_handler
async def handle_backup_file_upload(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    texts = get_texts(db_user.language)
    if not message.document:
        await message.answer(
            admin_screen(
                _clean_legacy(
                    texts.t('ADMIN_BACKUP_UI_043', 'Пожалуйста, отправьте файл бекапа (.json, .json.gz или .tar.gz)')
                ),
                icon='warning',
                compact=True,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_button(
                            _label(texts.t('ADMIN_BACKUP_UI_073', 'Отмена')),
                            callback_data='backup_panel',
                            icon='cross',
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )
        return

    document = message.document
    allowed_extensions = ('.json', '.json.gz', '.tar.gz', '.tar')

    if not document.file_name or not any(document.file_name.endswith(ext) for ext in allowed_extensions):
        await message.answer(
            admin_screen(
                _clean_legacy(
                    texts.t(
                        'ADMIN_BACKUP_UI_044',
                        'Неподдерживаемый формат файла. Загрузите .json, .json.gz или .tar.gz файл',
                    )
                ),
                icon='error',
                compact=True,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_button(
                            _label(texts.t('ADMIN_BACKUP_UI_073', 'Отмена')),
                            callback_data='backup_panel',
                            icon='cross',
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )
        return

    if document.file_size > 50 * 1024 * 1024:
        await message.answer(
            admin_screen(
                _clean_legacy(texts.t('ADMIN_BACKUP_UI_045', 'Файл слишком большой (максимум 50MB)')),
                icon='error',
                compact=True,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_button(
                            _label(texts.t('ADMIN_BACKUP_UI_073', 'Отмена')),
                            callback_data='backup_panel',
                            icon='cross',
                        )
                    ]
                ]
            ),
            parse_mode='HTML',
        )
        return

    try:
        file = await message.bot.get_file(document.file_id)

        temp_path = backup_service.backup_dir / f'uploaded_{document.file_name}'

        await message.bot.download_file(file.file_path, temp_path)

        uploaded_name = html.escape(document.file_name or '')

        text = '\n'.join(
            [
                '<b>Файл загружен</b>',
                '',
                admin_quote(
                    '\n'.join(
                        [
                            f'<b>Имя:</b> <code>{uploaded_name}</code>',
                            f'<b>Размер:</b> {document.file_size / 1024 / 1024:.2f} MB',
                        ]
                    )
                ),
                '',
                '<b>Внимание!</b>',
                'Процесс восстановления изменит данные в базе.',
                'Рекомендуется создать бекап перед восстановлением.',
                '',
                'Продолжить?',
            ]
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_button(
                        _label(texts.t('ADMIN_BACKUP_UI_071', 'Восстановить')),
                        callback_data=f'backup_restore_execute_{temp_path.name}',
                        icon='upload',
                    ),
                    admin_button(
                        _label(texts.t('ADMIN_BACKUP_UI_069', 'Очистить и восстановить')),
                        callback_data=f'backup_restore_clear_{temp_path.name}',
                        icon='delete',
                    ),
                ],
                [
                    admin_button(
                        _label(texts.t('ADMIN_BACKUP_UI_066', 'Отмена')),
                        callback_data='backup_panel',
                        icon='cross',
                    )
                ],
            ]
        )

        await message.answer(admin_screen(text, icon='upload', compact=True), parse_mode='HTML', reply_markup=keyboard)
        await state.clear()

    except Exception as e:
        logger.error('Ошибка загрузки файла бекапа', error=e)
        await message.answer(
            admin_screen(
                _clean_legacy(
                    texts.t('ADMIN_BACKUP_UI_052', 'Ошибка загрузки файла: {v0!s}').format(v0=html.escape(str(e)))
                ),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        admin_button(
                            _label(texts.t('ADMIN_BACKUP_UI_073', 'Отмена')),
                            callback_data='backup_panel',
                            icon='cross',
                        )
                    ]
                ]
            ),
        )


@admin_required
@error_handler
async def show_backup_settings(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    settings_obj = await backup_service.get_backup_settings()

    text = '\n'.join(
        [
            '<b>Настройки системы бекапов</b>',
            '',
            admin_quote(
                '\n'.join(
                    [
                        '<b>Автоматические бекапы:</b>',
                        f'• Статус: {"Включены" if settings_obj.auto_backup_enabled else "Отключены"}',
                        f'• Интервал: {settings_obj.backup_interval_hours} часов',
                        f'• Время запуска: {settings_obj.backup_time}',
                        '',
                        '<b>Хранение:</b>',
                        f'• Максимум файлов: {settings_obj.max_backups_keep}',
                        f'• Сжатие: {"Включено" if settings_obj.compression_enabled else "Отключено"}',
                        f'• Включать логи: {"Да" if settings_obj.include_logs else "Нет"}',
                        '',
                        f'<b>Расположение:</b> <code>{html.escape(str(settings_obj.backup_location))}</code>',
                    ]
                )
            ),
        ]
    )

    await callback.message.edit_text(
        admin_screen(text, icon='settings', compact=True),
        parse_mode='HTML',
        reply_markup=get_backup_settings_keyboard(settings_obj, language=db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_backup_setting(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    settings_obj = await backup_service.get_backup_settings()

    if callback.data == 'backup_toggle_auto':
        new_value = not settings_obj.auto_backup_enabled
        await backup_service.update_backup_settings(auto_backup_enabled=new_value)
        status = (
            get_texts(db_user.language).t('ADMIN_BACKUP_UI_032', 'включены')
            if new_value
            else get_texts(db_user.language).t('ADMIN_BACKUP_UI_033', 'отключены')
        )
        await callback.answer(get_texts(db_user.language).t('ADMIN_BACKUP_UI_048', 'Автобекапы {v0}').format(v0=status))

    elif callback.data == 'backup_toggle_compression':
        new_value = not settings_obj.compression_enabled
        await backup_service.update_backup_settings(compression_enabled=new_value)
        status = (
            get_texts(db_user.language).t('ADMIN_BACKUP_UI_049', 'включено')
            if new_value
            else get_texts(db_user.language).t('ADMIN_BACKUP_UI_050', 'отключено')
        )
        await callback.answer(get_texts(db_user.language).t('ADMIN_BACKUP_UI_053', 'Сжатие {v0}').format(v0=status))

    elif callback.data == 'backup_toggle_logs':
        new_value = not settings_obj.include_logs
        await backup_service.update_backup_settings(include_logs=new_value)
        status = (
            get_texts(db_user.language).t('ADMIN_BACKUP_UI_032', 'включены')
            if new_value
            else get_texts(db_user.language).t('ADMIN_BACKUP_UI_033', 'отключены')
        )
        await callback.answer(
            get_texts(db_user.language).t('ADMIN_BACKUP_UI_055', 'Логи в бекапе {v0}').format(v0=status)
        )

    await show_backup_settings(callback, db_user, db)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_backup_panel, F.data == 'backup_panel')

    dp.callback_query.register(create_backup_handler, F.data == 'backup_create')

    dp.callback_query.register(show_backup_list, F.data.startswith('backup_list'))

    dp.callback_query.register(manage_backup_file, F.data.startswith('backup_manage_'))

    dp.callback_query.register(
        delete_backup_confirm, F.data.startswith('backup_delete_') & ~F.data.startswith('backup_delete_confirm_')
    )

    dp.callback_query.register(delete_backup_execute, F.data.startswith('backup_delete_confirm_'))

    dp.callback_query.register(
        restore_backup_start, F.data.in_(['backup_restore']) | F.data.startswith('backup_restore_file_')
    )

    dp.callback_query.register(
        restore_backup_execute,
        F.data.startswith('backup_restore_execute_') | F.data.startswith('backup_restore_clear_'),
    )

    dp.callback_query.register(show_backup_settings, F.data == 'backup_settings')

    dp.callback_query.register(
        toggle_backup_setting, F.data.in_(['backup_toggle_auto', 'backup_toggle_compression', 'backup_toggle_logs'])
    )

    dp.message.register(handle_backup_file_upload, BackupStates.waiting_backup_file)
