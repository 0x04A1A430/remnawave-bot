import logging
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from app.database.models import User, Subscription
from app.database.crud.user import get_user_by_id, subtract_user_balance
from app.database.crud.subscription import get_expiring_subscriptions, extend_subscription
from app.database.crud.transaction import create_transaction
from app.database.models import TransactionType
from app.keyboards.inline import get_autopay_notification_keyboard, get_subscription_expiring_keyboard
from app.localization.texts import get_texts
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)


async def send_subscription_expiring_notification(
    bot: Bot,
    db: AsyncSession,
    subscription: Subscription,
    days_left: int
) -> bool:
    try:
        user = await get_user_by_id(db, subscription.user_id)
        if not user:
            return False
        
        texts = get_texts(user.language)
        
        if subscription.is_trial:
            text = texts.TRIAL_ENDING_SOON.format(
                price=texts.format_price(30000) 
            )
        else:
            autopay_status = texts.AUTOPAY_ENABLED_TEXT if subscription.autopay_enabled else texts.AUTOPAY_DISABLED_TEXT
            
            if subscription.autopay_enabled:
                action_text = f"💰 Убедитесь, что на балансе достаточно средств: {texts.format_price(user.balance_kopeks)}"
            else:
                action_text = "💡 Включите автоплатеж или продлите подписку вручную"
            
            text = texts.SUBSCRIPTION_EXPIRING_PAID.format(
                days=days_left,
                end_date=subscription.end_date.strftime("%d.%m.%Y %H:%M"),
                autopay_status=autopay_status,
                action_text=action_text
            )
        
        keyboard = get_subscription_expiring_keyboard(subscription.id, user.language)
        
        await bot.send_message(
            chat_id=user.telegram_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        logger.info(f"✅ Отправлено уведомление об истечении подписки пользователю {user.telegram_id}")
        return True
        
    except TelegramBadRequest as e:
        logger.warning(f"⚠️ Не удалось отправить уведомление пользователю {user.telegram_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления об истечении подписки: {e}")
        return False


async def send_autopay_failed_notification(
    bot: Bot,
    db: AsyncSession,
    subscription: Subscription,
    required_amount: int
) -> bool:
    try:
        user = await get_user_by_id(db, subscription.user_id)
        if not user:
            return False
        
        texts = get_texts(user.language)
        
        text = texts.AUTOPAY_FAILED.format(
            balance=texts.format_price(user.balance_kopeks),
            required=texts.format_price(required_amount)
        )
        
        keyboard = get_autopay_notification_keyboard(subscription.id, user.language)
        
        await bot.send_message(
            chat_id=user.telegram_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        logger.info(f"✅ Отправлено уведомление о неудачном автоплатеже пользователю {user.telegram_id}")
        return True
        
    except TelegramBadRequest as e:
        logger.warning(f"⚠️ Не удалось отправить уведомление пользователю {user.telegram_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления о неудачном автоплатеже: {e}")
        return False


async def process_autopayment(
    bot: Bot,
    db: AsyncSession,
    subscription: Subscription
) -> bool:
    try:
        user = await get_user_by_id(db, subscription.user_id)
        if not user:
            logger.error(f"Пользователь {subscription.user_id} не найден для автоплатежа")
            return False
        
        subscription_service = SubscriptionService()
        renewal_cost = await subscription_service.calculate_renewal_price(
            subscription, 30, db 
        )
        
        if user.balance_kopeks < renewal_cost:
            logger.warning(f"Недостаточно средств для автоплатежа у пользователя {user.telegram_id}")
            await send_autopay_failed_notification(bot, db, subscription, renewal_cost)
            return False
        
        success = await subtract_user_balance(
            db, user, renewal_cost,
            f"Автопродление подписки на 30 дней"
        )
        
        if not success:
            logger.error(f"Ошибка списания средств для автоплатежа у пользователя {user.telegram_id}")
            await send_autopay_failed_notification(bot, db, subscription, renewal_cost)
            return False
        
        await extend_subscription(db, subscription, 30)
        
        await subscription_service.update_remnawave_user(db, subscription)
        
        await create_transaction(
            db=db,
            user_id=user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=renewal_cost,
            description="Автопродление подписки на 30 дней"
        )
        
        texts = get_texts(user.language)
        success_text = texts.AUTOPAY_SUCCESS.format(
            days=30,
            amount=texts.format_price(renewal_cost),
            new_end_date=subscription.end_date.strftime("%d.%m.%Y %H:%M")
        )
        
        await bot.send_message(
            chat_id=user.telegram_id,
            text=success_text,
            parse_mode="HTML"
        )
        
        logger.info(f"✅ Автоплатеж успешно выполнен для пользователя {user.telegram_id}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки автоплатежа: {e}")
        return False
