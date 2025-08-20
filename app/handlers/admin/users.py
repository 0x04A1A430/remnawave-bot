import logging
from datetime import datetime
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.states import AdminStates
from app.database.models import User, UserStatus, Subscription 
from app.database.crud.user import get_user_by_id 
from app.keyboards.admin import (
    get_admin_users_keyboard, get_user_management_keyboard,
    get_admin_pagination_keyboard, get_confirmation_keyboard
)
from app.localization.texts import get_texts
from app.services.user_service import UserService
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_datetime, format_time_ago

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_users_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    user_service = UserService()
    stats = await user_service.get_user_statistics(db)
    
    text = f"""
👥 <b>Управление пользователями</b>

📊 <b>Статистика:</b>
• Всего: {stats['total_users']}
• Активных: {stats['active_users']}
• Заблокированных: {stats['blocked_users']}

📈 <b>Новые пользователи:</b>
• Сегодня: {stats['new_today']}
• За неделю: {stats['new_week']}
• За месяц: {stats['new_month']}

Выберите действие:
"""
    
    await callback.message.edit_text(
        text,
        reply_markup=get_admin_users_keyboard(db_user.language)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_users_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    page: int = 1
):
    
    user_service = UserService()
    users_data = await user_service.get_users_page(db, page=page, limit=10)
    
    if not users_data["users"]:
        await callback.message.edit_text(
            "👥 Пользователи не найдены",
            reply_markup=get_admin_users_keyboard(db_user.language)
        )
        await callback.answer()
        return
    
    text = f"👥 <b>Список пользователей</b> (стр. {page}/{users_data['total_pages']})\n\n"
    
    for user in users_data["users"]:
        status_emoji = "✅" if user.status == UserStatus.ACTIVE.value else "❌"
        subscription_info = ""
        
        if user.subscription:
            if user.subscription.is_trial:
                subscription_info = "🎁"
            elif user.subscription.is_active:
                subscription_info = "💎"
            else:
                subscription_info = "⏰"
        
        text += f"{status_emoji} {subscription_info} <b>{user.full_name}</b>\n"
        text += f"🆔 <code>{user.telegram_id}</code>\n"
        text += f"💰 {settings.format_price(user.balance_kopeks)}\n"
        text += f"📅 {format_time_ago(user.created_at)}\n\n"
    
    keyboard = []
    
    if users_data["total_pages"] > 1:
        pagination_row = get_admin_pagination_keyboard(
            users_data["current_page"],
            users_data["total_pages"],
            "admin_users_list",
            "admin_users",
            db_user.language
        ).inline_keyboard[0]
        keyboard.append(pagination_row)
    
    keyboard.extend([
        [
            types.InlineKeyboardButton(text="🔍 Поиск", callback_data="admin_users_search"),
            types.InlineKeyboardButton(text="📊 Статистика", callback_data="admin_users_stats")
        ],
        [
            types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")
        ]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_users_list_pagination_fixed(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    try:
        callback_parts = callback.data.split('_')
        page = int(callback_parts[-1]) 
        await show_users_list(callback, db_user, db, page)
    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка парсинга номера страницы: {e}")
        await show_users_list(callback, db_user, db, 1)


@admin_required
@error_handler
async def start_user_search(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    
    await callback.message.edit_text(
        "🔍 <b>Поиск пользователя</b>\n\n"
        "Введите для поиска:\n"
        "• Telegram ID\n"
        "• Username (без @)\n"
        "• Имя или фамилию\n\n"
        "Или нажмите /cancel для отмены",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_users")]
        ])
    )
    
    await state.set_state(AdminStates.waiting_for_user_search)
    await callback.answer()

@admin_required
@error_handler
async def show_users_statistics(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    user_service = UserService()
    stats = await user_service.get_user_statistics(db)
    
    from sqlalchemy import select, func, and_
    
    with_sub_result = await db.execute(
        select(func.count(User.id))
        .join(Subscription)
        .where(
            and_(
                User.status == UserStatus.ACTIVE.value,
                Subscription.is_active == True
            )
        )
    )
    users_with_subscription = with_sub_result.scalar() or 0
    
    trial_result = await db.execute(
        select(func.count(User.id))
        .join(Subscription)
        .where(
            and_(
                User.status == UserStatus.ACTIVE.value,
                Subscription.is_trial == True,
                Subscription.is_active == True
            )
        )
    )
    trial_users = trial_result.scalar() or 0
    
    avg_balance_result = await db.execute(
        select(func.avg(User.balance_kopeks))
        .where(User.status == UserStatus.ACTIVE.value)
    )
    avg_balance = avg_balance_result.scalar() or 0
    
    text = f"""
📊 <b>Детальная статистика пользователей</b>

👥 <b>Общие показатели:</b>
• Всего: {stats['total_users']}
• Активных: {stats['active_users']}
• Заблокированных: {stats['blocked_users']}

📱 <b>Подписки:</b>
• С активной подпиской: {users_with_subscription}
• На триале: {trial_users}
• Без подписки: {stats['active_users'] - users_with_subscription}

💰 <b>Финансы:</b>
• Средний баланс: {settings.format_price(int(avg_balance))}

📈 <b>Регистрации:</b>
• Сегодня: {stats['new_today']}
• За неделю: {stats['new_week']}
• За месяц: {stats['new_month']}

📊 <b>Активность:</b>
• Конверсия в подписку: {(users_with_subscription / max(stats['active_users'], 1) * 100):.1f}%
• Доля триальных: {(trial_users / max(users_with_subscription, 1) * 100):.1f}%
"""
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_users_stats")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")]
        ])
    )
    await callback.answer()


