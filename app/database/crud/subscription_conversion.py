import logging
from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SubscriptionConversion, User

logger = logging.getLogger(__name__)


async def create_subscription_conversion(
    db: AsyncSession,
    user_id: int,
    trial_duration_days: int,
    payment_method: str,
    first_payment_amount_kopeks: int,
    first_paid_period_days: int
) -> SubscriptionConversion:
    
    conversion = SubscriptionConversion(
        user_id=user_id,
        converted_at=datetime.utcnow(),
        trial_duration_days=trial_duration_days,
        payment_method=payment_method,
        first_payment_amount_kopeks=first_payment_amount_kopeks,
        first_paid_period_days=first_paid_period_days
    )
    
    db.add(conversion)
    await db.commit()
    await db.refresh(conversion)
    
    logger.info(f"✅ Создана запись о конверсии для пользователя {user_id}: {trial_duration_days} дн. → {first_paid_period_days} дн. за {first_payment_amount_kopeks/100}₽")
    
    return conversion


async def get_conversion_by_user_id(
    db: AsyncSession,
    user_id: int
) -> Optional[SubscriptionConversion]:
    
    result = await db.execute(
        select(SubscriptionConversion)
        .where(SubscriptionConversion.user_id == user_id)
        .order_by(SubscriptionConversion.converted_at.desc())
        .limit(1)
    )
    
    return result.scalar_one_or_none()


async def get_conversion_statistics(db: AsyncSession) -> dict:
    from app.database.models import Subscription

    # Подсчитываем уникальных пользователей, у которых когда-либо был триал
    users_with_trial_result = await db.execute(
        select(func.count(func.distinct(Subscription.user_id)))
        .where(Subscription.is_trial == True)
    )
    users_with_trial = users_with_trial_result.scalar() or 0

    # Подсчитываем пользователей, которые конвертировались из триала в платные
    # Это пользователи, у которых был триал И has_had_paid_subscription = True
    converted_users_result = await db.execute(
        select(func.count(func.distinct(User.id)))
        .join(Subscription, Subscription.user_id == User.id)
        .where(
            Subscription.is_trial == True,
            User.has_had_paid_subscription == True
        )
    )
    converted_users = converted_users_result.scalar() or 0

    # Расчёт конверсии: (конвертировавшиеся из триала) / (всего использовавших триал) * 100
    if users_with_trial > 0:
        conversion_rate = round((converted_users / users_with_trial) * 100, 1)
    else:
        conversion_rate = 0.0

    # Общее количество записей о конверсиях
    total_conversions_result = await db.execute(
        select(func.count(SubscriptionConversion.id))
    )
    total_conversions = total_conversions_result.scalar() or 0

    avg_trial_duration_result = await db.execute(
        select(func.avg(SubscriptionConversion.trial_duration_days))
    )
    avg_trial_duration = avg_trial_duration_result.scalar() or 0

    avg_first_payment_result = await db.execute(
        select(func.avg(SubscriptionConversion.first_payment_amount_kopeks))
    )
    avg_first_payment = avg_first_payment_result.scalar() or 0

    month_ago = datetime.utcnow() - timedelta(days=30)
    month_conversions_result = await db.execute(
        select(func.count(SubscriptionConversion.id))
        .where(SubscriptionConversion.converted_at >= month_ago)
    )
    month_conversions = month_conversions_result.scalar() or 0

    logger.info(f"📊 Статистика конверсий:")
    logger.info(f"   Пользователей с триалом: {users_with_trial}")
    logger.info(f"   Конвертировалось в платные: {converted_users}")
    logger.info(f"   Рассчитанная конверсия: {conversion_rate}%")

    return {
        "total_conversions": total_conversions,
        "conversion_rate": conversion_rate,
        "users_with_trial": users_with_trial,
        "converted_users": converted_users,
        "avg_trial_duration_days": round(avg_trial_duration, 1),
        "avg_first_payment_rubles": round((avg_first_payment or 0) / 100, 2),
        "month_conversions": month_conversions
    }


async def get_users_had_trial_count(db: AsyncSession) -> int:
    
    conversions_count_result = await db.execute(
        select(func.count(func.distinct(SubscriptionConversion.user_id)))
    )
    conversions_count = conversions_count_result.scalar()
    
    paid_users_result = await db.execute(
        select(func.count(User.id))
        .where(User.has_had_paid_subscription == True)
    )
    paid_users_count = paid_users_result.scalar()
    
    return max(conversions_count, paid_users_count)
