import logging
from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.referral import get_referral_earnings_sum
from app.database.models import User
from app.keyboards.inline import get_referral_keyboard, get_back_keyboard
from app.localization.texts import get_texts
from app.utils.user_utils import get_user_referral_summary

logger = logging.getLogger(__name__)


async def show_referral_info(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    texts = get_texts(db_user.language)
    
    summary = await get_user_referral_summary(db, db_user.id)
    
    bot_username = (await callback.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={db_user.referral_code}"
    
    referral_text = f"👥 <b>Реферальная программа</b>\n\n"
    
    referral_text += f"📊 <b>Ваша статистика:</b>\n"
    referral_text += f"• Приглашено пользователей: {summary['invited_count']}\n"
    referral_text += f"• Купили подписку: {summary['paid_referrals_count']}\n"
    referral_text += f"• Заработано всего: {texts.format_price(summary['total_earned_kopeks'])}\n"
    referral_text += f"• За последний месяц: {texts.format_price(summary['month_earned_kopeks'])}\n\n"
    
    referral_text += f"🎁 <b>Как работают награды:</b>\n"
    referral_text += f"• Новый пользователь получает: {texts.format_price(settings.REFERRED_USER_REWARD)}\n"
    referral_text += f"• Вы получаете при первой покупке реферала: {texts.format_price(settings.REFERRAL_REGISTRATION_REWARD)}\n"
    referral_text += f"• Комиссия с каждой покупки реферала: {settings.REFERRAL_COMMISSION_PERCENT}%\n\n"
    
    referral_text += f"🔗 <b>Ваша реферальная ссылка:</b>\n"
    referral_text += f"<code>{referral_link}</code>\n\n"
    referral_text += f"🆔 <b>Ваш код:</b> <code>{db_user.referral_code}</code>\n\n"
    
    if summary['recent_earnings']:
        referral_text += f"💰 <b>Последние начисления:</b>\n"
        for earning in summary['recent_earnings'][:3]: 
            reason_text = {
                "referral_first_purchase": "🎉 Первая покупка",
                "referral_commission": "💰 Комиссия",
                "referral_registration_pending": "⏳ Ожидание покупки"
            }.get(earning['reason'], earning['reason'])
            
            referral_text += f"• {reason_text}: {texts.format_price(earning['amount_kopeks'])} от {earning['referral_name']}\n"
        referral_text += "\n"
    
    referral_text += "📢 Приглашайте друзей и зарабатывайте!"
    
    await callback.message.edit_text(
        referral_text,
        reply_markup=get_referral_keyboard(db_user.language),
        parse_mode="HTML"
    )
    await callback.answer()


async def create_invite_message(
    callback: types.CallbackQuery,
    db_user: User
):
    
    texts = get_texts(db_user.language)
    
    bot_username = (await callback.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={db_user.referral_code}"
    
    invite_text = f"🎉 Присоединяйся к VPN сервису!\n\n"
    invite_text += f"💎 При регистрации по моей ссылке ты получишь {texts.format_price(settings.REFERRED_USER_REWARD)} на баланс!\n\n"
    invite_text += f"🚀 Быстрое подключение\n"
    invite_text += f"🌍 Серверы по всему миру\n"
    invite_text += f"🔒 Надежная защита\n\n"
    invite_text += f"👇 Переходи по ссылке:\n{referral_link}"
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text="📤 Поделиться",
                switch_inline_query=invite_text
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📋 Скопировать ссылку",
                callback_data="copy_referral_link"
            )
        ],
        [
            types.InlineKeyboardButton(
                text=texts.BACK,
                callback_data="menu_referrals"
            )
        ]
    ])
    
    await callback.message.edit_text(
        f"📝 <b>Приглашение создано!</b>\n\n"
        f"Используйте кнопку ниже для отправки приглашения или скопируйте текст:\n\n"
        f"<code>{invite_text}</code>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


async def copy_referral_link(
    callback: types.CallbackQuery,
    db_user: User
):
    
    bot_username = (await callback.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={db_user.referral_code}"
    
    await callback.answer(
        f"Ссылка скопирована: {referral_link}",
        show_alert=True
    )


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_referral_info,
        F.data == "menu_referrals"
    )
    
    dp.callback_query.register(
        create_invite_message,
        F.data == "referral_create_invite"
    )
    
    dp.callback_query.register(
        copy_referral_link,
        F.data == "copy_referral_link"
    )