@admin_required
@error_handler
async def show_user_subscription(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    user_id = int(callback.data.split('_')[-1])
    
    user_service = UserService()
    profile = await user_service.get_user_profile(db, user_id)
    
    if not profile:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    user = profile["user"]
    subscription = profile["subscription"]
    
    text = f"📱 <b>Подписка пользователя</b>\n\n"
    text += f"👤 {user.full_name} (ID: <code>{user.telegram_id}</code>)\n\n"
    
    if subscription:
        status_emoji = "✅" if subscription.is_active else "❌"
        type_emoji = "🎁" if subscription.is_trial else "💎"
        
        text += f"<b>Статус:</b> {status_emoji} {'Активна' if subscription.is_active else 'Неактивна'}\n"
        text += f"<b>Тип:</b> {type_emoji} {'Триал' if subscription.is_trial else 'Платная'}\n"
        text += f"<b>Начало:</b> {format_datetime(subscription.start_date)}\n"
        text += f"<b>Окончание:</b> {format_datetime(subscription.end_date)}\n"
        text += f"<b>Трафик:</b> {subscription.traffic_used_gb:.1f}/{subscription.traffic_limit_gb} ГБ\n"
        text += f"<b>Устройства:</b> {subscription.device_limit}\n"
        text += f"<b>Подключенных устройств:</b> {len(subscription.connected_devices) if subscription.connected_devices else 0}\n"
        
        if subscription.is_active:
            days_left = (subscription.end_date - datetime.utcnow()).days
            text += f"<b>Осталось дней:</b> {days_left}\n"
        
        keyboard = [
            [
                types.InlineKeyboardButton(
                    text="⏰ Продлить", 
                    callback_data=f"admin_sub_extend_{user_id}"
                ),
                types.InlineKeyboardButton(
                    text="📊 Трафик", 
                    callback_data=f"admin_sub_traffic_{user_id}"
                )
            ]
        ]
        
        if subscription.is_active:
            keyboard.append([
                types.InlineKeyboardButton(
                    text="🚫 Деактивировать", 
                    callback_data=f"admin_sub_deactivate_{user_id}"
                )
            ])
        else:
            keyboard.append([
                types.InlineKeyboardButton(
                    text="✅ Активировать", 
                    callback_data=f"admin_sub_activate_{user_id}"
                )
            ])
    else:
        text += "❌ <b>Подписка отсутствует</b>\n\n"
        text += "Пользователь еще не активировал подписку."
        
        keyboard = [
            [
                types.InlineKeyboardButton(
                    text="🎁 Выдать триал", 
                    callback_data=f"admin_sub_grant_trial_{user_id}"
                ),
                types.InlineKeyboardButton(
                    text="💎 Выдать подписку", 
                    callback_data=f"admin_sub_grant_{user_id}"
                )
            ]
        ]
    
    keyboard.append([
        types.InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"admin_user_manage_{user_id}")
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_user_transactions(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    user_id = int(callback.data.split('_')[-1])
    
    from app.database.crud.transaction import get_user_transactions
    
    user = await get_user_by_id(db, user_id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    transactions = await get_user_transactions(db, user_id, limit=10)
    
    text = f"💳 <b>Транзакции пользователя</b>\n\n"
    text += f"👤 {user.full_name} (ID: <code>{user.telegram_id}</code>)\n"
    text += f"💰 Текущий баланс: {settings.format_price(user.balance_kopeks)}\n\n"
    
    if transactions:
        text += "<b>Последние транзакции:</b>\n\n"
        
        for transaction in transactions:
            type_emoji = "📈" if transaction.amount_kopeks > 0 else "📉"
            text += f"{type_emoji} {settings.format_price(abs(transaction.amount_kopeks))}\n"
            text += f"📋 {transaction.description}\n"
            text += f"📅 {format_datetime(transaction.created_at)}\n\n"
    else:
        text += "📭 <b>Транзакции отсутствуют</b>"
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"admin_user_manage_{user_id}")]
        ])
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_user_delete(
    callback: types.CallbackQuery,
    db_user: User
):
    
    user_id = int(callback.data.split('_')[-1])
    
    await callback.message.edit_text(
        "🗑️ <b>Удаление пользователя</b>\n\n"
        "⚠️ <b>ВНИМАНИЕ!</b>\n"
        "Вы уверены, что хотите удалить этого пользователя?\n\n"
        "Это действие:\n"
        "• Пометит пользователя как удаленного\n"
        "• Деактивирует его подписку\n"
        "• Заблокирует доступ к боту\n\n"
        "Данное действие необратимо!",
        reply_markup=get_confirmation_keyboard(
            f"admin_user_delete_confirm_{user_id}",
            f"admin_user_manage_{user_id}",
            db_user.language
        )
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_user_account(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    user_id = int(callback.data.split('_')[-1])
    
    user_service = UserService()
    success = await user_service.delete_user_account(db, user_id, db_user.id)
    
    if success:
        await callback.message.edit_text(
            "✅ Пользователь успешно удален",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="👥 К списку пользователей", callback_data="admin_users_list")]
            ])
        )
    else:
        await callback.message.edit_text(
            "❌ Ошибка удаления пользователя",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="👤 К пользователю", callback_data=f"admin_user_manage_{user_id}")]
            ])
        )
    
    await callback.answer()


