import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.services.maintenance_service import maintenance_service
from app.keyboards.admin import get_maintenance_keyboard, get_admin_main_keyboard
from app.localization.texts import get_texts
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


class MaintenanceStates(StatesGroup):
    waiting_for_reason = State()


@admin_required
@error_handler
async def show_maintenance_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    """Показывает панель управления техработами"""
    texts = get_texts(db_user.language)
    
    status_info = maintenance_service.get_status_info()
    
    # Формируем информацию о статусе
    status_emoji = "🔧" if status_info["is_active"] else "✅"
    status_text = "Включен" if status_info["is_active"] else "Выключен"
    
    api_emoji = "✅" if status_info["api_status"] else "❌"
    api_text = "Доступно" if status_info["api_status"] else "Недоступно"
    
    monitoring_emoji = "🔄" if status_info["monitoring_active"] else "⏹️"
    monitoring_text = "Запущен" if status_info["monitoring_active"] else "Остановлен"
    
    # Информация о включении
    enabled_info = ""
    if status_info["is_active"] and status_info["enabled_at"]:
        enabled_time = status_info["enabled_at"].strftime("%d.%m.%Y %H:%M:%S")
        enabled_info = f"\n📅 <b>Включен:</b> {enabled_time}"
        if status_info["reason"]:
            enabled_info += f"\n📝 <b>Причина:</b> {status_info['reason']}"
    
    # Информация о последней проверке
    last_check_info = ""
    if status_info["last_check"]:
        last_check_time = status_info["last_check"].strftime("%H:%M:%S")
        last_check_info = f"\n🕐 <b>Последняя проверка:</b> {last_check_time}"
    
    # Информация о неудачных попытках
    failures_info = ""
    if status_info["consecutive_failures"] > 0:
        failures_info = f"\n⚠️ <b>Неудачных проверок подряд:</b> {status_info['consecutive_failures']}"
    
    message_text = f"""
🔧 <b>Режим технических работ</b>

{status_emoji} <b>Статус:</b> {status_text}
{api_emoji} <b>API RemnaWave:</b> {api_text}
{monitoring_emoji} <b>Мониторинг:</b> {monitoring_text}
⏱️ <b>Интервал проверки:</b> {status_info['check_interval']}с
🤖 <b>Автовключение:</b> {'Включено' if status_info['auto_enable_configured'] else 'Отключено'}
{enabled_info}
{last_check_info}
{failures_info}

ℹ️ <i>В режиме техработ обычные пользователи не могут использовать бота. Администраторы имеют полный доступ.</i>
"""
    
    await callback.message.edit_text(
        message_text,
        reply_markup=get_maintenance_keyboard(db_user.language, status_info["is_active"], status_info["monitoring_active"])
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_maintenance_mode(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    """Переключает режим техработ"""
    is_active = maintenance_service.is_maintenance_active()
    
    if is_active:
        # Выключаем техработы
        success = await maintenance_service.disable_maintenance()
        if success:
            await callback.answer("Режим техработ выключен", show_alert=True)
        else:
            await callback.answer("Ошибка выключения режима техработ", show_alert=True)
    else:
        # Включаем техработы - спрашиваем причину
        await state.set_state(MaintenanceStates.waiting_for_reason)
        await callback.message.edit_text(
            "🔧 <b>Включение режима техработ</b>\n\nВведите причину включения техработ или отправьте /skip для пропуска:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="❌ Отмена", callback_data="maintenance_panel")]
            ])
        )
    
    await callback.answer()


@admin_required
@error_handler
async def process_maintenance_reason(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    """Обрабатывает ввод причины техработ"""
    current_state = await state.get_state()
    
    if current_state != MaintenanceStates.waiting_for_reason:
        return
    
    reason = None
    if message.text and message.text != "/skip":
        reason = message.text[:200]  # Ограничиваем длину
    
    success = await maintenance_service.enable_maintenance(reason=reason, auto=False)
    
    if success:
        response_text = "Режим техработ включен"
        if reason:
            response_text += f"\nПричина: {reason}"
    else:
        response_text = "Ошибка включения режима техработ"
    
    await message.answer(response_text)
    await state.clear()
    
    # Показываем обновленную панель
    status_info = maintenance_service.get_status_info()
    await message.answer(
        "Вернуться к панели управления техработами:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔧 Панель техработ", callback_data="maintenance_panel")]
        ])
    )


@admin_required  
@error_handler
async def toggle_monitoring(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Переключает мониторинг API"""
    status_info = maintenance_service.get_status_info()
    
    if status_info["monitoring_active"]:
        success = await maintenance_service.stop_monitoring()
        message = "Мониторинг остановлен" if success else "Ошибка остановки мониторинга"
    else:
        success = await maintenance_service.start_monitoring()
        message = "Мониторинг запущен" if success else "Ошибка запуска мониторинга"
    
    await callback.answer(message, show_alert=True)
    
    # Обновляем панель
    await show_maintenance_panel(callback, db_user, db, None)


@admin_required
@error_handler
async def force_api_check(
    callback: types.CallbackQuery,
    db_user: User, 
    db: AsyncSession
):
    """Принудительная проверка API"""
    await callback.answer("Проверка API...", show_alert=False)
    
    check_result = await maintenance_service.force_api_check()
    
    if check_result["success"]:
        status_text = "доступно" if check_result["api_available"] else "недоступно"
        message = f"API {status_text}\nВремя ответа: {check_result['response_time']}с"
    else:
        message = f"Ошибка проверки: {check_result.get('error', 'Неизвестная ошибка')}"
    
    await callback.message.answer(message)
    
    # Обновляем панель
    await show_maintenance_panel(callback, db_user, db, None)


@admin_required
@error_handler
async def back_to_admin_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Возвращение в главную админку"""
    texts = get_texts(db_user.language)
    
    await callback.message.edit_text(
        texts.ADMIN_PANEL,
        reply_markup=get_admin_main_keyboard(db_user.language)
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    """Регистрирует обработчики техработ"""
    
    # Панель управления техработами
    dp.callback_query.register(
        show_maintenance_panel,
        F.data == "maintenance_panel"
    )
    
    # Переключение режима техработ
    dp.callback_query.register(
        toggle_maintenance_mode,
        F.data == "maintenance_toggle"
    )
    
    # Переключение мониторинга
    dp.callback_query.register(
        toggle_monitoring,
        F.data == "maintenance_monitoring"
    )
    
    # Принудительная проверка API
    dp.callback_query.register(
        force_api_check,
        F.data == "maintenance_check_api"
    )
    
    # Возврат в админку
    dp.callback_query.register(
        back_to_admin_panel,
        F.data == "admin_panel"
    )
    
    # Обработка ввода причины техработ
    dp.message.register(
        process_maintenance_reason,
        MaintenanceStates.waiting_for_reason
    )