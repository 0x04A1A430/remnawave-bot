import logging
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import ReferralEarning, User

logger = logging.getLogger(__name__)


async def create_referral_earning(
    db: AsyncSession,
    user_id: int,
    referral_id: int,
    amount_kopeks: int,
    reason: str,
    referral_transaction_id: Optional[int] = None
) -> ReferralEarning:
    
    earning = ReferralEarning(
        user_id=user_id,
        referral_id=referral_id,
        amount_kopeks=amount_kopeks,
        reason=reason,
        referral_transaction_id=referral_transaction_id
    )
    
    db.add(earning)
    await db.commit()
    await db.refresh(earning)
    
    logger.info(f"💰 Создан реферальный заработок: {amount_kopeks/100}₽ для пользователя {user_id}")
    return earning


async def get_referral_earnings_by_user(
    db: AsyncSession,
    user_id: int,
    limit: int = 50,
    offset: int = 0
) -> List[ReferralEarning]:
    
    result = await db.execute(
        select(ReferralEarning)
        .options(
            selectinload(ReferralEarning.referral),
            selectinload(ReferralEarning.referral_transaction)
        )
        .where(ReferralEarning.user_id == user_id)
        .order_by(ReferralEarning.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


async def get_referral_earnings_by_referral(
    db: AsyncSession,
    referral_id: int
) -> List[ReferralEarning]:
    
    result = await db.execute(
        select(ReferralEarning)
        .where(ReferralEarning.referral_id == referral_id)
        .order_by(ReferralEarning.created_at.desc())
    )
    return result.scalars().all()


async def get_referral_earnings_sum(
    db: AsyncSession,
    user_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> int:
    
    query = select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0)).where(
        ReferralEarning.user_id == user_id
    )
    
    if start_date:
        query = query.where(ReferralEarning.created_at >= start_date)
    
    if end_date:
        query = query.where(ReferralEarning.created_at <= end_date)
    
    result = await db.execute(query)
    return result.scalar()


async def get_referral_statistics(db: AsyncSession) -> dict:
    users_with_referrals_result = await db.execute(
        select(func.count(func.distinct(User.id)))
        .where(User.referred_by_id.isnot(None))
    )
    users_with_referrals = users_with_referrals_result.scalar()
    
    active_referrers_result = await db.execute(
        select(func.count(func.distinct(User.referred_by_id)))
        .where(User.referred_by_id.isnot(None))
    )
    active_referrers = active_referrers_result.scalar()
    
    referral_paid_result = await db.execute(
        select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0))
    )
    referral_paid = referral_paid_result.scalar()
    
    from app.database.models import Transaction, TransactionType
    transaction_paid_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(Transaction.type == TransactionType.REFERRAL_REWARD.value)
    )
    transaction_paid = transaction_paid_result.scalar()
    
    total_paid = referral_paid + transaction_paid
    
    top_referrers_result = await db.execute(
        select(
            User.referred_by_id.label('referrer_id'),
            func.count(User.id).label('referrals_count'),
            func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0).label('referral_earnings'),
            func.coalesce(func.sum(Transaction.amount_kopeks), 0).label('transaction_earnings')
        )
        .outerjoin(
            ReferralEarning, 
            ReferralEarning.user_id == User.referred_by_id
        )
        .outerjoin(
            Transaction,
            and_(
                Transaction.user_id == User.referred_by_id,
                Transaction.type == TransactionType.REFERRAL_REWARD.value
            )
        )
        .where(
            and_(
                User.referred_by_id.isnot(None),
                User.referred_by_id != User.id 
            )
        )
        .group_by(User.referred_by_id)
        .order_by(func.count(User.id).desc())
        .limit(5)
    )
    top_referrers_raw = top_referrers_result.all()
    
    top_referrers = []
    for row in top_referrers_raw:
        user_result = await db.execute(
            select(User.id, User.username, User.first_name, User.last_name, User.telegram_id)
            .where(User.id == row.referrer_id)
        )
        user = user_result.first()
        
        if user:
            display_name = ""
            if user.first_name:
                display_name = user.first_name
                if user.last_name:
                    display_name += f" {user.last_name}"
            elif user.username:
                display_name = f"@{user.username}"
            else:
                display_name = f"ID{user.telegram_id}"
            
            total_earned = (row.referral_earnings or 0) + (row.transaction_earnings or 0)
            
            top_referrers.append({
                "user_id": row.referrer_id,
                "display_name": display_name,
                "username": user.username,
                "total_earned_kopeks": total_earned,
                "referrals_count": row.referrals_count
            })
    
    today = datetime.utcnow().date()
    
    today_referral_earnings = await db.execute(
        select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0))
        .where(ReferralEarning.created_at >= today)
    )
    today_transaction_earnings = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(
            and_(
                Transaction.type == TransactionType.REFERRAL_REWARD.value,
                Transaction.created_at >= today
            )
        )
    )
    today_earnings = today_referral_earnings.scalar() + today_transaction_earnings.scalar()
    
    week_ago = datetime.utcnow() - timedelta(days=7)
    week_referral_earnings = await db.execute(
        select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0))
        .where(ReferralEarning.created_at >= week_ago)
    )
    week_transaction_earnings = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(
            and_(
                Transaction.type == TransactionType.REFERRAL_REWARD.value,
                Transaction.created_at >= week_ago
            )
        )
    )
    week_earnings = week_referral_earnings.scalar() + week_transaction_earnings.scalar()
    
    month_ago = datetime.utcnow() - timedelta(days=30)
    month_referral_earnings = await db.execute(
        select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0))
        .where(ReferralEarning.created_at >= month_ago)
    )
    month_transaction_earnings = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(
            and_(
                Transaction.type == TransactionType.REFERRAL_REWARD.value,
                Transaction.created_at >= month_ago
            )
        )
    )
    month_earnings = month_referral_earnings.scalar() + month_transaction_earnings.scalar()
    
    return {
        "users_with_referrals": users_with_referrals,
        "active_referrers": active_referrers,
        "total_paid_kopeks": total_paid,
        "today_earnings_kopeks": today_earnings,
        "week_earnings_kopeks": week_earnings,
        "month_earnings_kopeks": month_earnings,
        "top_referrers": top_referrers
    }


async def get_user_referral_stats(db: AsyncSession, user_id: int) -> dict:
    
    invited_count_result = await db.execute(
        select(func.count(User.id)).where(User.referred_by_id == user_id)
    )
    invited_count = invited_count_result.scalar()
    
    total_earned = await get_referral_earnings_sum(db, user_id)
    
    month_ago = datetime.utcnow() - timedelta(days=30)
    month_earned = await get_referral_earnings_sum(db, user_id, start_date=month_ago)
    
    active_referrals_result = await db.execute(
        select(func.count(User.id))
        .join(User.subscription)
        .where(
            and_(
                User.referred_by_id == user_id,
                User.subscription.has()
            )
        )
    )
    active_referrals = active_referrals_result.scalar()
    
    return {
        "invited_count": invited_count,
        "active_referrals": active_referrals,
        "total_earned_kopeks": total_earned,
        "month_earned_kopeks": month_earned
    }