@admin_required
@error_handler
async def process_user_search(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    
    query = message.text.strip()
    
    if not query:
        await message.answer("❌ Введите корректный запрос для поиска")
        return
    
    user_service = UserService()
    search_results = await user_service.search_users(db, query, page=1, limit=10)
    
    if not search_results["users"]:
        await message.answer(
            f"🔍 По запросу '<b>{query}</b>' ничего не найдено",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")]
            ])
        )
        await state.clear()
        return
    
    text = f"🔍 <b>Результаты поиска:</b> '{query}'\n\n"
    keyboard = []
    
    for user in search_results["users"]:
        status_emoji = "✅" if user.status == UserStatus.ACTIVE.value else "❌"
        subscription_info = ""
        
        if user.subscription:
            if user.subscription.is_trial:
                subscription_info = "🎁"
            elif user.subscription.is_active:
                subscription_info = "💎"
            else:
                subscription_info = "⏰"
        
        text += f"{status_emoji} {subscription_info} <b>{user.full_name}</b>\n"
        text += f"🆔 <code>{user.telegram_id}</code>\n"
        text += f"💰 {settings.format_price(user.balance_kopeks)}\n\n"
        
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"👤 {user.full_name}",
                callback_data=f"admin_user_manage_{user.id}"
            )
        ])
    
    keyboard.append([
        types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")
    ])
    
    await message.answer(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await state.clear()


@admin_required
@error_handler
async def show_user_management(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    user_id = int(callback.data.split('_')[-1])
    
    user_service = UserService()
    profile = await user_service.get_user_profile(db, user_id)
    
    if not profile:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    user = profile["user"]
    subscription = profile["subscription"]
    
    status_text = "✅ Активен" if user.status == UserStatus.ACTIVE.value else "❌ Заблокирован"
    
    text = f"""
👤 <b>Управление пользователем</b>

<b>Основная информация:</b>
• Имя: {user.full_name}
• ID: <code>{user.telegram_id}</code>
• Username: @{user.username or 'не указан'}
• Статус: {status_text}
• Язык: {user.language}

<b>Финансы:</b>
• Баланс: {settings.format_price(user.balance_kopeks)}
• Транзакций: {profile['transactions_count']}

<b>Активность:</b>
• Регистрация: {format_datetime(user.created_at)}
• Последняя активность: {format_time_ago(user.last_activity) if user.last_activity else 'Неизвестно'}
• Дней с регистрации: {profile['registration_days']}
"""
    
    if subscription:
        text += f"""
<b>Подписка:</b>
• Тип: {'🎁 Триал' if subscription.is_trial else '💎 Платная'}
• Статус: {'✅ Активна' if subscription.is_active else '❌ Неактивна'}
• До: {format_datetime(subscription.end_date)}
• Трафик: {subscription.traffic_used_gb:.1f}/{subscription.traffic_limit_gb} ГБ
• Устройства: {subscription.device_limit}
• Стран: {len(subscription.connected_squads)}
"""
    else:
        text += "\n<b>Подписка:</b> Отсутствует"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_user_management_keyboard(user.id, db_user.language)
    )
    await callback.answer()


@admin_required
@error_handler
async def start_balance_edit(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    
    user_id = int(callback.data.split('_')[-1])
    
    await state.update_data(editing_user_id=user_id)
    
    await callback.message.edit_text(
        "💰 <b>Изменение баланса</b>\n\n"
        "Введите сумму для изменения баланса:\n"
        "• Положительное число для пополнения\n"
        "• Отрицательное число для списания\n"
        "• Примеры: 100, -50, 25.5\n\n"
        "Или нажмите /cancel для отмены",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_user_manage_{user_id}")]
        ])
    )
    
    await state.set_state(AdminStates.editing_user_balance)
    await callback.answer()


