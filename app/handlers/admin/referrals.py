import logging
from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession
import datetime

from app.config import settings
from app.database.models import User
from app.localization.texts import get_texts
from app.database.crud.referral import (
    get_referral_statistics,
    get_top_referrers_by_period,
    get_user_referral_stats,
)
from app.database.crud.user import get_user_by_id
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_referral_statistics(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    try:
        stats = await get_referral_statistics(db)
        
        avg_per_referrer = 0
        if stats.get('active_referrers', 0) > 0:
            avg_per_referrer = stats.get('total_paid_kopeks', 0) / stats['active_referrers']
        
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        
        text = f"""
🤝 <b>Реферальная статистика</b>

<b>Общие показатели:</b>
- Пользователей с рефералами: {stats.get('users_with_referrals', 0)}
- Активных рефереров: {stats.get('active_referrers', 0)}
- Выплачено всего: {settings.format_price(stats.get('total_paid_kopeks', 0))}

<b>За период:</b>
- Сегодня: {settings.format_price(stats.get('today_earnings_kopeks', 0))}
- За неделю: {settings.format_price(stats.get('week_earnings_kopeks', 0))}
- За месяц: {settings.format_price(stats.get('month_earnings_kopeks', 0))}

<b>Средние показатели:</b>
- На одного реферера: {settings.format_price(int(avg_per_referrer))}

<b>Топ-5 рефереров:</b>
"""
        
        top_referrers = stats.get('top_referrers', [])
        if top_referrers:
            for i, referrer in enumerate(top_referrers[:5], 1):
                earned = referrer.get('total_earned_kopeks', 0)
                count = referrer.get('referrals_count', 0)
                user_id = referrer.get('user_id', 'N/A')
                
                if count > 0:
                    text += f"{i}. ID {user_id}: {settings.format_price(earned)} ({count} реф.)\n"
                else:
                    logger.warning(f"Реферер {user_id} имеет {count} рефералов, но есть в топе")
        else:
            text += "Нет данных\n"
        
        text += f"""

<b>Настройки реферальной системы:</b>
- Минимальное пополнение: {settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}
- Бонус за первое пополнение: {settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}
- Бонус пригласившему: {settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}
- Комиссия с покупок: {settings.REFERRAL_COMMISSION_PERCENT}%
- Уведомления: {'✅ Включены' if settings.REFERRAL_NOTIFICATIONS_ENABLED else '❌ Отключены'}

<i>🕐 Обновлено: {current_time}</i>
"""
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_referrals")],
            [types.InlineKeyboardButton(text="👥 Топ рефереров", callback_data="admin_referrals_top")],
            [types.InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_referrals_settings")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
        ])
        
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
            await callback.answer("Обновлено")
        except Exception as edit_error:
            if "message is not modified" in str(edit_error):
                await callback.answer("Данные актуальны")
            else:
                logger.error(f"Ошибка редактирования сообщения: {edit_error}")
                await callback.answer("Ошибка обновления")
        
    except Exception as e:
        logger.error(f"Ошибка в show_referral_statistics: {e}", exc_info=True)
        
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        text = f"""
🤝 <b>Реферальная статистика</b>

❌ <b>Ошибка загрузки данных</b>

<b>Текущие настройки:</b>
- Минимальное пополнение: {settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}
- Бонус за первое пополнение: {settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}
- Бонус пригласившему: {settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}
- Комиссия с покупок: {settings.REFERRAL_COMMISSION_PERCENT}%

<i>🕐 Время: {current_time}</i>
"""
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Повторить", callback_data="admin_referrals")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
        ])
        
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except:
            pass
        await callback.answer("Произошла ошибка при загрузке статистики")


def _get_top_keyboard(period: str, sort_by: str) -> types.InlineKeyboardMarkup:
    """Создаёт клавиатуру для выбора периода и сортировки."""
    period_week = "✅ Неделя" if period == "week" else "Неделя"
    period_month = "✅ Месяц" if period == "month" else "Месяц"
    sort_earnings = "✅ По заработку" if sort_by == "earnings" else "По заработку"
    sort_invited = "✅ По приглашённым" if sort_by == "invited" else "По приглашённым"

    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text=period_week,
                callback_data=f"admin_top_ref:week:{sort_by}"
            ),
            types.InlineKeyboardButton(
                text=period_month,
                callback_data=f"admin_top_ref:month:{sort_by}"
            ),
        ],
        [
            types.InlineKeyboardButton(
                text=sort_earnings,
                callback_data=f"admin_top_ref:{period}:earnings"
            ),
            types.InlineKeyboardButton(
                text=sort_invited,
                callback_data=f"admin_top_ref:{period}:invited"
            ),
        ],
        [types.InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin_top_ref:{period}:{sort_by}")],
        [types.InlineKeyboardButton(text="⬅️ К статистике", callback_data="admin_referrals")]
    ])


