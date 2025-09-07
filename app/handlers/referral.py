import logging
from pathlib import Path

import qrcode
from aiogram import Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_referral_keyboard
from app.localization.texts import get_texts
from app.utils.user_utils import (
    get_detailed_referral_list,
    get_referral_analytics,
    get_user_referral_summary,
)

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
    referral_text += f"• Приглашено пользователей: <b>{summary['invited_count']}</b>\n"
    referral_text += f"• Сделали первое пополнение: <b>{summary['paid_referrals_count']}</b>\n"
    referral_text += f"• Активных рефералов: <b>{summary['active_referrals_count']}</b>\n"
    referral_text += f"• Конверсия: <b>{summary['conversion_rate']}%</b>\n"
    referral_text += f"• Заработано всего: <b>{texts.format_price(summary['total_earned_kopeks'])}</b>\n"
    referral_text += f"• За последний месяц: <b>{texts.format_price(summary['month_earned_kopeks'])}</b>\n\n"
    
    referral_text += f"🎁 <b>Как работают награды:</b>\n"
    referral_text += f"• Новый пользователь получает: <b>{texts.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}</b> при первом пополнении от <b>{texts.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}</b>\n"
    referral_text += f"• Вы получаете при первом пополнении реферала: <b>{texts.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}</b>\n"
    referral_text += f"• Комиссия с каждого пополнения реферала: <b>{settings.REFERRAL_COMMISSION_PERCENT}%</b>\n\n"
    
    referral_text += f"🔗 <b>Ваша реферальная ссылка:</b>\n"
    referral_text += f"<code>{referral_link}</code>\n\n"
    referral_text += f"🆔 <b>Ваш код:</b> <code>{db_user.referral_code}</code>\n\n"
    
    if summary['recent_earnings']:
        meaningful_earnings = [
            earning for earning in summary['recent_earnings'][:5] 
            if earning['amount_kopeks'] > 0
        ]
        
        if meaningful_earnings:
            referral_text += f"💰 <b>Последние начисления:</b>\n"
            for earning in meaningful_earnings[:3]: 
                reason_text = {
                    "referral_first_topup": "🎉 Первое пополнение",
                    "referral_commission_topup": "💰 Комиссия с пополнения", 
                    "referral_commission": "💰 Комиссия с покупки"
                }.get(earning['reason'], earning['reason'])
                
                referral_text += f"• {reason_text}: <b>{texts.format_price(earning['amount_kopeks'])}</b> от {earning['referral_name']}\n"
            referral_text += "\n"
    
    if summary['earnings_by_type']:
        referral_text += f"📈 <b>Доходы по типам:</b>\n"
        
        if 'referral_first_topup' in summary['earnings_by_type']:
            data = summary['earnings_by_type']['referral_first_topup']
            if data['total_amount_kopeks'] > 0:
                referral_text += f"• Бонусы за первые пополнения: <b>{data['count']}</b> ({texts.format_price(data['total_amount_kopeks'])})\n"
        
        if 'referral_commission_topup' in summary['earnings_by_type']:
            data = summary['earnings_by_type']['referral_commission_topup']
            if data['total_amount_kopeks'] > 0:
                referral_text += f"• Комиссии с пополнений: <b>{data['count']}</b> ({texts.format_price(data['total_amount_kopeks'])})\n"
        
        if 'referral_commission' in summary['earnings_by_type']:
            data = summary['earnings_by_type']['referral_commission']
            if data['total_amount_kopeks'] > 0:
                referral_text += f"• Комиссии с покупок: <b>{data['count']}</b> ({texts.format_price(data['total_amount_kopeks'])})\n"
        
        referral_text += "\n"
    
    referral_text += "📢 Приглашайте друзей и зарабатывайте!"
    
    if callback.message.text:
        await callback.message.edit_text(
            referral_text,
            reply_markup=get_referral_keyboard(db_user.language),
            parse_mode="HTML"
        )
    else:
        await callback.message.delete()
        await callback.message.answer(
            referral_text,
            reply_markup=get_referral_keyboard(db_user.language),
            parse_mode="HTML"
        )
    await callback.answer()


