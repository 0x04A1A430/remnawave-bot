import logging
from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.localization.texts import get_texts
from app.database.crud.referral import get_referral_statistics, get_user_referral_stats
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
    stats = await get_referral_statistics(db)
    
    avg_per_referrer = 0
    if stats['active_referrers'] > 0:
        avg_per_referrer = stats['total_paid_kopeks'] / stats['active_referrers']
    
    text = f"""
🤝 <b>Реферальная статистика</b>

<b>Общие показатели:</b>
- Пользователей с рефералами: {stats['users_with_referrals']}
- Активных рефереров: {stats['active_referrers']}
- Выплачено всего: {settings.format_price(stats['total_paid_kopeks'])}

<b>За период:</b>
- Сегодня: {settings.format_price(stats['today_earnings_kopeks'])}
- За неделю: {settings.format_price(stats['week_earnings_kopeks'])}
- За месяц: {settings.format_price(stats['month_earnings_kopeks'])}

<b>Средние показатели:</b>
- На одного реферера: {settings.format_price(int(avg_per_referrer))}

<b>Топ-5 рефереров:</b>
"""
    
    for i, referrer in enumerate(stats['top_referrers'][:5], 1):
        text += f"{i}. ID {referrer['user_id']}: {settings.format_price(referrer['total_earned_kopeks'])} ({referrer['referrals_count']} реф.)\n"
    
    if not stats['top_referrers']:
        text += "Нет данных\n"
    
    text += f"""

<b>Настройки:</b>
- Бонус за регистрацию: {settings.format_price(settings.REFERRAL_REGISTRATION_REWARD)}
- Бонус новому пользователю: {settings.format_price(settings.REFERRED_USER_REWARD)}
- Комиссия: {settings.REFERRAL_COMMISSION_PERCENT}%
"""
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_referrals")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
        ])
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_referral_statistics, F.data == "admin_referrals")