@admin_required
@error_handler
async def show_top_referrers(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Показывает топ рефереров (по умолчанию: неделя, по заработку)."""
    await _show_top_referrers_filtered(callback, db, period="week", sort_by="earnings")


@admin_required
@error_handler
async def show_top_referrers_filtered(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Обрабатывает выбор периода и сортировки."""
    # Парсим callback_data: admin_top_ref:period:sort_by
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Ошибка параметров")
        return

    period = parts[1]  # week или month
    sort_by = parts[2]  # earnings или invited

    if period not in ("week", "month"):
        period = "week"
    if sort_by not in ("earnings", "invited"):
        sort_by = "earnings"

    await _show_top_referrers_filtered(callback, db, period, sort_by)


async def _show_top_referrers_filtered(
    callback: types.CallbackQuery,
    db: AsyncSession,
    period: str,
    sort_by: str
):
    """Внутренняя функция отображения топа с фильтрами."""
    try:
        top_referrers = await get_top_referrers_by_period(db, period=period, sort_by=sort_by)

        period_text = "за неделю" if period == "week" else "за месяц"
        sort_text = "по заработку" if sort_by == "earnings" else "по приглашённым"

        text = f"🏆 <b>Топ рефереров {period_text}</b>\n"
        text += f"<i>Сортировка: {sort_text}</i>\n\n"

        if top_referrers:
            for i, referrer in enumerate(top_referrers[:20], 1):
                earned = referrer.get('earnings_kopeks', 0)
                count = referrer.get('invited_count', 0)
                display_name = referrer.get('display_name', 'N/A')
                username = referrer.get('username', '')
                telegram_id = referrer.get('telegram_id', 'N/A')

                if username:
                    display_text = f"@{username} (ID{telegram_id})"
                elif display_name and display_name != f"ID{telegram_id}":
                    display_text = f"{display_name} (ID{telegram_id})"
                else:
                    display_text = f"ID{telegram_id}"

                emoji = ""
                if i == 1:
                    emoji = "🥇 "
                elif i == 2:
                    emoji = "🥈 "
                elif i == 3:
                    emoji = "🥉 "

                # Выделяем основную метрику в зависимости от сортировки
                if sort_by == "invited":
                    text += f"{emoji}{i}. {display_text}\n"
                    text += f"   👥 <b>{count} приглашённых</b> | 💰 {settings.format_price(earned)}\n\n"
                else:
                    text += f"{emoji}{i}. {display_text}\n"
                    text += f"   💰 <b>{settings.format_price(earned)}</b> | 👥 {count} приглашённых\n\n"
        else:
            text += "Нет данных за выбранный период\n"

        keyboard = _get_top_keyboard(period, sort_by)

        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
            await callback.answer()
        except Exception as edit_error:
            if "message is not modified" in str(edit_error):
                await callback.answer("Данные актуальны")
            else:
                raise

    except Exception as e:
        logger.error(f"Ошибка в show_top_referrers_filtered: {e}", exc_info=True)
        await callback.answer("Ошибка загрузки топа рефереров")


@admin_required
@error_handler
async def show_referral_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    text = f"""
⚙️ <b>Настройки реферальной системы</b>

<b>Бонусы и награды:</b>
• Минимальная сумма пополнения для участия: {settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}
• Бонус за первое пополнение реферала: {settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}
• Бонус пригласившему за первое пополнение: {settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}

<b>Комиссионные:</b>
• Процент с каждой покупки реферала: {settings.REFERRAL_COMMISSION_PERCENT}%

<b>Уведомления:</b>
• Статус: {'✅ Включены' if settings.REFERRAL_NOTIFICATIONS_ENABLED else '❌ Отключены'}
• Попытки отправки: {getattr(settings, 'REFERRAL_NOTIFICATION_RETRY_ATTEMPTS', 3)}

<i>💡 Для изменения настроек отредактируйте файл .env и перезапустите бота</i>
"""
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ К статистике", callback_data="admin_referrals")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_referral_statistics, F.data == "admin_referrals")
    dp.callback_query.register(show_top_referrers, F.data == "admin_referrals_top")
    dp.callback_query.register(show_top_referrers_filtered, F.data.startswith("admin_top_ref:"))
    dp.callback_query.register(show_referral_settings, F.data == "admin_referrals_settings")
