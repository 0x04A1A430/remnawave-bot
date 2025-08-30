import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.user import add_user_balance, get_user_by_id
from app.database.crud.referral import create_referral_earning
from app.database.models import TransactionType

logger = logging.getLogger(__name__)


async def process_referral_registration(
    db: AsyncSession,
    new_user_id: int,
    referrer_id: int
):
    
    try:
        new_user = await get_user_by_id(db, new_user_id)
        referrer = await get_user_by_id(db, referrer_id)
        
        if not new_user or not referrer:
            logger.error(f"Пользователи не найдены: new_user_id={new_user_id}, referrer_id={referrer_id}")
            return False
        
        if new_user.referred_by_id != referrer_id:
            logger.error(f"Пользователь {new_user_id} не привязан к рефереру {referrer_id}")
            return False
        
        if settings.REFERRED_USER_REWARD > 0:
            await add_user_balance(
                db, new_user, settings.REFERRED_USER_REWARD,
                f"Бонус за регистрацию по реферальной ссылке"
            )
            
            logger.info(f"💰 Новый пользователь {new_user_id} получил бонус {settings.REFERRED_USER_REWARD/100}₽")
        
        await create_referral_earning(
            db=db,
            user_id=referrer_id,
            referral_id=new_user_id,
            amount_kopeks=0,
            reason="referral_registration_pending"
        )
        
        logger.info(f"✅ Обработана реферальная регистрация: {new_user_id} -> {referrer_id}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка обработки реферальной регистрации: {e}")
        return False


async def process_referral_purchase(
    db: AsyncSession,
    user_id: int,
    purchase_amount_kopeks: int,
    transaction_id: int = None
):
    
    try:
        user = await get_user_by_id(db, user_id)
        if not user or not user.referred_by_id:
            logger.info(f"Пользователь {user_id} не является рефералом")
            return False
        
        referrer = await get_user_by_id(db, user.referred_by_id)
        if not referrer:
            logger.error(f"Реферер {user.referred_by_id} не найден")
            return False
        
        from app.database.crud.referral import get_referral_earnings_by_referral
        existing_earnings = await get_referral_earnings_by_referral(db, user_id)
        
        purchase_earnings = [
            earning for earning in existing_earnings 
            if earning.reason in ["referral_first_purchase", "referral_commission"]
        ]
        
        is_first_purchase = len(purchase_earnings) == 0
        
        logger.info(f"🔍 Покупка реферала {user_id}: первая = {is_first_purchase}, сумма = {purchase_amount_kopeks/100}₽")
        
        if is_first_purchase and settings.REFERRAL_REGISTRATION_REWARD > 0:
            reward_amount = settings.REFERRAL_REGISTRATION_REWARD
            
            if reward_amount > 1000000: 
                logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: reward_amount = {reward_amount} слишком большой! Проверьте настройки REFERRAL_REGISTRATION_REWARD")
                reward_amount = 10000 
            
            await add_user_balance(
                db, referrer, reward_amount,
                f"Реферальная награда за первую покупку {user.full_name}"
            )
            
            await create_referral_earning(
                db=db,
                user_id=referrer.id,
                referral_id=user_id,
                amount_kopeks=reward_amount,
                reason="referral_first_purchase",
                referral_transaction_id=transaction_id
            )
            
            logger.info(f"🎉 Первая покупка реферала: {referrer.telegram_id} получил {reward_amount/100}₽")
        
        if not (0 <= settings.REFERRAL_COMMISSION_PERCENT <= 100):
            logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: REFERRAL_COMMISSION_PERCENT = {settings.REFERRAL_COMMISSION_PERCENT} некорректный! Должен быть от 0 до 100")
            commission_percent = 10 
        else:
            commission_percent = settings.REFERRAL_COMMISSION_PERCENT
            
        commission_amount = int(purchase_amount_kopeks * commission_percent / 100)
        
        if commission_amount > 0:
            await add_user_balance(
                db, referrer, commission_amount,
                f"Комиссия {commission_percent}% с покупки {user.full_name}"
            )
            
            await create_referral_earning(
                db=db,
                user_id=referrer.id,
                referral_id=user_id,
                amount_kopeks=commission_amount,
                reason="referral_commission",
                referral_transaction_id=transaction_id
            )
            
            logger.info(f"💰 Комиссия с покупки: {referrer.telegram_id} получил {commission_amount/100}₽")
        
        if not user.has_had_paid_subscription:
            user.has_had_paid_subscription = True
            await db.commit()
            logger.info(f"✅ Пользователь {user_id} отмечен как имевший платную подписку")
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка обработки покупки реферала: {e}")
        import traceback
        logger.error(f"Полный traceback: {traceback.format_exc()}")
        return False


async def get_referral_stats_for_user(db: AsyncSession, user_id: int) -> dict:
    
    try:
        from app.database.crud.referral import get_referral_earnings_sum
        from sqlalchemy import select, func
        from app.database.models import User
        
        invited_count_result = await db.execute(
            select(func.count(User.id)).where(User.referred_by_id == user_id)
        )
        invited_count = invited_count_result.scalar() or 0
        
        paid_referrals_result = await db.execute(
            select(func.count(User.id)).where(
                User.referred_by_id == user_id,
                User.has_had_paid_subscription == True
            )
        )
        paid_referrals_count = paid_referrals_result.scalar() or 0
        
        total_earned = await get_referral_earnings_sum(db, user_id) or 0
        
        from datetime import datetime, timedelta
        month_ago = datetime.utcnow() - timedelta(days=30)
        month_earned = await get_referral_earnings_sum(db, user_id, start_date=month_ago) or 0
        
        return {
            "invited_count": invited_count,
            "paid_referrals_count": paid_referrals_count,
            "total_earned_kopeks": total_earned,
            "month_earned_kopeks": month_earned
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики рефералов: {e}")
        return {
            "invited_count": 0,
            "paid_referrals_count": 0,
            "total_earned_kopeks": 0,
            "month_earned_kopeks": 0
        }