@admin_required
@error_handler
async def process_balance_edit(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    
    data = await state.get_data()
    user_id = data.get("editing_user_id")
    
    if not user_id:
        await message.answer("❌ Ошибка: пользователь не найден")
        await state.clear()
        return
    
    try:
        amount_rubles = float(message.text.replace(',', '.'))
        amount_kopeks = int(amount_rubles * 100)
        
        if abs(amount_kopeks) > 10000000: 
            await message.answer("❌ Слишком большая сумма (максимум 100,000 ₽)")
            return
        
        user_service = UserService()
        
        description = f"Изменение баланса администратором {db_user.full_name}"
        if amount_kopeks > 0:
            description = f"Пополнение администратором: +{amount_rubles} ₽"
        else:
            description = f"Списание администратором: {amount_rubles} ₽"
        
        success = await user_service.update_user_balance(
            db, user_id, amount_kopeks, description, db_user.id
        )
        
        if success:
            action = "пополнен" if amount_kopeks > 0 else "списан"
            await message.answer(
                f"✅ Баланс пользователя {action} на {settings.format_price(abs(amount_kopeks))}",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="👤 К пользователю", callback_data=f"admin_user_manage_{user_id}")]
                ])
            )
        else:
            await message.answer("❌ Ошибка изменения баланса (возможно, недостаточно средств для списания)")
        
    except ValueError:
        await message.answer("❌ Введите корректную сумму (например: 100 или -50)")
        return
    
    await state.clear()


@admin_required
@error_handler
async def confirm_user_block(
    callback: types.CallbackQuery,
    db_user: User
):
    
    user_id = int(callback.data.split('_')[-1])
    
    await callback.message.edit_text(
        "🚫 <b>Блокировка пользователя</b>\n\n"
        "Вы уверены, что хотите заблокировать этого пользователя?\n"
        "Пользователь потеряет доступ к боту.",
        reply_markup=get_confirmation_keyboard(
            f"admin_user_block_confirm_{user_id}",
            f"admin_user_manage_{user_id}",
            db_user.language
        )
    )
    await callback.answer()


