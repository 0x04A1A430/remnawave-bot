import logging
import os
from datetime import datetime
from pathlib import Path
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.services.backup_service import backup_service
from app.utils.decorators import admin_required, error_handler
from app.keyboards.admin import (
    get_admin_main_keyboard, 
    get_confirmation_keyboard,
    get_admin_pagination_keyboard
)

logger = logging.getLogger(__name__)


class BackupStates(StatesGroup):
    waiting_backup_file = State()
    waiting_settings_update = State()


def get_backup_main_keyboard(language: str = "ru"):
    """Главная клавиатура настроек бекапов"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_backup_manage_keyboard(backup_filename: str):
    """Клавиатура управления конкретным бекапом"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📥 Восстановить", callback_data=f"backup_restore_file_{backup_filename}"),
            InlineKeyboardButton(text="📄 Информация", callback_data=f"backup_info_{backup_filename}")
        ],
        [
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"backup_delete_{backup_filename}")
        ],
        [
            InlineKeyboardButton(text="◀️ К списку", callback_data="backup_list")
        ]
    ])


def get_backup_settings_keyboard(settings_obj):
    """Клавиатура настроек бекапов"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    auto_status = "✅ Включены" if settings_obj.auto_backup_enabled else "❌ Отключены"
    compression_status = "✅ Включено" if settings_obj.compression_enabled else "❌ Отключено"
    logs_status = "✅ Включены" if settings_obj.include_logs else "❌ Отключены"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"🔄 Автобекапы: {auto_status}", 
                callback_data="backup_toggle_auto"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🗜️ Сжатие: {compression_status}", 
                callback_data="backup_toggle_compression"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"📋 Логи в бекапе: {logs_status}", 
                callback_data="backup_toggle_logs"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"⏰ Интервал: {settings_obj.backup_interval_hours}ч", 
                callback_data="backup_set_interval"
            ),
            InlineKeyboardButton(
                text=f"📦 Хранить: {settings_obj.max_backups_keep}шт", 
                callback_data="backup_set_retention"
            )
        ],
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data="backup_panel")
        ]
    ])


@admin_required
@error_handler
async def show_backup_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Показывает главную панель бекапов"""
    settings_obj = await backup_service.get_backup_settings()
    
    status_auto = "✅ Включены" if settings_obj.auto_backup_enabled else "❌ Отключены"
    
    text = f"""🗄️ <b>СИСТЕМА БЕКАПОВ</b>

📊 <b>Статус:</b>
• Автобекапы: {status_auto}
• Интервал: {settings_obj.backup_interval_hours} часов
• Хранить: {settings_obj.max_backups_keep} файлов
• Сжатие: {'Да' if settings_obj.compression_enabled else 'Нет'}

📁 <b>Расположение:</b> <code>/app/data/backups</code>

⚡ <b>Доступные операции:</b>
• Создание полного бекапа всех данных
• Восстановление из файла бекапа
• Управление автоматическими бекапами
• Просмотр истории операций
"""
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_backup_main_keyboard(db_user.language)
    )
    await callback.answer()


