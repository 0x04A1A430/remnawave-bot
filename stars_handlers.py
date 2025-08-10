from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime
import logging
from typing import Dict

from database import Database, User
from keyboards import stars_topup_keyboard, stars_payment_keyboard, balance_keyboard
from translations import t
from utils import log_user_action
from referral_utils import process_referral_rewards

logger = logging.getLogger(__name__)

stars_router = Router()

@stars_router.callback_query(F.data == "topup_stars")
async def topup_stars_callback(callback: CallbackQuery, **kwargs):
    """Показать варианты пополнения через звезды"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    if not config or not config.STARS_ENABLED:
        await callback.message.edit_text(
            "❌ Пополнение через Telegram Stars временно недоступно",
            reply_markup=balance_keyboard(user.language)
        )
        return
    
    if not config.STARS_RATES:
        await callback.message.edit_text(
            "❌ Курсы звезд не настроены",
            reply_markup=balance_keyboard(user.language)
        )
        return
    
    text = "⭐ **Пополнение через Telegram Stars**\n\n"
    text += "🚀 **Преимущества:**\n"
    text += "• Мгновенное зачисление\n"
    text += "• Безопасные платежи через Telegram\n"
    text += "• Без комиссий и скрытых платежей\n\n"
    
    text += "💎 **Доступные варианты:**\n"
    
    sorted_rates = sorted(config.STARS_RATES.items())
    for stars, rubles in sorted_rates:
        rate_per_star = rubles / stars
        
        if stars >= 500:
            bonus_text = " 🔥 Выгодно!"
        elif stars >= 250:
            bonus_text = " 💎 Хорошо!"
        else:
            bonus_text = ""
        
        text += f"• {stars} ⭐ → {rubles:.0f}₽{bonus_text}\n"
    
    text += f"\n💡 Выберите подходящий вариант:"
    
    await callback.message.edit_text(
        text,
        reply_markup=stars_topup_keyboard(config.STARS_RATES, user.language),
        parse_mode='Markdown'
    )

@stars_router.callback_query(F.data.startswith("buy_stars_"))
async def buy_stars_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Обработка покупки звезд"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    bot = kwargs.get('bot')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    if not config or not config.STARS_ENABLED or not config.STARS_RATES:
        await callback.answer("❌ Пополнение недоступно")
        return
    
    try:
        stars_amount = int(callback.data.split("_")[2])
        
        if stars_amount not in config.STARS_RATES:
            await callback.answer("❌ Неверное количество звезд")
            return
        
        rub_amount = config.STARS_RATES[stars_amount]
        
        star_payment = await db.create_star_payment(
            user_id=user.telegram_id,
            stars_amount=stars_amount,
            rub_amount=rub_amount
        )
        
        prices = [LabeledPrice(label=f"Пополнение на {rub_amount:.0f}₽", amount=stars_amount)]
        
        try:
            await callback.answer("💳 Создаю платеж...")
            
            await bot.send_invoice(
                chat_id=callback.message.chat.id,
                title=f"Пополнение баланса",
                description=f"Пополнение баланса на {rub_amount:.0f}₽ за {stars_amount} ⭐",
                payload=f"star_payment_{star_payment.id}",
                currency="XTR",  
                prices=prices
            )
            
            try:
                await callback.message.edit_text(
                    f"💳 **Оплата через Telegram Stars**\n\n"
                    f"⭐ Количество звезд: {stars_amount}\n"
                    f"💰 Сумма пополнения: {rub_amount:.0f}₽\n\n"
                    f"👆 Нажмите кнопку \"Оплатить\" в инвойсе выше\n\n"
                    f"❌ Если передумали - нажмите кнопку ниже",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="❌ Отменить платеж", callback_data=f"cancel_star_payment_{star_payment.id}")],
                        [InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="topup_stars")]
                    ]),
                    parse_mode='Markdown'
                )
            except TelegramBadRequest:
                await callback.message.answer(
                    f"💳 **Оплата через Telegram Stars**\n\n"
                    f"⭐ Количество звезд: {stars_amount}\n"
                    f"💰 Сумма пополнения: {rub_amount:.0f}₽\n\n"
                    f"👆 Нажмите кнопку \"Оплатить\" в инвойсе выше",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="❌ Отменить платеж", callback_data=f"cancel_star_payment_{star_payment.id}")],
                        [InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="topup_stars")]
                    ]),
                    parse_mode='Markdown'
                )
            
        except TelegramBadRequest as e:
            logger.error(f"Failed to send invoice: {e}")
            await callback.answer("❌ Ошибка создания платежа", show_alert=True)
            
            try:
                await callback.message.edit_text(
                    "❌ Ошибка создания платежа. Попробуйте позже.",
                    reply_markup=stars_topup_keyboard(config.STARS_RATES, user.language)
                )
            except TelegramBadRequest:
                await callback.message.answer(
                    "❌ Ошибка создания платежа. Попробуйте позже.",
                    reply_markup=stars_topup_keyboard(config.STARS_RATES, user.language)
                )
            
            await db.cancel_star_payment(star_payment.id)
            
    except (ValueError, IndexError) as e:
        logger.error(f"Error processing stars amount: {e}")
        await callback.answer("❌ Ошибка обработки")
    except Exception as e:
        logger.error(f"Error in buy_stars_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@stars_router.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: PreCheckoutQuery, db: Database, **kwargs):
    """Обработка pre-checkout запроса для звезд"""
    try:
        if not pre_checkout_query.invoice_payload.startswith("star_payment_"):
            await pre_checkout_query.answer(ok=False, error_message="Неверный формат платежа")
            return
        
        payment_id = int(pre_checkout_query.invoice_payload.split("_")[2])
        
        star_payment = await db.get_star_payment_by_id(payment_id)
        
        if not star_payment:
            await pre_checkout_query.answer(ok=False, error_message="Платеж не найден")
            return
        
        if star_payment.status != 'pending':
            await pre_checkout_query.answer(ok=False, error_message="Платеж уже обработан")
            return
        
        if pre_checkout_query.total_amount != star_payment.stars_amount:
            await pre_checkout_query.answer(ok=False, error_message="Неверная сумма платежа")
            return
        
        await pre_checkout_query.answer(ok=True)
        
    except Exception as e:
        logger.error(f"Error in pre_checkout_query_handler: {e}")
        await pre_checkout_query.answer(ok=False, error_message="Внутренняя ошибка")

@stars_router.message(F.successful_payment)
async def successful_payment_handler(message: Message, db: Database, **kwargs):
    """Обработка успешного платежа звездами"""
    user = kwargs.get('user')
    bot = kwargs.get('bot')
    
    if not user:
        logger.error("User not found in successful payment handler")
        return
    
    try:
        payment_info = message.successful_payment
        
        if not payment_info.invoice_payload.startswith("star_payment_"):
            logger.error(f"Invalid payment payload: {payment_info.invoice_payload}")
            return
        
        payment_id = int(payment_info.invoice_payload.split("_")[2])
        
        success = await db.complete_star_payment(
            payment_id=payment_id,
            telegram_payment_charge_id=payment_info.telegram_payment_charge_id
        )
        
        if success:
            star_payment = await db.get_star_payment_by_id(payment_id)
            
            if star_payment:
                updated_user = await db.get_user_by_telegram_id(user.telegram_id)
                
                success_text = "✅ **Платеж успешно обработан!**\n\n"
                success_text += f"⭐ Оплачено звезд: {star_payment.stars_amount}\n"
                success_text += f"💰 Зачислено: {star_payment.rub_amount:.0f}₽\n"
                success_text += f"💳 Текущий баланс: {updated_user.balance:.0f}₽\n\n"
                success_text += "🎉 Средства уже доступны на вашем балансе!"
                
                await message.answer(
                    success_text,
                    parse_mode='Markdown'
                )
                
                if bot:
                    try:
                        regular_payment = await db.create_payment(
                            user_id=user.telegram_id,
                            amount=star_payment.rub_amount,
                            payment_type='stars',
                            description=f'Пополнение через Telegram Stars ({star_payment.stars_amount} ⭐)',
                            status='completed'
                        )
                        
                        await process_referral_rewards(
                            user.telegram_id,
                            star_payment.rub_amount,
                            regular_payment.id,
                            db,
                            bot,
                            payment_type='stars'
                        )
                    except Exception as ref_error:
                        logger.error(f"Error processing referral rewards for stars payment: {ref_error}")
                
                log_user_action(user.telegram_id, "stars_payment_completed", 
                               f"Stars: {star_payment.stars_amount}, Amount: {star_payment.rub_amount}")
            else:
                await message.answer("❌ Ошибка получения данных платежа")
        else:
            await message.answer("❌ Ошибка обработки платежа. Обратитесь в поддержку.")
            logger.error(f"Failed to complete star payment {payment_id}")
            
    except Exception as e:
        logger.error(f"Error in successful_payment_handler: {e}")
        await message.answer("❌ Произошла ошибка при обработке платежа. Обратитесь в поддержку.")

def get_stars_rate_info(stars_rates: Dict[int, float], lang: str = 'ru') -> str:
    """Получить информацию о курсах звезд"""
    if not stars_rates:
        return "Курсы не настроены"
    
    text = "⭐ **Курсы Telegram Stars:**\n\n"
    
    sorted_rates = sorted(stars_rates.items())
    for stars, rubles in sorted_rates:
        rate_per_star = rubles / stars
        text += f"• {stars} ⭐ = {rubles:.0f}₽ (курс: {rate_per_star:.2f}₽ за ⭐)\n"
    
    return text

@stars_router.callback_query(F.data.startswith("cancel_star_payment_"))
async def cancel_star_payment_callback(callback: CallbackQuery, db: Database, **kwargs):
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        payment_id = int(callback.data.split("_")[-1])
        
        star_payment = await db.get_star_payment_by_id(payment_id)
        
        if not star_payment:
            await callback.answer("❌ Платеж не найден")
            return
        
        if star_payment.user_id != user.telegram_id:
            await callback.answer("❌ Нет доступа к этому платежу")
            return
        
        if star_payment.status != 'pending':
            await callback.answer("❌ Платеж уже обработан")
            return
        
        success = await db.cancel_star_payment(payment_id)
        
        if success:
            await callback.answer("✅ Платеж отменен", show_alert=True)
            
            if config and config.STARS_RATES:
                try:
                    await callback.message.edit_text(
                        "❌ Платеж отменен\n\n⭐ **Пополнение через Telegram Stars**\n\nВыберите другой вариант:",
                        reply_markup=stars_topup_keyboard(config.STARS_RATES, user.language),
                        parse_mode='Markdown'
                    )
                except TelegramBadRequest:
                    await callback.message.answer(
                        "❌ Платеж отменен\n\n⭐ **Пополнение через Telegram Stars**\n\nВыберите другой вариант:",
                        reply_markup=stars_topup_keyboard(config.STARS_RATES, user.language),
                        parse_mode='Markdown'
                    )
            else:
                try:
                    await callback.message.edit_text(
                        "❌ Платеж отменен",
                        reply_markup=balance_keyboard(user.language)
                    )
                except TelegramBadRequest:
                    await callback.message.answer(
                        "❌ Платеж отменен",
                        reply_markup=balance_keyboard(user.language)
                    )
            
            log_user_action(user.telegram_id, "stars_payment_cancelled", f"Payment ID: {payment_id}")
        else:
            await callback.answer("❌ Ошибка отмены платежа")
            
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing payment ID from callback: {e}")
        await callback.answer("❌ Ошибка обработки")
    except Exception as e:
        logger.error(f"Error in cancel_star_payment_callback: {e}")
        await callback.answer("❌ Произошла ошибка")
