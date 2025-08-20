import logging
import random
import string
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database.models import User

logger = logging.getLogger(__name__)


async def mark_user_as_had_paid_subscription(
    db: AsyncSession,
    user: User
) -> None:
    if not user.has_had_paid_subscription:
        user.has_had_paid_subscription = True
        user.updated_at = datetime.utcnow()
        await db.commit()
        logger.info(f"🎯 Пользователь {user.telegram_id} отмечен как имевший платную подписку")


async def generate_unique_referral_code(db: AsyncSession, telegram_id: int) -> str:
    
    base_code = str(telegram_id)[-6:]
    
    for attempt in range(10):
        if attempt == 0:
            referral_code = base_code
        else:
            suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=2))
            referral_code = base_code + suffix
        
        result = await db.execute(
            select(User.id).where(User.referral_code == referral_code)
        )
        
        if not result.scalar():
            return referral_code
    
    import uuid
    return str(uuid.uuid4())[:8]


async def get_user_referral_summary(db: AsyncSession, user_id: int) -> dict:
    
    try:
        from app.services.referral_service import get_referral_stats_for_user
        from app.database.crud.referral import get_referral_earnings_by_user
        
        stats = await get_referral_stats_for_user(db, user_id)
        
        recent_earnings = await get_referral_earnings_by_user(db, user_id, limit=5)
        
        return {
            **stats,
            "recent_earnings": [
                {
                    "amount_kopeks": earning.amount_kopeks,
                    "reason": earning.reason,
                    "created_at": earning.created_at,
                    "referral_name": earning.referral.full_name if earning.referral else "Неизвестно"
                }
                for earning in recent_earnings
            ]
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения сводки рефералов: {e}")
        return {
            "invited_count": 0,
            "paid_referrals_count": 0,
            "total_earned_kopeks": 0,
            "month_earned_kopeks": 0,
            "recent_earnings": []
        }