@admin_required
@error_handler
async def create_backup_handler(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Запускает создание бекапа"""
    await callback.answer("🔄 Создание бекапа запущено...")
    
    # Показываем сообщение о начале процесса
    progress_msg = await callback.message.edit_text(
        "🔄 <b>Создание бекапа...</b>\n\n"
        "⏳ Экспортируем данные из базы...\n"
        "Это может занять несколько минут.",
        parse_mode="HTML"
    )
    
    # Создаем бекап
    success, message, file_path = await backup_service.create_backup(
        created_by=db_user.telegram_id,
        compress=True
    )
    
    if success:
        await progress_msg.edit_text(
            f"✅ <b>Бекап создан успешно!</b>\n\n{message}",
            parse_mode="HTML",
            reply_markup=get_backup_main_keyboard(db_user.language)
        )
    else:
        await progress_msg.edit_text(
            f"❌ <b>Ошибка создания бекапа</b>\n\n{message}",
            parse_mode="HTML",
            reply_markup=get_backup_main_keyboard(db_user.language)
        )


@admin_required
@error_handler
async def show_backup_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Показывает список бекапов"""
    # Извлекаем номер страницы из callback_data
    page = 1
    if callback.data.startswith("backup_list_page_"):
        try:
            page = int(callback.data.split("_")[-1])
        except:
            page = 1
    
    backups = await backup_service.get_backup_list()
    
    if not backups:
        text = "📦 <b>Список бекапов пуст</b>\n\nБекапы еще не создавались."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Создать первый бекап", callback_data="backup_create")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="backup_panel")]
        ])
    else:
        text = f"📦 <b>Список бекапов</b> (всего: {len(backups)})\n\n"
        text += "Выберите бекап для управления:"
        keyboard = get_backup_list_keyboard(backups, page)
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@admin_required
@error_handler
async def manage_backup_file(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Управление конкретным файлом бекапа"""
    filename = callback.data.replace("backup_manage_", "")
    
    backups = await backup_service.get_backup_list()
    backup_info = None
    
    for backup in backups:
        if backup["filename"] == filename:
            backup_info = backup
            break
    
    if not backup_info:
        await callback.answer("❌ Файл бекапа не найден", show_alert=True)
        return
    
    # Форматируем информацию о бекапе
    try:
        if backup_info.get("timestamp"):
            dt = datetime.fromisoformat(backup_info["timestamp"].replace('Z', '+00:00'))
            date_str = dt.strftime("%d.%m.%Y %H:%M:%S")
        else:
            date_str = "Неизвестно"
    except:
        date_str = "Ошибка формата даты"
    
    text = f"""📦 <b>Информация о бекапе</b>

📄 <b>Файл:</b> <code>{filename}</code>
📅 <b>Создан:</b> {date_str}
💾 <b>Размер:</b> {backup_info.get('file_size_mb', 0):.2f} MB
📊 <b>Таблиц:</b> {backup_info.get('tables_count', '?')}
📈 <b>Записей:</b> {backup_info.get('total_records', '?'):,}
🗜️ <b>Сжатие:</b> {'Да' if backup_info.get('compressed') else 'Нет'}
🗄️ <b>БД:</b> {backup_info.get('database_type', 'unknown')}
"""
    
    if backup_info.get("error"):
        text += f"\n⚠️ <b>Ошибка:</b> {backup_info['error']}"
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_backup_manage_keyboard(filename)
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_backup_confirm(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Подтверждение удаления бекапа"""
    filename = callback.data.replace("backup_delete_", "")
    
    text = f"🗑️ <b>Удаление бекапа</b>\n\n"
    text += f"Вы уверены, что хотите удалить бекап?\n\n"
    text += f"📄 <code>{filename}</code>\n\n"
    text += "⚠️ <b>Это действие нельзя отменить!</b>"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"backup_delete_confirm_{filename}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"backup_manage_{filename}")
        ]
    ])
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_backup_execute(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Выполняет удаление бекапа"""
    filename = callback.data.replace("backup_delete_confirm_", "")
    
    success, message = await backup_service.delete_backup(filename)
    
    if success:
        await callback.message.edit_text(
            f"✅ <b>Бекап удален</b>\n\n{message}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 К списку бекапов", callback_data="backup_list")]
            ])
        )
    else:
        await callback.message.edit_text(
            f"❌ <b>Ошибка удаления</b>\n\n{message}",
            parse_mode="HTML",
            reply_markup=get_backup_manage_keyboard(filename)
        )
    
    await callback.answer()


@admin_required
@error_handler
async def restore_backup_start(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    """Начинает процесс восстановления из бекапа"""
    if callback.data.startswith("backup_restore_file_"):
        # Восстановление из конкретного файла
        filename = callback.data.replace("backup_restore_file_", "")
        
        text = f"📥 <b>Восстановление из бекапа</b>\n\n"
        text += f"📄 <b>Файл:</b> <code>{filename}</code>\n\n"
        text += "⚠️ <b>ВНИМАНИЕ!</b>\n"
        text += "• Процесс может занять несколько минут\n"
        text += "• Рекомендуется создать бекап перед восстановлением\n"
        text += "• Существующие данные будут дополнены\n\n"
        text += "Продолжить восстановление?"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, восстановить", callback_data=f"backup_restore_execute_{filename}"),
                InlineKeyboardButton(text="🗑️ Очистить и восстановить", callback_data=f"backup_restore_clear_{filename}")
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"backup_manage_{filename}")
            ]
        ])
    else:
        # Восстановление из загруженного файла
        text = """📥 <b>Восстановление из бекапа</b>

📎 Отправьте файл бекапа (.json или .json.gz)

⚠️ <b>ВАЖНО:</b>
• Файл должен быть создан этой системой бекапов
• Процесс может занять несколько минут
• Рекомендуется создать бекап перед восстановлением

💡 Или выберите из существующих бекапов ниже."""
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Выбрать из списка", callback_data="backup_list")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="backup_panel")]
        ])
        
        await state.set_state(BackupStates.waiting_backup_file)
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


@admin_required
@error_handler
async def restore_backup_execute(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Выполняет восстановление бекапа"""
    if callback.data.startswith("backup_restore_execute_"):
        filename = callback.data.replace("backup_restore_execute_", "")
        clear_existing = False
    elif callback.data.startswith("backup_restore_clear_"):
        filename = callback.data.replace("backup_restore_clear_", "")
        clear_existing = True
    else:
        await callback.answer("❌ Неверный формат команды", show_alert=True)
        return
    
    await callback.answer("🔄 Восстановление запущено...")
    
    # Показываем прогресс
    action_text = "очисткой и восстановлением" if clear_existing else "восстановлением"
    progress_msg = await callback.message.edit_text(
        f"📥 <b>Восстановление из бекапа...</b>\n\n"
        f"⏳ Работаем с {action_text} данных...\n"
        f"📄 Файл: <code>{filename}</code>\n\n"
        f"Это может занять несколько минут.",
        parse_mode="HTML"
    )
    
    # Формируем путь к файлу
    backup_path = backup_service.backup_dir / filename
    
    # Выполняем восстановление
    success, message = await backup_service.restore_backup(
        str(backup_path),
        clear_existing=clear_existing
    )
    
    if success:
        await progress_msg.edit_text(
            f"✅ <b>Восстановление завершено!</b>\n\n{message}",
            parse_mode="HTML",
            reply_markup=get_backup_main_keyboard(db_user.language)
        )
    else:
        await progress_msg.edit_text(
            f"❌ <b>Ошибка восстановления</b>\n\n{message}",
            parse_mode="HTML",
            reply_markup=get_backup_manage_keyboard(filename)
        )


@admin_required
@error_handler
async def handle_backup_file_upload(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    """Обрабатывает загрузку файла бекапа"""
    if not message.document:
        await message.answer(
            "❌ Пожалуйста, отправьте файл бекапа (.json или .json.gz)",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="backup_panel")]
            ])
        )
        return
    
    document = message.document
    
    # Проверяем расширение файла
    if not (document.file_name.endswith('.json') or document.file_name.endswith('.json.gz')):
        await message.answer(
            "❌ Неподдерживаемый формат файла. Загрузите .json или .json.gz файл",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="backup_panel")]
            ])
        )
        return
    
    # Проверяем размер файла (максимум 50MB)
    if document.file_size > 50 * 1024 * 1024:
        await message.answer(
            "❌ Файл слишком большой (максимум 50MB)",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="backup_panel")]
            ])
        )
        return
    
    try:
        # Скачиваем файл
        file = await message.bot.get_file(document.file_id)
        
        # Создаем временный файл
        temp_path = backup_service.backup_dir / f"uploaded_{document.file_name}"
        
        await message.bot.download_file(file.file_path, temp_path)
        
        # Подтверждение восстановления
        text = f"""📥 <b>Файл загружен</b>

📄 <b>Имя:</b> <code>{document.file_name}</code>
💾 <b>Размер:</b> {document.file_size / 1024 / 1024:.2f} MB

⚠️ <b>ВНИМАНИЕ!</b>
Процесс восстановления изменит данные в базе.
Рекомендуется создать бекап перед восстановлением.

Продолжить?"""
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Восстановить", callback_data=f"backup_restore_uploaded_{temp_path.name}"),
                InlineKeyboardButton(text="🗑️ Очистить и восстановить", callback_data=f"backup_restore_uploaded_clear_{temp_path.name}")
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="backup_panel")
            ]
        ])
        
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
        await state.clear()
        
    except Exception as e:
        logger.error(f"Ошибка загрузки файла бекапа: {e}")
        await message.answer(
            f"❌ Ошибка загрузки файла: {str(e)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="backup_panel")]
            ])
        )


