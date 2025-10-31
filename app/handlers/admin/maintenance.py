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
    waiting_for_notification_message = State()


@admin_required
@error_handler
async def show_maintenance_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    status_info = maintenance_service.get_status_info()
    
    try:
        from app.services.remnawave_service import RemnaWaveService
        rw_service = RemnaWaveService()
        panel_status = await rw_service.get_panel_status_summary()
    except Exception as e:
        logger.error(f"Ошибка получения статуса панели: {e}")
        panel_status = {"description": "❓ Не удалось проверить", "has_issues": True}
    
    status_emoji = "🔧" if status_info["is_active"] else "✅"
    status_text = "Включен" if status_info["is_active"] else "Выключен"
    
    api_emoji = "✅" if status_info["api_status"] else "❌"
    api_text = "Доступно" if status_info["api_status"] else "Недоступно"
    
    monitoring_emoji = "🔄" if status_info["monitoring_active"] else "⏹️"
    monitoring_text = "Запущен" if status_info["monitoring_active"] else "Остановлен"
    
    enabled_info = ""
    if status_info["is_active"] and status_info["enabled_at"]:
        enabled_time = status_info["enabled_at"].strftime("%d.%m.%Y %H:%M:%S")
        enabled_info = f"\n📅 <b>Включен:</b> {enabled_time}"
        if status_info["reason"]:
            enabled_info += f"\n📝 <b>Причина:</b> {status_info['reason']}"
    
    last_check_info = ""
    if status_info["last_check"]:
        last_check_time = status_info["last_check"].strftime("%H:%M:%S")
        last_check_info = f"\n🕐 <b>Последняя проверка:</b> {last_check_time}"
    
    failures_info = ""
    if status_info["consecutive_failures"] > 0:
        failures_info = f"\n⚠️ <b>Неудачных проверок подряд:</b> {status_info['consecutive_failures']}"
    
    panel_info = f"\n🌐 <b>Панель Remnawave:</b> {panel_status['description']}"
    if panel_status.get("response_time"):
        panel_info += f"\n⚡ <b>Время отклика:</b> {panel_status['response_time']}с"
    
    message_text = f"""
🔧 <b>Управление техническими работами</b>

{status_emoji} <b>Режим техработ:</b> {status_text}
{api_emoji} <b>API Remnawave:</b> {api_text}
{monitoring_emoji} <b>Мониторинг:</b> {monitoring_text}
🛠️ <b>Автозапуск мониторинга:</b> {'Включен' if status_info['monitoring_configured'] else 'Отключен'}
⏱️ <b>Интервал проверки:</b> {status_info['check_interval']}с
🤖 <b>Автовключение:</b> {'Включено' if status_info['auto_enable_configured'] else 'Отключено'}
{panel_info}
{enabled_info}
{last_check_info}
{failures_info}

ℹ️ <i>В режиме техработ обычные пользователи не могут использовать бота. Администраторы имеют полный доступ.</i>
"""
    
    await callback.message.edit_text(
        message_text,
        reply_markup=get_maintenance_keyboard(
            db_user.language, 
            status_info["is_active"], 
            status_info["monitoring_active"],
            panel_status.get("has_issues", False)
        )
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
    is_active = maintenance_service.is_maintenance_active()
    
    if is_active:
        success = await maintenance_service.disable_maintenance()
        if success:
            await callback.answer("Режим техработ выключен", show_alert=True)
        else:
            await callback.answer("Ошибка выключения режима техработ", show_alert=True)
    else:
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
    current_state = await state.get_state()
    
    if current_state != MaintenanceStates.waiting_for_reason:
        return
    
    reason = None
    if message.text and message.text != "/skip":
        reason = message.text[:200] 
    
    success = await maintenance_service.enable_maintenance(reason=reason, auto=False)
    
    if success:
        response_text = "Режим техработ включен"
        if reason:
            response_text += f"\nПричина: {reason}"
    else:
        response_text = "Ошибка включения режима техработ"
    
    await message.answer(response_text)
    await state.clear()
    
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
    status_info = maintenance_service.get_status_info()
    
    if status_info["monitoring_active"]:
        success = await maintenance_service.stop_monitoring()
        message = "Мониторинг остановлен" if success else "Ошибка остановки мониторинга"
    else:
        success = await maintenance_service.start_monitoring()
        message = "Мониторинг запущен" if success else "Ошибка запуска мониторинга"
    
    await callback.answer(message, show_alert=True)
    
    await show_maintenance_panel(callback, db_user, db, None)


@admin_required
@error_handler
async def force_api_check(
    callback: types.CallbackQuery,
    db_user: User, 
    db: AsyncSession
):
    await callback.answer("Проверка API...", show_alert=False)
    
    check_result = await maintenance_service.force_api_check()
    
    if check_result["success"]:
        status_text = "доступно" if check_result["api_available"] else "недоступно"
        message = f"API {status_text}\nВремя ответа: {check_result['response_time']}с"
    else:
        message = f"Ошибка проверки: {check_result.get('error', 'Неизвестная ошибка')}"
    
    await callback.message.answer(message)
    
    await show_maintenance_panel(callback, db_user, db, None)


@admin_required
@error_handler
async def check_panel_status(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    await callback.answer("Проверка статуса панели...", show_alert=False)
    
    try:
        from app.services.remnawave_service import RemnaWaveService
        rw_service = RemnaWaveService()
        
        status_data = await rw_service.check_panel_health()
        
        status_text = {
            "online": "🟢 Панель работает нормально",
            "offline": "🔴 Панель недоступна", 
            "degraded": "🟡 Панель работает со сбоями"
        }.get(status_data["status"], "❓ Статус неизвестен")
        
        message_parts = [
            f"🌐 <b>Статус панели Remnawave</b>\n",
            f"{status_text}",
            f"⚡ Время отклика: {status_data.get('response_time', 0)}с",
            f"👥 Пользователей онлайн: {status_data.get('users_online', 0)}",
            f"🖥️ Нод онлайн: {status_data.get('nodes_online', 0)}/{status_data.get('total_nodes', 0)}"
        ]

        attempts_used = status_data.get("attempts_used")
        if attempts_used:
            message_parts.append(f"🔁 Попыток проверки: {attempts_used}")

        if status_data.get("api_error"):
            message_parts.append(f"❌ Ошибка: {status_data['api_error'][:100]}")
        
        message = "\n".join(message_parts)
        
        await callback.message.answer(message, parse_mode="HTML")
        
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка проверки статуса: {str(e)}")


@admin_required
@error_handler
async def send_manual_notification(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    await state.set_state(MaintenanceStates.waiting_for_notification_message)
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="🟢 Онлайн", callback_data="manual_notify_online"),
            types.InlineKeyboardButton(text="🔴 Офлайн", callback_data="manual_notify_offline")
        ],
        [
            types.InlineKeyboardButton(text="🟡 Проблемы", callback_data="manual_notify_degraded"),
            types.InlineKeyboardButton(text="🔧 Обслуживание", callback_data="manual_notify_maintenance")
        ],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="maintenance_panel")]
    ])
    
    await callback.message.edit_text(
        "📢 <b>Ручная отправка уведомления</b>\n\nВыберите статус для уведомления:",
        reply_markup=keyboard
    )


