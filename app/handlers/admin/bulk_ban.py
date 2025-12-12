"""
Обработчики команд для массовой блокировки пользователей
"""
import logging
from aiogram import types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database.models import User
from app.services.bulk_ban_service import bulk_ban_service
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler
from app.keyboards.admin import get_admin_users_keyboard

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def start_bulk_ban_process(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    """
    Начало процесса массовой блокировки пользователей
    """
    await callback.message.edit_text(
        "🛑 <b>Массовая блокировка пользователей</b>\n\n"
        "Введите список Telegram ID для блокировки.\n\n"
        "<b>Форматы ввода:</b>\n"
        "• По одному ID на строку\n"
        "• Через запятую\n"
        "• Через пробел\n\n"
        "Пример:\n"
        "<code>123456789\n"
        "987654321\n"
        "111222333</code>\n\n"
        "Или:\n"
        "<code>123456789, 987654321, 111222333</code>\n\n"
        "Для отмены используйте команду /cancel",
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_users")]
        ])
    )
    
    await state.set_state(AdminStates.waiting_for_bulk_ban_list)
    await callback.answer()


@admin_required
@error_handler
async def process_bulk_ban_list(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    """
    Обработка списка Telegram ID и выполнение массовой блокировки
    """
    if not message.text:
        await message.answer(
            "❌ Отправьте текстовое сообщение со списком Telegram ID",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
            ])
        )
        return

    input_text = message.text.strip()

    if not input_text:
        await message.answer(
            "❌ Введите корректный список Telegram ID",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
            ])
        )
        return
    
    # Парсим ID из текста
    try:
        telegram_ids = await bulk_ban_service.parse_telegram_ids_from_text(input_text)
    except Exception as e:
        logger.error(f"Ошибка парсинга Telegram ID: {e}")
        await message.answer(
            "❌ Ошибка при обработке списка ID. Проверьте формат ввода.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
            ])
        )
        return
    
    if not telegram_ids:
        await message.answer(
            "❌ Не найдено корректных Telegram ID в списке",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
            ])
        )
        return
    
    if len(telegram_ids) > 1000:  # Ограничение на количество ID за раз
        await message.answer(
            f"❌ Слишком много ID в списке ({len(telegram_ids)}). Максимум: 1000",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
            ])
        )
        return
    
    # Выполняем массовую блокировку
    try:
        successfully_banned, not_found, error_ids = await bulk_ban_service.ban_users_by_telegram_ids(
            db=db,
            admin_user_id=db_user.id,
            telegram_ids=telegram_ids,
            reason="Массовая блокировка администратором",
            bot=message.bot,
            notify_admin=True,
            admin_name=db_user.full_name
        )
        
        # Подготавливаем сообщение с результатами
        result_text = f"✅ <b>Массовая блокировка завершена</b>\n\n"
        result_text += f"📊 <b>Результаты:</b>\n"
        result_text += f"✅ Успешно заблокировано: {successfully_banned}\n"
        result_text += f"❌ Не найдено: {not_found}\n"
        result_text += f"💥 Ошибок: {len(error_ids)}\n\n"
        result_text += f"📈 Всего обработано: {len(telegram_ids)}"
        
        if successfully_banned > 0:
            result_text += f"\n🎯 Процент успеха: {round((successfully_banned/len(telegram_ids))*100, 1)}%"
        
        # Добавляем информацию об ошибках, если есть
        if error_ids:
            result_text += f"\n\n⚠️ <b>Telegram ID с ошибками:</b>\n"
            result_text += f"<code>{', '.join(map(str, error_ids[:10]))}</code>"  # Показываем первые 10
            if len(error_ids) > 10:
                result_text += f" и еще {len(error_ids) - 10}..."
        
        await message.answer(
            result_text,
            parse_mode="HTML",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="👥 К пользователям", callback_data="admin_users")]
            ])
        )
        
    except Exception as e:
        logger.error(f"Ошибка при выполнении массовой блокировки: {e}")
        await message.answer(
            "❌ Произошла ошибка при выполнении массовой блокировки",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
            ])
        )
    
    await state.clear()


def register_bulk_ban_handlers(dp):
    """
    Регистрация обработчиков команд для массовой блокировки
    """
    # Обработчик команды начала массовой блокировки
    dp.callback_query.register(
        start_bulk_ban_process,
        lambda c: c.data == "admin_bulk_ban_start"
    )
    
    # Обработчик текстового сообщения с ID для блокировки
    dp.message.register(
        process_bulk_ban_list,
        AdminStates.waiting_for_bulk_ban_list
    )