async def show_referral_qr(
    callback: types.CallbackQuery,
    db_user: User,
):
    texts = get_texts(db_user.language)

    bot_username = (await callback.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={db_user.referral_code}"

    qr_dir = Path("data") / "referral_qr"
    qr_dir.mkdir(parents=True, exist_ok=True)

    file_path = qr_dir / f"{db_user.id}.png"
    if not file_path.exists():
        img = qrcode.make(referral_link)
        img.save(file_path)

    photo = FSInputFile(file_path)
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_referrals")]]
    )

    try:
        await callback.message.edit_media(
            types.InputMediaPhoto(
                media=photo,
                caption=f"🔗 Ваша реферальная ссылка:\n{referral_link}",
            ),
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer_photo(
            photo,
            caption=f"🔗 Ваша реферальная ссылка:\n{referral_link}",
            reply_markup=keyboard,
        )
    await callback.answer()


async def show_detailed_referral_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    page: int = 1
):
    texts = get_texts(db_user.language)
    
    referrals_data = await get_detailed_referral_list(db, db_user.id, limit=10, offset=(page - 1) * 10)
    
    if not referrals_data['referrals']:
        await callback.message.edit_text(
            "📋 У вас пока нет рефералов.\n\nПоделитесь своей реферальной ссылкой, чтобы начать зарабатывать!",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_referrals")]
            ])
        )
        await callback.answer()
        return
    
    text = f"👥 <b>Ваши рефералы</b> (стр. {referrals_data['current_page']}/{referrals_data['total_pages']})\n\n"
    
    for i, referral in enumerate(referrals_data['referrals'], 1):
        status_emoji = "🟢" if referral['status'] == 'active' else "🔴"
        
        topup_emoji = "💰" if referral['has_made_first_topup'] else "⏳"
        
        text += f"{i}. {status_emoji} <b>{referral['full_name']}</b>\n"
        text += f"   {topup_emoji} Пополнений: {referral['topups_count']}\n"
        text += f"   💎 Заработано с него: {texts.format_price(referral['total_earned_kopeks'])}\n"
        text += f"   📅 Регистрация: {referral['days_since_registration']} дн. назад\n"
        
        if referral['days_since_activity'] is not None:
            text += f"   🕐 Активность: {referral['days_since_activity']} дн. назад\n"
        else:
            text += f"   🕐 Активность: давно\n"
        
        text += "\n"
    
    keyboard = []
    nav_buttons = []
    
    if referrals_data['has_prev']:
        nav_buttons.append(types.InlineKeyboardButton(
            text="⬅️ Назад", 
            callback_data=f"referral_list_page_{page - 1}"
        ))
    
    if referrals_data['has_next']:
        nav_buttons.append(types.InlineKeyboardButton(
            text="Вперед ➡️", 
            callback_data=f"referral_list_page_{page + 1}"
        ))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([types.InlineKeyboardButton(
        text=texts.BACK, 
        callback_data="menu_referrals"
    )])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await callback.answer()


async def show_referral_analytics(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    analytics = await get_referral_analytics(db, db_user.id)
    
    text = f"📊 <b>Аналитика рефералов</b>\n\n"
    
    text += f"💰 <b>Доходы по периодам:</b>\n"
    text += f"• Сегодня: {texts.format_price(analytics['earnings_by_period']['today'])}\n"
    text += f"• За неделю: {texts.format_price(analytics['earnings_by_period']['week'])}\n"
    text += f"• За месяц: {texts.format_price(analytics['earnings_by_period']['month'])}\n"
    text += f"• За квартал: {texts.format_price(analytics['earnings_by_period']['quarter'])}\n\n"
    
    if analytics['top_referrals']:
        text += f"🏆 <b>Топ-{len(analytics['top_referrals'])} рефералов:</b>\n"
        for i, ref in enumerate(analytics['top_referrals'], 1):
            text += f"{i}. {ref['referral_name']}: {texts.format_price(ref['total_earned_kopeks'])} ({ref['earnings_count']} начислений)\n"
        text += "\n"
    
    text += "📈 Продолжайте развивать свою реферальную сеть!"
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_referrals")]
        ]),
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
    invite_text += f"💎 При первом пополнении от {texts.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)} ты получишь {texts.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)} бонусом на баланс!\n\n"
    invite_text += f"🚀 Быстрое подключение\n"
    invite_text += f"🌍 Серверы по всему миру\n"
    invite_text += f"🔒 Надежная защита\n\n"
    invite_text += f"👇 Переходи по ссылке:\n{referral_link}"
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="📤 Поделиться",
            switch_inline_query=invite_text 
        )],
        [types.InlineKeyboardButton(
            text=texts.BACK,
            callback_data="menu_referrals"
        )]
    ])
    
    await callback.message.edit_text(
        f"📝 <b>Приглашение создано!</b>\n\n"
        f"Нажмите кнопку «📤 Поделиться» чтобы отправить приглашение в любой чат, или скопируйте текст ниже:\n\n"
        f"<code>{invite_text}</code>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


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
        show_referral_qr,
        F.data == "referral_show_qr"
    )
    
    dp.callback_query.register(
        show_detailed_referral_list,
        F.data == "referral_list"
    )
    
    dp.callback_query.register(
        show_referral_analytics,
        F.data == "referral_analytics"
    )
    
    dp.callback_query.register(
        lambda callback, db_user, db: show_detailed_referral_list(
            callback, db_user, db, int(callback.data.split('_')[-1])
        ),
        F.data.startswith("referral_list_page_")
    )