@admin_required
@error_handler
async def block_user(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    user_id = int(callback.data.split('_')[-1])
    
    user_service = UserService()
    success = await user_service.block_user(
        db, user_id, db_user.id, "Заблокирован администратором"
    )
    
    if success:
        await callback.message.edit_text(
            "✅ Пользователь заблокирован",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="👤 К пользователю", callback_data=f"admin_user_manage_{user_id}")]
            ])
        )
    else:
        await callback.message.edit_text(
            "❌ Ошибка блокировки пользователя",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="👤 К пользователю", callback_data=f"admin_user_manage_{user_id}")]
            ])
        )
    
    await callback.answer()


@admin_required
@error_handler
async def show_inactive_users(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    user_service = UserService()
    
    from app.database.crud.user import get_inactive_users
    inactive_users = await get_inactive_users(db, settings.INACTIVE_USER_DELETE_MONTHS)
    
    if not inactive_users:
        await callback.message.edit_text(
            f"✅ Неактивных пользователей (более {settings.INACTIVE_USER_DELETE_MONTHS} месяцев) не найдено",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")]
            ])
        )
        await callback.answer()
        return
    
    text = f"🗑️ <b>Неактивные пользователи</b>\n"
    text += f"Без активности более {settings.INACTIVE_USER_DELETE_MONTHS} месяцев: {len(inactive_users)}\n\n"
    
    for user in inactive_users[:10]: 
        text += f"👤 {user.full_name}\n"
        text += f"🆔 <code>{user.telegram_id}</code>\n"
        text += f"📅 {format_time_ago(user.last_activity) if user.last_activity else 'Никогда'}\n\n"
    
    if len(inactive_users) > 10:
        text += f"... и еще {len(inactive_users) - 10} пользователей"
    
    keyboard = [
        [types.InlineKeyboardButton(text="🗑️ Очистить всех", callback_data="admin_cleanup_inactive")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def cleanup_inactive_users(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    user_service = UserService()
    deleted_count = await user_service.cleanup_inactive_users(db)
    
    await callback.message.edit_text(
        f"✅ Очистка завершена\n\n"
        f"Удалено неактивных пользователей: {deleted_count}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")]
        ])
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_users_menu,
        F.data == "admin_users"
    )
    
    dp.callback_query.register(
        show_users_list,
        F.data == "admin_users_list"
    )
    
    dp.callback_query.register(
        show_users_statistics,
        F.data == "admin_users_stats"
    )
    
    dp.callback_query.register(
        show_user_subscription,
        F.data.startswith("admin_user_sub_")
    )
    
    dp.callback_query.register(
        show_user_transactions,
        F.data.startswith("admin_user_trans_")
    )
    
    dp.callback_query.register(
        confirm_user_delete,
        F.data.startswith("admin_user_delete_")
    )
    
    dp.callback_query.register(
        delete_user_account,
        F.data.startswith("admin_user_delete_confirm_")
    )
    
    dp.callback_query.register(
        handle_users_list_pagination_fixed,
        F.data.startswith("admin_users_list_page_")
    )
    
    dp.callback_query.register(
        start_user_search,
        F.data == "admin_users_search"
    )
    
    dp.message.register(
        process_user_search,
        AdminStates.waiting_for_user_search
    )
    
    dp.callback_query.register(
        show_user_management,
        F.data.startswith("admin_user_manage_")
    )
    
    dp.callback_query.register(
        start_balance_edit,
        F.data.startswith("admin_user_balance_")
    )
    
    dp.message.register(
        process_balance_edit,
        AdminStates.editing_user_balance
    )
    
    dp.callback_query.register(
        confirm_user_block,
        F.data.startswith("admin_user_block_")
    )
    
    dp.callback_query.register(
        block_user,
        F.data.startswith("admin_user_block_confirm_")
    )
    
    dp.callback_query.register(
        show_inactive_users,
        F.data == "admin_users_inactive"
    )
    
    dp.callback_query.register(
        cleanup_inactive_users,
        F.data == "admin_cleanup_inactive"
    )