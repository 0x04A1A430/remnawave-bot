import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_

from app.config import settings
from app.states import AdminStates
from app.database.models import User, UserStatus, Subscription, BroadcastHistory
from app.keyboards.admin import (
    get_admin_messages_keyboard, get_broadcast_target_keyboard,
    get_custom_criteria_keyboard, get_broadcast_history_keyboard,
    get_admin_pagination_keyboard
)
from app.localization.texts import get_texts
from app.database.crud.user import get_users_list
from app.database.crud.subscription import get_expiring_subscriptions
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_messages_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    text = """
📨 <b>Управление рассылками</b>

Выберите тип рассылки:

- <b>Всем пользователям</b> - рассылка всем активным пользователям
- <b>По подпискам</b> - фильтрация по типу подписки
- <b>По критериям</b> - настраиваемые фильтры
- <b>История</b> - просмотр предыдущих рассылок

⚠️ Будьте осторожны с массовыми рассылками!
"""
    
    await callback.message.edit_text(
        text,
        reply_markup=get_admin_messages_keyboard(db_user.language)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_broadcast_targets(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    await callback.message.edit_text(
        "🎯 <b>Выбор целевой аудитории</b>\n\n"
        "Выберите категорию пользователей для рассылки:",
        reply_markup=get_broadcast_target_keyboard(db_user.language)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_messages_history(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    page = 1
    if '_page_' in callback.data:
        page = int(callback.data.split('_page_')[1])
    
    limit = 10
    offset = (page - 1) * limit
    
    stmt = select(BroadcastHistory).order_by(BroadcastHistory.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    broadcasts = result.scalars().all()
    
    count_stmt = select(func.count(BroadcastHistory.id))
    count_result = await db.execute(count_stmt)
    total_count = count_result.scalar() or 0
    total_pages = (total_count + limit - 1) // limit
    
    if not broadcasts:
        text = """
📋 <b>История рассылок</b>

❌ История рассылок пуста.
Отправьте первую рассылку, чтобы увидеть её здесь.
"""
        keyboard = [[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_messages")]]
    else:
        text = f"📋 <b>История рассылок</b> (страница {page}/{total_pages})\n\n"
        
        for broadcast in broadcasts:
            status_emoji = "✅" if broadcast.status == "completed" else "❌" if broadcast.status == "failed" else "⏳"
            success_rate = round((broadcast.sent_count / broadcast.total_count * 100), 1) if broadcast.total_count > 0 else 0
            
            message_preview = broadcast.message_text[:100] + "..." if len(broadcast.message_text) > 100 else broadcast.message_text
            
            text += f"""
{status_emoji} <b>{broadcast.created_at.strftime('%d.%m.%Y %H:%M')}</b>
📊 Отправлено: {broadcast.sent_count}/{broadcast.total_count} ({success_rate}%)
🎯 Аудитория: {get_target_name(broadcast.target_type)}
👤 Админ: {broadcast.admin_name}
📝 Сообщение: <i>{message_preview}</i>
━━━━━━━━━━━━━━━━━━━━
"""
        
        keyboard = get_broadcast_history_keyboard(page, total_pages, db_user.language).inline_keyboard
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_custom_broadcast(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    
    stats = await get_users_statistics(db)
    
    text = f"""
🔍 <b>Рассылка по критериям</b>

📊 <b>Доступные фильтры:</b>

👥 <b>По регистрации:</b>
• Сегодня: {stats['today']} чел.
• За неделю: {stats['week']} чел.
• За месяц: {stats['month']} чел.

💼 <b>По активности:</b>
• Активные сегодня: {stats['active_today']} чел.
• Неактивные 7+ дней: {stats['inactive_week']} чел.
• Неактивные 30+ дней: {stats['inactive_month']} чел.

🔗 <b>По источнику:</b>
• Через рефералов: {stats['referrals']} чел.
• Прямая регистрация: {stats['direct']} чел.

Выберите критерий для фильтрации:
"""
    
    await callback.message.edit_text(
        text,
        reply_markup=get_custom_criteria_keyboard(db_user.language)
    )
    await callback.answer()


@admin_required
@error_handler
async def select_custom_criteria(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    criteria = callback.data.replace('criteria_', '')
    
    criteria_names = {
        "today": "Зарегистрированные сегодня",
        "week": "Зарегистрированные за неделю",
        "month": "Зарегистрированные за месяц",
        "active_today": "Активные сегодня",
        "inactive_week": "Неактивные 7+ дней",
        "inactive_month": "Неактивные 30+ дней",
        "referrals": "Пришедшие через рефералов",
        "direct": "Прямая регистрация"
    }
    
    user_count = await get_custom_users_count(db, criteria)
    
    await state.update_data(broadcast_target=f"custom_{criteria}")
    
    await callback.message.edit_text(
        f"📨 <b>Создание рассылки</b>\n\n"
        f"🎯 <b>Критерий:</b> {criteria_names.get(criteria, criteria)}\n"
        f"👥 <b>Получателей:</b> {user_count}\n\n"
        f"Введите текст сообщения для рассылки:\n\n"
        f"<i>Поддерживается HTML разметка</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_messages")]
        ])
    )
    
    await state.set_state(AdminStates.waiting_for_broadcast_message)
    await callback.answer()


@admin_required
@error_handler
async def select_broadcast_target(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    target = callback.data.split('_')[-1]
    
    target_names = {
        "all": "Всем пользователям",
        "active": "С активной подпиской",
        "trial": "С триальной подпиской", 
        "no": "Без подписки",
        "expiring": "С истекающей подпиской"
    }
    
    user_count = await get_target_users_count(db, target)
    
    await state.update_data(broadcast_target=target)
    
    await callback.message.edit_text(
        f"📨 <b>Создание рассылки</b>\n\n"
        f"🎯 <b>Аудитория:</b> {target_names.get(target, target)}\n"
        f"👥 <b>Получателей:</b> {user_count}\n\n"
        f"Введите текст сообщения для рассылки:\n\n"
        f"<i>Поддерживается HTML разметка</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_messages")]
        ])
    )
    
    await state.set_state(AdminStates.waiting_for_broadcast_message)
    await callback.answer()


@admin_required
@error_handler
async def process_broadcast_message(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    broadcast_text = message.text
    
    if len(broadcast_text) > 4000:
        await message.answer("❌ Сообщение слишком длинное (максимум 4000 символов)")
        return
    
    data = await state.get_data()
    target = data.get('broadcast_target')
    
    user_count = await get_target_users_count(db, target) if not target.startswith('custom_') else await get_custom_users_count(db, target.replace('custom_', ''))
    
    await state.update_data(broadcast_message=broadcast_text)
    
    target_display = get_target_display_name(target)
    
    preview_text = f"""
📨 <b>Предварительный просмотр рассылки</b>

🎯 <b>Аудитория:</b> {target_display}
👥 <b>Получателей:</b> {user_count}

📝 <b>Сообщение:</b>
{broadcast_text}

Подтвердить отправку?
"""
    
    keyboard = [
        [
            types.InlineKeyboardButton(text="✅ Отправить", callback_data="admin_confirm_broadcast"),
            types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_messages")
        ]
    ]
    
    await message.answer(
        preview_text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await state.set_state(AdminStates.confirming_broadcast)


@admin_required
@error_handler
async def confirm_broadcast(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession
):
    data = await state.get_data()
    target = data.get('broadcast_target')
    message_text = data.get('broadcast_message')
    
    await callback.message.edit_text(
        "📨 Начинаю рассылку...\n\n"
        "⏳ Это может занять несколько минут.",
        reply_markup=None
    )
    
    if target.startswith('custom_'):
        users = await get_custom_users(db, target.replace('custom_', ''))
    else:
        users = await get_target_users(db, target)
    
    broadcast_history = BroadcastHistory(
        target_type=target,
        message_text=message_text,
        total_count=len(users),
        sent_count=0,
        failed_count=0,
        admin_id=db_user.id,
        admin_name=db_user.full_name,
        status="in_progress"
    )
    db.add(broadcast_history)
    await db.commit()
    await db.refresh(broadcast_history)
    
    sent_count = 0
    failed_count = 0
    
    for user in users:
        try:
            await callback.bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="HTML"
            )
            sent_count += 1
            
            if sent_count % 20 == 0:
                await asyncio.sleep(1)
                
        except Exception as e:
            failed_count += 1
            logger.error(f"Ошибка отправки рассылки пользователю {user.telegram_id}: {e}")
    
    broadcast_history.sent_count = sent_count
    broadcast_history.failed_count = failed_count
    broadcast_history.status = "completed" if failed_count == 0 else "partial"
    broadcast_history.completed_at = datetime.utcnow()
    await db.commit()
    
    result_text = f"""
✅ <b>Рассылка завершена!</b>

📊 <b>Результат:</b>
- Отправлено: {sent_count}
- Не доставлено: {failed_count}
- Всего пользователей: {len(users)}
- Успешность: {round(sent_count / len(users) * 100, 1) if users else 0}%

<b>Администратор:</b> {db_user.full_name}
"""
    
    await callback.message.edit_text(
        result_text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📨 К рассылкам", callback_data="admin_messages")]
        ])
    )
    
    await state.clear()
    logger.info(f"Рассылка выполнена админом {db_user.telegram_id}: {sent_count}/{len(users)}")


async def get_target_users_count(db: AsyncSession, target: str) -> int:
    users = await get_target_users(db, target)
    return len(users)


async def get_target_users(db: AsyncSession, target: str) -> list:
   if target == "all":
       return await get_users_list(db, offset=0, limit=10000, status=UserStatus.ACTIVE)
   elif target == "active":
       users = await get_users_list(db, offset=0, limit=10000, status=UserStatus.ACTIVE)
       return [user for user in users if user.subscription and user.subscription.is_active and not user.subscription.is_trial]
   elif target == "trial":
       users = await get_users_list(db, offset=0, limit=10000, status=UserStatus.ACTIVE)
       return [user for user in users if user.subscription and user.subscription.is_trial]
   elif target == "no":
       users = await get_users_list(db, offset=0, limit=10000, status=UserStatus.ACTIVE)
       return [user for user in users if not user.subscription or not user.subscription.is_active]
   elif target == "expiring":
       expiring_subs = await get_expiring_subscriptions(db, 3)
       return [sub.user for sub in expiring_subs if sub.user]
   else:
       return []


async def get_custom_users_count(db: AsyncSession, criteria: str) -> int:
    users = await get_custom_users(db, criteria)
    return len(users)


async def get_custom_users(db: AsyncSession, criteria: str) -> list:
    """Получение пользователей по настраиваемым критериям"""
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    
    if criteria == "today":
        stmt = select(User).where(
            and_(User.status == "active", User.created_at >= today)
        )
    elif criteria == "week":
        stmt = select(User).where(
            and_(User.status == "active", User.created_at >= week_ago)
        )
    elif criteria == "month":
        stmt = select(User).where(
            and_(User.status == "active", User.created_at >= month_ago)
        )
    elif criteria == "active_today":
        stmt = select(User).where(
            and_(User.status == "active", User.last_activity >= today)
        )
    elif criteria == "inactive_week":
        stmt = select(User).where(
            and_(User.status == "active", User.last_activity < week_ago)
        )
    elif criteria == "inactive_month":
        stmt = select(User).where(
            and_(User.status == "active", User.last_activity < month_ago)
        )
    elif criteria == "referrals":
        stmt = select(User).where(
            and_(User.status == "active", User.referred_by_id.isnot(None))
        )
    elif criteria == "direct":
        stmt = select(User).where(
            and_(
                User.status == "active", 
                User.referred_by_id.is_(None)
            )
        )
    else:
        return []
    
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_users_statistics(db: AsyncSession) -> dict:
    """Получение статистики пользователей для отображения"""
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    
    stats = {}
    
    stats['today'] = await db.scalar(
        select(func.count(User.id)).where(
            and_(User.status == "active", User.created_at >= today)
        )
    ) or 0
    
    stats['week'] = await db.scalar(
        select(func.count(User.id)).where(
            and_(User.status == "active", User.created_at >= week_ago)
        )
    ) or 0
    
    stats['month'] = await db.scalar(
        select(func.count(User.id)).where(
            and_(User.status == "active", User.created_at >= month_ago)
        )
    ) or 0
    
    stats['active_today'] = await db.scalar(
        select(func.count(User.id)).where(
            and_(User.status == "active", User.last_activity >= today)
        )
    ) or 0
    
    stats['inactive_week'] = await db.scalar(
        select(func.count(User.id)).where(
            and_(User.status == "active", User.last_activity < week_ago)
        )
    ) or 0
    
    stats['inactive_month'] = await db.scalar(
        select(func.count(User.id)).where(
            and_(User.status == "active", User.last_activity < month_ago)
        )
    ) or 0
    
    stats['referrals'] = await db.scalar(
        select(func.count(User.id)).where(
            and_(User.status == "active", User.referred_by_id.isnot(None))
        )
    ) or 0
    
    stats['direct'] = await db.scalar(
        select(func.count(User.id)).where(
            and_(
                User.status == "active", 
                User.referred_by_id.is_(None)
            )
        )
    ) or 0
    
    return stats


def get_target_name(target_type: str) -> str:
    names = {
        "all": "Всем пользователям",
        "active": "С активной подпиской",
        "trial": "С триальной подпиской",
        "no": "Без подписки",
        "expiring": "С истекающей подпиской",
        "custom_today": "Зарегистрированные сегодня",
        "custom_week": "Зарегистрированные за неделю",
        "custom_month": "Зарегистрированные за месяц",
        "custom_active_today": "Активные сегодня",
        "custom_inactive_week": "Неактивные 7+ дней",
        "custom_inactive_month": "Неактивные 30+ дней",
        "custom_referrals": "Через рефералов",
        "custom_direct": "Прямая регистрация"
    }
    return names.get(target_type, target_type)


def get_target_display_name(target: str) -> str:
    return get_target_name(target)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_messages_menu, F.data == "admin_messages")
    dp.callback_query.register(show_broadcast_targets, F.data.in_(["admin_msg_all", "admin_msg_by_sub"]))
    dp.callback_query.register(select_broadcast_target, F.data.startswith("broadcast_"))
    dp.callback_query.register(confirm_broadcast, F.data == "admin_confirm_broadcast")
    
    dp.callback_query.register(show_messages_history, F.data.startswith("admin_msg_history"))
    dp.callback_query.register(show_custom_broadcast, F.data == "admin_msg_custom")
    dp.callback_query.register(select_custom_criteria, F.data.startswith("criteria_"))
    
    dp.message.register(process_broadcast_message, AdminStates.waiting_for_broadcast_message)