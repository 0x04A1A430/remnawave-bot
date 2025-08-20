import logging
from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy import select, and_, or_, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Transaction, TransactionType, PaymentMethod, User

logger = logging.getLogger(__name__)


async def create_transaction(
    db: AsyncSession,
    user_id: int,
    type: TransactionType,
    amount_kopeks: int,
    description: str,
    payment_method: Optional[PaymentMethod] = None,
    external_id: Optional[str] = None,
    is_completed: bool = True
) -> Transaction:
    
    transaction = Transaction(
        user_id=user_id,
        type=type.value,
        amount_kopeks=amount_kopeks,
        description=description,
        payment_method=payment_method.value if payment_method else None,
        external_id=external_id,
        is_completed=is_completed,
        completed_at=datetime.utcnow() if is_completed else None
    )
    
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    
    logger.info(f"💳 Создана транзакция: {type.value} на {amount_kopeks/100}₽ для пользователя {user_id}")
    return transaction


async def get_transaction_by_id(db: AsyncSession, transaction_id: int) -> Optional[Transaction]:
    result = await db.execute(
        select(Transaction)
        .options(selectinload(Transaction.user))
        .where(Transaction.id == transaction_id)
    )
    return result.scalar_one_or_none()


async def get_transaction_by_external_id(
    db: AsyncSession, 
    external_id: str, 
    payment_method: PaymentMethod
) -> Optional[Transaction]:
    result = await db.execute(
        select(Transaction)
        .where(
            and_(
                Transaction.external_id == external_id,
                Transaction.payment_method == payment_method.value
            )
        )
    )
    return result.scalar_one_or_none()


async def get_user_transactions(
    db: AsyncSession, 
    user_id: int, 
    limit: int = 50,
    offset: int = 0
) -> List[Transaction]:
    
    result = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


async def get_user_transactions_count(
    db: AsyncSession,
    user_id: int,
    transaction_type: Optional[TransactionType] = None
) -> int:
    
    query = select(func.count(Transaction.id)).where(Transaction.user_id == user_id)
    
    if transaction_type:
        query = query.where(Transaction.type == transaction_type.value)
    
    result = await db.execute(query)
    return result.scalar()


async def complete_transaction(db: AsyncSession, transaction: Transaction) -> Transaction:
    
    transaction.is_completed = True
    transaction.completed_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(transaction)
    
    logger.info(f"✅ Транзакция {transaction.id} завершена")
    return transaction


async def get_pending_transactions(db: AsyncSession) -> List[Transaction]:
    
    result = await db.execute(
        select(Transaction)
        .options(selectinload(Transaction.user))
        .where(Transaction.is_completed == False)
        .order_by(Transaction.created_at)
    )
    return result.scalars().all()


async def get_transactions_statistics(
    db: AsyncSession,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> dict:
    
    if not start_date:
        start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if not end_date:
        end_date = datetime.utcnow()
    
    income_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(
            and_(
                Transaction.type == TransactionType.DEPOSIT.value,
                Transaction.is_completed == True,
                Transaction.created_at >= start_date,
                Transaction.created_at <= end_date
            )
        )
    )
    total_income = income_result.scalar()
    
    expenses_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(
            and_(
                Transaction.type == TransactionType.WITHDRAWAL.value,
                Transaction.is_completed == True,
                Transaction.created_at >= start_date,
                Transaction.created_at <= end_date
            )
        )
    )
    total_expenses = expenses_result.scalar()
    
    subscription_income_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(
            and_(
                Transaction.type == TransactionType.SUBSCRIPTION_PAYMENT.value,
                Transaction.is_completed == True,
                Transaction.created_at >= start_date,
                Transaction.created_at <= end_date
            )
        )
    )
    subscription_income = subscription_income_result.scalar()
    
    transactions_count_result = await db.execute(
        select(
            Transaction.type,
            func.count(Transaction.id).label('count'),
            func.coalesce(func.sum(Transaction.amount_kopeks), 0).label('total_amount')
        )
        .where(
            and_(
                Transaction.is_completed == True,
                Transaction.created_at >= start_date,
                Transaction.created_at <= end_date
            )
        )
        .group_by(Transaction.type)
    )
    transactions_by_type = {row.type: {"count": row.count, "amount": row.total_amount} 
                           for row in transactions_count_result}
    
    payment_methods_result = await db.execute(
        select(
            Transaction.payment_method,
            func.count(Transaction.id).label('count'),
            func.coalesce(func.sum(Transaction.amount_kopeks), 0).label('total_amount')
        )
        .where(
            and_(
                Transaction.type == TransactionType.DEPOSIT.value,
                Transaction.is_completed == True,
                Transaction.created_at >= start_date,
                Transaction.created_at <= end_date
            )
        )
        .group_by(Transaction.payment_method)
    )
    payment_methods = {row.payment_method: {"count": row.count, "amount": row.total_amount} 
                      for row in payment_methods_result}
    
    today = datetime.utcnow().date()
    today_result = await db.execute(
        select(func.count(Transaction.id))
        .where(
            and_(
                Transaction.is_completed == True,
                Transaction.created_at >= today
            )
        )
    )
    transactions_today = today_result.scalar()
    
    today_income_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(
            and_(
                Transaction.type == TransactionType.DEPOSIT.value,
                Transaction.is_completed == True,
                Transaction.created_at >= today
            )
        )
    )
    income_today = today_income_result.scalar()
    
    return {
        "period": {
            "start_date": start_date,
            "end_date": end_date
        },
        "totals": {
            "income_kopeks": total_income,
            "expenses_kopeks": total_expenses,
            "profit_kopeks": total_income - total_expenses,
            "subscription_income_kopeks": subscription_income
        },
        "today": {
            "transactions_count": transactions_today,
            "income_kopeks": income_today
        },
        "by_type": transactions_by_type,
        "by_payment_method": payment_methods
    }


async def get_revenue_by_period(
    db: AsyncSession,
    days: int = 30
) -> List[dict]:
    
    start_date = datetime.utcnow() - timedelta(days=days)
    
    result = await db.execute(
        select(
            func.date(Transaction.created_at).label('date'),
            func.coalesce(func.sum(Transaction.amount_kopeks), 0).label('amount')
        )
        .where(
            and_(
                Transaction.type == TransactionType.DEPOSIT.value,
                Transaction.is_completed == True,
                Transaction.created_at >= start_date
            )
        )
        .group_by(func.date(Transaction.created_at))
        .order_by(func.date(Transaction.created_at))
    )
    
    return [{"date": row.date, "amount_kopeks": row.amount} for row in result]