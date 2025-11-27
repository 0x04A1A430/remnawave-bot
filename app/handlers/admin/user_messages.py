import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.user_message import (
    create_user_message, get_all_user_messages, get_user_message_by_id,
    update_user_message, delete_user_message, toggle_user_message_status,
    get_user_messages_stats
)
from app.database.models import User
from app.keyboards.admin import get_admin_main_keyboard
from app.utils.validators import (
    get_html_help_text,
    sanitize_html,
    validate_html_tags,
)
from app.utils.decorators import admin_required, error_handler
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


class UserMessageStates(StatesGroup):
    waiting_for_message_text = State()
    waiting_for_edit_text = State()


def get_user_messages_keyboard(language: str = "ru"):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📝 Добавить сообщение",
                callback_data="add_user_message"
            )
        ],
        [
            InlineKeyboardButton(
                text="📋 Список сообщений",
                callback_data="list_user_messages:0"
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Статистика",
                callback_data="user_messages_stats"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔙 Назад в админку",
                callback_data="admin_panel"
            )
        ]
    ])


def get_message_actions_keyboard(message_id: int, is_active: bool, language: str = "ru"):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    status_text = "🔴 Деактивировать" if is_active else "🟢 Активировать"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✏️ Редактировать",
                callback_data=f"edit_user_message:{message_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=status_text,
                callback_data=f"toggle_user_message:{message_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🗑️ Удалить",
                callback_data=f"delete_user_message:{message_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔙 К списку",
                callback_data="list_user_messages:0"
            )
        ]
    ])


@admin_required
@error_handler
async def show_user_messages_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    text = (
        "📢 <b>Управление сообщениями в главном меню</b>\n\n"
        "Здесь вы можете добавлять сообщения, которые будут показываться пользователям "
        "в главном меню между информацией о подписке и кнопками действий.\n\n"
        "• Сообщения поддерживают HTML теги\n"
        "• Можно создать несколько сообщений\n"
        "• Активные сообщения показываются случайно\n"
        "• Неактивные сообщения не показываются"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_user_messages_keyboard(db_user.language),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def add_user_message_start(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    await callback.message.edit_text(
        f"📝 <b>Добавление нового сообщения</b>\n\n"
        f"Введите текст сообщения, которое будет показываться в главном меню.\n\n"
        f"{get_html_help_text()}\n\n"
        f"Отправьте /cancel для отмены.",
        parse_mode="HTML"
    )
    
    await state.set_state(UserMessageStates.waiting_for_message_text)
    await callback.answer()


@admin_required
@error_handler
async def process_new_message_text(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    if message.text == "/cancel":
        await state.clear()
        await message.answer(
            "❌ Добавление сообщения отменено.",
            reply_markup=get_user_messages_keyboard(db_user.language)
        )
        return
    
    message_text = message.text.strip()
    
    if len(message_text) > 4000:
        await message.answer(
            "❌ Сообщение слишком длинное. Максимум 4000 символов.\n"
            "Попробуйте еще раз или отправьте /cancel для отмены."
        )
        return
    
    is_valid, error_msg = validate_html_tags(message_text)
    if not is_valid:
        await message.answer(
            f"❌ Ошибка в HTML разметке: {error_msg}\n\n"
            f"Исправьте ошибку и попробуйте еще раз, или отправьте /cancel для отмены.",
            parse_mode=None 
        )
        return
    
    try:
        new_message = await create_user_message(
            db=db,
            message_text=message_text,
            created_by=db_user.id,
            is_active=True
        )
        
        await state.clear()
        
        await message.answer(
            f"✅ <b>Сообщение добавлено!</b>\n\n"
            f"<b>ID:</b> {new_message.id}\n"
            f"<b>Статус:</b> {'🟢 Активно' if new_message.is_active else '🔴 Неактивно'}\n"
            f"<b>Создано:</b> {new_message.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"<b>Предварительный просмотр:</b>\n"
            f"<blockquote>{message_text}</blockquote>",
            reply_markup=get_user_messages_keyboard(db_user.language),
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка создания сообщения: {e}")
        await state.clear()
        await message.answer(
            "❌ Произошла ошибка при создании сообщения. Попробуйте еще раз.",
            reply_markup=get_user_messages_keyboard(db_user.language)
        )

@admin_required
@error_handler
async def list_user_messages(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    page = 0
    if ":" in callback.data:
        try:
            page = int(callback.data.split(":")[1])
        except (ValueError, IndexError):
            page = 0
    
    limit = 5
    offset = page * limit
    
    messages = await get_all_user_messages(db, offset=offset, limit=limit)
    
    if not messages:
        await callback.message.edit_text(
            "📋 <b>Список сообщений</b>\n\n"
            "Сообщений пока нет. Добавьте первое сообщение!",
            reply_markup=get_user_messages_keyboard(db_user.language),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    text = "📋 <b>Список сообщений</b>\n\n"
    
    for msg in messages:
        status_emoji = "🟢" if msg.is_active else "🔴"
        preview = msg.message_text[:100] + "..." if len(msg.message_text) > 100 else msg.message_text
        preview = preview.replace('<', '&lt;').replace('>', '&gt;')
        
        text += (
            f"{status_emoji} <b>ID {msg.id}</b>\n"
            f"{preview}\n"
            f"📅 {msg.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        )
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = []
    
    for msg in messages:
        status_emoji = "🟢" if msg.is_active else "🔴"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{status_emoji} ID {msg.id}",
                callback_data=f"view_user_message:{msg.id}"
            )
        ])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"list_user_messages:{page-1}"
            )
        )
    
    nav_buttons.append(
        InlineKeyboardButton(
            text="➕ Добавить",
            callback_data="add_user_message"
        )
    )
    
    if len(messages) == limit:  
        nav_buttons.append(
            InlineKeyboardButton(
                text="Вперед ➡️",
                callback_data=f"list_user_messages:{page+1}"
            )
        )
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="user_messages_panel"
        )
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def view_user_message(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    try:
        message_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сообщения", show_alert=True)
        return
    
    message = await get_user_message_by_id(db, message_id)

    if not message:
        await callback.answer("❌ Сообщение не найдено", show_alert=True)
        return

    safe_content = sanitize_html(message.message_text)

    status_text = "🟢 Активно" if message.is_active else "🔴 Неактивно"

    text = (
        f"📋 <b>Сообщение ID {message.id}</b>\n\n"
        f"<b>Статус:</b> {status_text}\n"
        f"<b>Создано:</b> {message.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"<b>Обновлено:</b> {message.updated_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"<b>Содержимое:</b>\n"
        f"<blockquote>{safe_content}</blockquote>"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_message_actions_keyboard(
            message_id, message.is_active, db_user.language
        ),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_message_status(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    try:
        message_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сообщения", show_alert=True)
        return
    
    message = await toggle_user_message_status(db, message_id)
    
    if not message:
        await callback.answer("❌ Сообщение не найдено", show_alert=True)
        return
    
    status_text = "активировано" if message.is_active else "деактивировано"
    await callback.answer(f"✅ Сообщение {status_text}")
    
    await view_user_message(callback, db_user, db)


@admin_required
@error_handler
async def delete_message_confirm(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Подтвердить удаление сообщения"""
    try:
        message_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сообщения", show_alert=True)
        return
    
    success = await delete_user_message(db, message_id)
    
    if success:
        await callback.answer("✅ Сообщение удалено")
        await list_user_messages(
            types.CallbackQuery(
                id=callback.id,
                from_user=callback.from_user,
                chat_instance=callback.chat_instance,
                data="list_user_messages:0",
                message=callback.message
            ),
            db_user,
            db
        )
    else:
        await callback.answer("❌ Ошибка удаления сообщения", show_alert=True)


@admin_required
@error_handler
async def show_messages_stats(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    stats = await get_user_messages_stats(db)
    
    text = (
        "📊 <b>Статистика сообщений</b>\n\n"
        f"📝 Всего сообщений: <b>{stats['total_messages']}</b>\n"
        f"🟢 Активных: <b>{stats['active_messages']}</b>\n"
        f"🔴 Неактивных: <b>{stats['inactive_messages']}</b>\n\n"
        "Активные сообщения показываются пользователям случайным образом "
        "в главном меню между информацией о подписке и кнопками действий."
    )
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔙 Назад",
                callback_data="user_messages_panel"
            )
        ]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@admin_required
@error_handler
async def edit_user_message_start(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    try:
        message_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сообщения", show_alert=True)
        return
    
    message = await get_user_message_by_id(db, message_id)
    
    if not message:
        await callback.answer("❌ Сообщение не найдено", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"✏️ <b>Редактирование сообщения ID {message.id}</b>\n\n"
        f"<b>Текущий текст:</b>\n"
        f"<blockquote>{sanitize_html(message.message_text)}</blockquote>\n\n"
        f"Введите новый текст сообщения или отправьте /cancel для отмены:",
        parse_mode="HTML"
    )
    
    await state.set_data({"editing_message_id": message_id})
    await state.set_state(UserMessageStates.waiting_for_edit_text)
    await callback.answer()

@admin_required
@error_handler
async def process_edit_message_text(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    if message.text == "/cancel":
        await state.clear()
        await message.answer(
            "❌ Редактирование отменено.",
            reply_markup=get_user_messages_keyboard(db_user.language)
        )
        return
    
    data = await state.get_data()
    message_id = data.get("editing_message_id")
    
    if not message_id:
        await state.clear()
        await message.answer("❌ Ошибка: ID сообщения не найден")
        return
    
    new_text = message.text.strip()

    if len(new_text) > 4000:
        await message.answer(
            "❌ Сообщение слишком длинное. Максимум 4000 символов.\n"
            "Попробуйте еще раз или отправьте /cancel для отмены."
        )
        return

    is_valid, error_msg = validate_html_tags(new_text)
    if not is_valid:
        await message.answer(
            f"❌ Ошибка в HTML разметке: {error_msg}\n\n"
            f"Исправьте ошибку и попробуйте еще раз, или отправьте /cancel для отмены.",
            parse_mode=None
        )
        return

    try:
        updated_message = await update_user_message(
            db=db,
            message_id=message_id,
            message_text=new_text
        )
        
        if updated_message:
            await state.clear()
            await message.answer(
                f"✅ <b>Сообщение обновлено!</b>\n\n"
                f"<b>ID:</b> {updated_message.id}\n"
                f"<b>Обновлено:</b> {updated_message.updated_at.strftime('%d.%m.%Y %H:%M')}\n\n"
                f"<b>Новый текст:</b>\n"
                f"<blockquote>{sanitize_html(new_text)}</blockquote>",
                reply_markup=get_user_messages_keyboard(db_user.language),
                parse_mode="HTML"
            )
        else:
            await state.clear()
            await message.answer(
                "❌ Сообщение не найдено или ошибка обновления.",
                reply_markup=get_user_messages_keyboard(db_user.language)
            )
        
    except Exception as e:
        logger.error(f"Ошибка обновления сообщения: {e}")
        await state.clear()
        await message.answer(
            "❌ Произошла ошибка при обновлении сообщения.",
            reply_markup=get_user_messages_keyboard(db_user.language)
        )


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_user_messages_panel,
        F.data == "user_messages_panel"
    )
    
    dp.callback_query.register(
        add_user_message_start,
        F.data == "add_user_message"
    )
    
    dp.message.register(
        process_new_message_text,
        StateFilter(UserMessageStates.waiting_for_message_text)
    )

    dp.callback_query.register(
        edit_user_message_start,
        F.data.startswith("edit_user_message:")
    )
    
    dp.message.register(
        process_edit_message_text,
        StateFilter(UserMessageStates.waiting_for_edit_text)
    )
    
    dp.callback_query.register(
        list_user_messages,
        F.data.startswith("list_user_messages")
    )
    
    dp.callback_query.register(
        view_user_message,
        F.data.startswith("view_user_message:")
    )
    
    dp.callback_query.register(
        toggle_message_status,
        F.data.startswith("toggle_user_message:")
    )
    
    dp.callback_query.register(
        delete_message_confirm,
        F.data.startswith("delete_user_message:")
    )
    
    dp.callback_query.register(
        show_messages_stats,
        F.data == "user_messages_stats"
    )
