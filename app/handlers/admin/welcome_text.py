import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.states import AdminStates
from app.keyboards.admin import get_welcome_text_keyboard, get_admin_main_keyboard
from app.utils.decorators import admin_required, error_handler
from app.database.crud.welcome_text import (
    get_active_welcome_text, 
    set_welcome_text, 
    get_current_welcome_text_or_default
)

logger = logging.getLogger(__name__)

@admin_required
@error_handler
async def show_welcome_text_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    await callback.message.edit_text(
        "👋 Управление приветственным текстом\n\n"
        "Здесь вы можете изменить текст, который показывается новым пользователям после регистрации.",
        reply_markup=get_welcome_text_keyboard(db_user.language)
    )
    await callback.answer()

@admin_required
@error_handler
async def show_current_welcome_text(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    current_text = await get_active_welcome_text(db)
    
    if not current_text:
        current_text = await get_current_welcome_text_or_default()
        status = "📝 Используется стандартный текст:"
    else:
        status = "📝 Текущий приветственный текст:"
    
    await callback.message.edit_text(
        f"{status}\n\n"
        f"<code>{current_text}</code>\n\n"
        f"💡 В тексте можно использовать имя пользователя - оно автоматически заменит 'Egor'",
        reply_markup=get_welcome_text_keyboard(db_user.language),
        parse_mode="HTML"
    )
    await callback.answer()

@admin_required
@error_handler
async def start_edit_welcome_text(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    current_text = await get_active_welcome_text(db)
    
    if not current_text:
        current_text = await get_current_welcome_text_or_default()
    
    await callback.message.edit_text(
        f"📝 Редактирование приветственного текста\n\n"
        f"Текущий текст:\n"
        f"<code>{current_text}</code>\n\n"
        f"Отправьте новый текст:",
        parse_mode="HTML"
    )
    
    await state.set_state(AdminStates.editing_welcome_text)
    await callback.answer()

@admin_required
@error_handler
async def process_welcome_text_edit(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    new_text = message.text.strip()
    
    if len(new_text) < 10:
        await message.answer("❌ Текст слишком короткий! Минимум 10 символов.")
        return
    
    if len(new_text) > 4000:
        await message.answer("❌ Текст слишком длинный! Максимум 4000 символов.")
        return
    
    success = await set_welcome_text(db, new_text, db_user.id)
    
    if success:
        await message.answer(
            f"✅ Приветственный текст успешно обновлен!\n\n"
            f"Новый текст:\n"
            f"<code>{new_text}</code>",
            reply_markup=get_welcome_text_keyboard(db_user.language),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "❌ Ошибка при сохранении текста. Попробуйте еще раз.",
            reply_markup=get_welcome_text_keyboard(db_user.language)
        )
    
    await state.clear()

@admin_required
@error_handler
async def reset_welcome_text(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    default_text = await get_current_welcome_text_or_default()
    success = await set_welcome_text(db, default_text, db_user.id)
    
    if success:
        await callback.message.edit_text(
            f"✅ Приветственный текст сброшен на стандартный!\n\n"
            f"Стандартный текст:\n"
            f"<code>{default_text}</code>",
            reply_markup=get_welcome_text_keyboard(db_user.language),
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(
            "❌ Ошибка при сбросе текста. Попробуйте еще раз.",
            reply_markup=get_welcome_text_keyboard(db_user.language)
        )
    
    await callback.answer()

def register_welcome_text_handlers(dp: Dispatcher):
    dp.callback_query.register(
        show_welcome_text_panel,
        F.data == "welcome_text_panel"
    )
    
    dp.callback_query.register(
        show_current_welcome_text,
        F.data == "show_welcome_text"
    )
    
    dp.callback_query.register(
        start_edit_welcome_text,
        F.data == "edit_welcome_text"
    )
    
    dp.callback_query.register(
        reset_welcome_text,
        F.data == "reset_welcome_text"
    )
    
    dp.message.register(
        process_welcome_text_edit,
        AdminStates.editing_welcome_text
    )