@admin_required
@error_handler
async def handle_manual_notification(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    status_map = {
        "manual_notify_online": "online",
        "manual_notify_offline": "offline", 
        "manual_notify_degraded": "degraded",
        "manual_notify_maintenance": "maintenance"
    }
    
    status = status_map.get(callback.data)
    if not status:
        await callback.answer("Неизвестный статус")
        return
    
    await state.update_data(notification_status=status)
    
    status_names = {
        "online": "🟢 Онлайн",
        "offline": "🔴 Офлайн",
        "degraded": "🟡 Проблемы", 
        "maintenance": "🔧 Обслуживание"
    }
    
    await callback.message.edit_text(
        f"📢 <b>Отправка уведомления: {status_names[status]}</b>\n\n"
        f"Введите сообщение для уведомления или отправьте /skip для отправки без дополнительного текста:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data="maintenance_panel")]
        ])
    )


@admin_required
@error_handler
async def process_notification_message(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    current_state = await state.get_state()
    
    if current_state != MaintenanceStates.waiting_for_notification_message:
        return
    
    data = await state.get_data()
    status = data.get("notification_status")
    
    if not status:
        await message.answer("Ошибка: статус не выбран")
        await state.clear()
        return
    
    notification_message = ""
    if message.text and message.text != "/skip":
        notification_message = message.text[:300]
    
    try:
        from app.services.remnawave_service import RemnaWaveService
        rw_service = RemnaWaveService()
        
        success = await rw_service.send_manual_status_notification(
            message.bot, 
            status, 
            notification_message
        )
        
        if success:
            await message.answer("✅ Уведомление отправлено")
        else:
            await message.answer("❌ Ошибка отправки уведомления")
            
    except Exception as e:
        logger.error(f"Ошибка отправки ручного уведомления: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
    
    await state.clear()
    
    await message.answer(
        "Вернуться к панели техработ:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔧 Панель техработ", callback_data="maintenance_panel")]
        ])
    )


@admin_required
@error_handler
async def back_to_admin_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    await callback.message.edit_text(
        texts.ADMIN_PANEL,
        reply_markup=get_admin_main_keyboard(db_user.language)
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_maintenance_panel,
        F.data == "maintenance_panel"
    )
    
    dp.callback_query.register(
        toggle_maintenance_mode,
        F.data == "maintenance_toggle"
    )
    
    dp.callback_query.register(
        toggle_monitoring,
        F.data == "maintenance_monitoring"
    )
    
    dp.callback_query.register(
        force_api_check,
        F.data == "maintenance_check_api"
    )
    
    dp.callback_query.register(
        check_panel_status,
        F.data == "maintenance_check_panel"
    )
    
    dp.callback_query.register(
        send_manual_notification,
        F.data == "maintenance_manual_notify"
    )
    
    dp.callback_query.register(
        handle_manual_notification,
        F.data.startswith("manual_notify_")
    )
    
    dp.callback_query.register(
        back_to_admin_panel,
        F.data == "admin_panel"
    )
    
    dp.message.register(
        process_maintenance_reason,
        MaintenanceStates.waiting_for_reason
    )
    
    dp.message.register(
        process_notification_message,
        MaintenanceStates.waiting_for_notification_message
    )