@admin_required
@error_handler
async def restore_uploaded_backup(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Восстанавливает загруженный бекап"""
    if callback.data.startswith("backup_restore_uploaded_clear_"):
        filename = callback.data.replace("backup_restore_uploaded_clear_", "")
        clear_existing = True
    else:
        filename = callback.data.replace("backup_restore_uploaded_", "")
        clear_existing = False
    
    await callback.answer("🔄 Восстановление запущено...")
    
    temp_path = backup_service.backup_dir / filename
    
    if not temp_path.exists():
        await callback.message.edit_text(
            "❌ Временный файл не найден. Попробуйте загрузить файл заново.",
            reply_markup=get_backup_main_keyboard(db_user.language)
        )
        return
    
    # Показываем прогресс
    action_text = "очисткой и восстановлением" if clear_existing else "восстановлением"
    progress_msg = await callback.message.edit_text(
        f"📥 <b>Восстановление из загруженного файла...</b>\n\n"
        f"⏳ Работаем с {action_text} данных...\n"
        f"Это может занять несколько минут.",
        parse_mode="HTML"
    )
    
    try:
        # Выполняем восстановление
        success, message = await backup_service.restore_backup(
            str(temp_path),
            clear_existing=clear_existing
        )
        
        # Удаляем временный файл
        try:
            temp_path.unlink()
        except:
            pass
        
        if success:
            await progress_msg.edit_text(
                f"✅ <b>Восстановление завершено!</b>\n\n{message}",
                parse_mode="HTML",
                reply_markup=get_backup_main_keyboard(db_user.language)
            )
        else:
            await progress_msg.edit_text(
                f"❌ <b>Ошибка восстановления</b>\n\n{message}",
                parse_mode="HTML",
                reply_markup=get_backup_main_keyboard(db_user.language)
            )
    
    except Exception as e:
        # Удаляем временный файл при ошибке
        try:
            temp_path.unlink()
        except:
            pass
        
        await progress_msg.edit_text(
            f"❌ <b>Ошибка восстановления</b>\n\n{str(e)}",
            parse_mode="HTML",
            reply_markup=get_backup_main_keyboard(db_user.language)
        )


@admin_required
@error_handler
async def show_backup_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Показывает настройки бекапов"""
    settings_obj = await backup_service.get_backup_settings()
    
    text = f"""⚙️ <b>Настройки системы бекапов</b>

🔄 <b>Автоматические бекапы:</b>
• Статус: {'✅ Включены' if settings_obj.auto_backup_enabled else '❌ Отключены'}
• Интервал: {settings_obj.backup_interval_hours} часов
• Время запуска: {settings_obj.backup_time}

📦 <b>Хранение:</b>
• Максимум файлов: {settings_obj.max_backups_keep}
• Сжатие: {'✅ Включено' if settings_obj.compression_enabled else '❌ Отключено'}
• Включать логи: {'✅ Да' if settings_obj.include_logs else '❌ Нет'}

📁 <b>Расположение:</b> <code>{settings_obj.backup_location}</code>
"""
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_backup_settings_keyboard(settings_obj)
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_backup_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Переключает настройки бекапов"""
    settings_obj = await backup_service.get_backup_settings()
    
    if callback.data == "backup_toggle_auto":
        new_value = not settings_obj.auto_backup_enabled
        await backup_service.update_backup_settings(auto_backup_enabled=new_value)
        status = "включены" if new_value else "отключены"
        await callback.answer(f"Автобекапы {status}")
        
    elif callback.data == "backup_toggle_compression":
        new_value = not settings_obj.compression_enabled
        await backup_service.update_backup_settings(compression_enabled=new_value)
        status = "включено" if new_value else "отключено"
        await callback.answer(f"Сжатие {status}")
        
    elif callback.data == "backup_toggle_logs":
        new_value = not settings_obj.include_logs
        await backup_service.update_backup_settings(include_logs=new_value)
        status = "включены" if new_value else "отключены"
        await callback.answer(f"Логи в бекапе {status}")
    
    # Обновляем отображение настроек
    await show_backup_settings(callback, db_user, db)


def register_handlers(dp: Dispatcher):
    """Регистрирует обработчики бекапов"""
    
    # Главная панель
    dp.callback_query.register(
        show_backup_panel,
        F.data == "backup_panel"
    )
    
    # Создание бекапа
    dp.callback_query.register(
        create_backup_handler,
        F.data == "backup_create"
    )
    
    # Список бекапов
    dp.callback_query.register(
        show_backup_list,
        F.data.startswith("backup_list")
    )
    
    # Управление бекапом
    dp.callback_query.register(
        manage_backup_file,
        F.data.startswith("backup_manage_")
    )
    
    # Удаление бекапа
    dp.callback_query.register(
        delete_backup_confirm,
        F.data.startswith("backup_delete_") & ~F.data.startswith("backup_delete_confirm_")
    )
    
    dp.callback_query.register(
        delete_backup_execute,
        F.data.startswith("backup_delete_confirm_")
    )
    
    # Восстановление
    dp.callback_query.register(
        restore_backup_start,
        F.data.in_(["backup_restore"]) | F.data.startswith("backup_restore_file_")
    )
    
    dp.callback_query.register(
        restore_backup_execute,
        F.data.startswith("backup_restore_execute_") | F.data.startswith("backup_restore_clear_")
    )
    
    dp.callback_query.register(
        restore_uploaded_backup,
        F.data.startswith("backup_restore_uploaded_")
    )
    
    # Настройки
    dp.callback_query.register(
        show_backup_settings,
        F.data == "backup_settings"
    )
    
    dp.callback_query.register(
        toggle_backup_setting,
        F.data.in_(["backup_toggle_auto", "backup_toggle_compression", "backup_toggle_logs"])
    )
    
    # Загрузка файла
    dp.message.register(
        handle_backup_file_upload,
        BackupStates.waiting_backup_file
    )d=[
        [
            InlineKeyboardButton(text="🚀 Создать бекап", callback_data="backup_create"),
            InlineKeyboardButton(text="📥 Восстановить", callback_data="backup_restore")
        ],
        [
            InlineKeyboardButton(text="📋 Список бекапов", callback_data="backup_list"),
            InlineKeyboardButton(text="📊 Журнал операций", callback_data="backup_logs")
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="backup_settings"),
            InlineKeyboardButton(text="🔄 Автобекапы", callback_data="backup_auto_toggle")
        ],
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")
        ]
    ])


def get_backup_list_keyboard(backups: list, page: int = 1, per_page: int = 5):
    """Клавиатура со списком бекапов"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = []
    
    # Пагинация
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_backups = backups[start_idx:end_idx]
    
    for backup in page_backups:
        # Форматируем дату
        try:
            if backup.get("timestamp"):
                dt = datetime.fromisoformat(backup["timestamp"].replace('Z', '+00:00'))
                date_str = dt.strftime("%d.%m %H:%M")
            else:
                date_str = "?"
        except:
            date_str = "?"
        
        size_str = f"{backup.get('file_size_mb', 0):.1f}MB"
        records_str = backup.get('total_records', '?')
        
        button_text = f"📦 {date_str} • {size_str} • {records_str} записей"
        callback_data = f"backup_manage_{backup['filename']}"
        
        keyboard.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
    
    # Пагинация
    if len(backups) > per_page:
        total_pages = (len(backups) + per_page - 1) // per_page
        nav_row = []
        
        if page > 1:
            nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"backup_list_page_{page-1}"))
        
        nav_row.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
        
        if page < total_pages:
            nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"backup_list_page_{page+1}"))
        
        keyboard.append(nav_row)
    
    # Управляющие кнопки
    keyboard.extend([
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="backup_list")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="backup_panel")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboar
