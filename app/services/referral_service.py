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
        
        await create_referral_earning(
            db=db,
            user_id=referrer_id,
            referral_id=new_user_id,
            amount_kopeks=0,
            reason="referral_registration_pending"
        )
        
        logger.info(f"✅ Зарегистрирован реферал {new_user_id} для {referrer_id}. Бонусы будут выданы после пополнения.")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка обработки реферальной регистрации: {e}")
        return False


async def process_referral_topup(
    db: AsyncSession,
    user_id: int,
    topup_amount_kopeks: int
):
    try:
        user = await get_user_by_id(db, user_id)
        if not user or not user.referred_by_id:
            logger.info(f"Пользователь {user_id} не является рефералом")
            return True
        
        if topup_amount_kopeks < settings.REFERRAL_MINIMUM_TOPUP_KOPEKS:
            logger.info(f"Пополнение {user_id} на {topup_amount_kopeks/100}₽ меньше минимума")
            return True
        
        referrer = await get_user_by_id(db, user.referred_by_id)
        if not referrer:
            logger.error(f"Реферер {user.referred_by_id} не найден")
            return False
        
        if not user.has_made_first_topup:
            user.has_made_first_topup = True
            await db.commit()
            
            if settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS > 0:
                await add_user_balance(
                    db, user, settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS,
                    f"Бонус за первое пополнение по реферальной программе"
                )
                logger.info(f"💰 Реферал {user_id} получил бонус {settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS/100}₽")
            
            if settings.REFERRAL_INVITER_BONUS_KOPEKS > 0:
                await add_user_balance(
                    db, referrer, settings.REFERRAL_INVITER_BONUS_KOPEKS,
                    f"Бонус за первое пополнение реферала {user.full_name}"
                )
                
                await create_referral_earning(
                    db=db,
                    user_id=referrer.id,
                    referral_id=user_id,
                    amount_kopeks=settings.REFERRAL_INVITER_BONUS_KOPEKS,
                    reason="referral_first_topup"
                )
                logger.info(f"💰 Реферер {referrer.telegram_id} получил бонус {settings.REFERRAL_INVITER_BONUS_KOPEKS/100}₽")
        else:
            if settings.REFERRAL_COMMISSION_PERCENT > 0:
                commission_amount = int(topup_amount_kopeks * settings.REFERRAL_COMMISSION_PERCENT / 100)
                
                if commission_amount > 0:
                    await add_user_balance(
                        db, referrer, commission_amount,
                        f"Комиссия {settings.REFERRAL_COMMISSION_PERCENT}% с пополнения {user.full_name}"
                    )
                    
                    await create_referral_earning(
                        db=db,
                        user_id=referrer.id,
                        referral_id=user_id,
                        amount_kopeks=commission_amount,
                        reason="referral_commission_topup"
                    )
                    
                    logger.info(f"💰 Комиссия с пополнения: {referrer.telegram_id} получил {commission_amount/100}₽")
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка обработки пополнения реферала: {e}")
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
            return True
        
        referrer = await get_user_by_id(db, user.referred_by_id)
        if not referrer:
            logger.error(f"Реферер {user.referred_by_id} не найден")
            return False
        
        if not (0 <= settings.REFERRAL_COMMISSION_PERCENT <= 100):
            logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: REFERRAL_COMMISSION_PERCENT = {settings.REFERRAL_COMMISSION_PERCENT} некорректный!")
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
