import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.states import AdminStates
from app.database.models import User
from app.localization.texts import get_texts
from app.utils.decorators import admin_required, error_handler
from app.database.crud.rules import get_current_rules_content, create_or_update_rules

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_rules_management(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    text = """
📋 <b>Управление правилами сервиса</b>

Текущие правила показываются пользователям при регистрации и в главном меню.

Выберите действие:
"""
    
    keyboard = [
        [types.InlineKeyboardButton(text="📝 Редактировать правила", callback_data="admin_edit_rules")],
        [types.InlineKeyboardButton(text="👀 Просмотр правил", callback_data="admin_view_rules")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def view_current_rules(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    current_rules = await get_current_rules_content(db, db_user.language)
    
    await callback.message.edit_text(
        f"📋 <b>Текущие правила сервиса</b>\n\n{current_rules}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✏️ Редактировать", callback_data="admin_edit_rules")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_rules")]
        ])
    )
    await callback.answer()


@admin_required
@error_handler
async def start_edit_rules(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    current_rules = await get_current_rules_content(db, db_user.language)
    
    await callback.message.edit_text(
        "✏️ <b>Редактирование правил</b>\n\n"
        f"<b>Текущие правила:</b>\n{current_rules[:500]}{'...' if len(current_rules) > 500 else ''}\n\n"
        "Отправьте новый текст правил сервиса.\n\n"
        "<i>Поддерживается HTML разметка</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_rules")]
        ])
    )
    
    await state.set_state(AdminStates.editing_rules_page)
    await callback.answer()


@admin_required
@error_handler
async def process_rules_edit(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    new_rules = message.text
    
    if len(new_rules) > 4000:
        await message.answer("❌ Текст правил слишком длинный (максимум 4000 символов)")
        return
    
    await message.answer(
        f"📋 <b>Предварительный просмотр новых правил:</b>\n\n{new_rules}\n\n"
        f"⚠️ <b>Внимание!</b> Новые правила будут показываться всем пользователям.\n\n"
        f"Сохранить изменения?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="✅ Сохранить", callback_data="admin_save_rules"),
                types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_rules")
            ]
        ])
    )
    
    await state.update_data(new_rules=new_rules)


@admin_required
@error_handler
async def save_rules(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    data = await state.get_data()
    new_rules = data.get('new_rules')
    
    if not new_rules:
        await callback.answer("❌ Ошибка: текст правил не найден", show_alert=True)
        return
    
    try:
        await create_or_update_rules(
            db=db,
            content=new_rules,
            language=db_user.language
        )
        
        from app.localization.texts import clear_rules_cache
        clear_rules_cache()
        
        from app.localization.texts import refresh_rules_cache
        await refresh_rules_cache(db_user.language)
        
        await callback.message.edit_text(
            "✅ <b>Правила сервиса обновлены!</b>\n\n"
            "Новые правила сохранены в базе данных и будут показываться пользователям.\n\n"
            "Кеш правил очищен и обновлен.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📋 К правилам", callback_data="admin_rules")]
            ])
        )
        
        await state.clear()
        logger.info(f"Правила сервиса обновлены администратором {db_user.telegram_id}")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка сохранения правил: {e}")
        await callback.answer("❌ Ошибка сохранения правил", show_alert=True)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_rules_management, F.data == "admin_rules")
    dp.callback_query.register(view_current_rules, F.data == "admin_view_rules")
    dp.callback_query.register(start_edit_rules, F.data == "admin_edit_rules")
    dp.callback_query.register(save_rules, F.data == "admin_save_rules")
    
    dp.message.register(process_rules_edit, AdminStates.editing_rules_page)