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
    external_id: str
) -> Optional[Transaction]:
    """Получить транзакцию по external_id"""
    result = await db.execute(
        select(Transaction)
        .options(selectinload(Transaction.user))
        .where(Transaction.external_id == external_id)
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


async def find_tribute_transactions_by_payment_id(
    db: AsyncSession, 
    payment_id: str, 
    user_telegram_id: Optional[int] = None
) -> List[Transaction]:
    
    query = select(Transaction).options(selectinload(Transaction.user))
    
    conditions = [
        Transaction.external_id == f"tribute_{payment_id}",
        Transaction.external_id == f"donation_{payment_id}",
        Transaction.external_id == payment_id,
        Transaction.external_id.like(f"%{payment_id}%")
    ]
    
    query = query.where(
        and_(
            Transaction.payment_method == PaymentMethod.TRIBUTE.value,
            or_(*conditions)
        )
    )
    
    if user_telegram_id:
        from app.database.models import User
        query = query.join(User).where(User.telegram_id == user_telegram_id)
    
    result = await db.execute(query.order_by(Transaction.created_at.desc()))
    return result.scalars().all()


async def check_tribute_payment_duplicate(
    db: AsyncSession,
    payment_id: str,
    amount_kopeks: int,
    user_telegram_id: int
) -> Optional[Transaction]:
    
    transactions = await find_tribute_transactions_by_payment_id(
        db, payment_id, user_telegram_id
    )
    
    for transaction in transactions:
        if (transaction.amount_kopeks == amount_kopeks and 
            transaction.is_completed and
            transaction.user.telegram_id == user_telegram_id):
            return transaction
    
    return None


async def create_unique_tribute_transaction(
    db: AsyncSession,
    user_id: int,
    payment_id: str,
    amount_kopeks: int,
    description: str
) -> Transaction:
    
    external_id = f"tribute_{payment_id}"
    
    existing = await get_transaction_by_external_id(db, external_id)
    
    if existing:
        timestamp = int(datetime.utcnow().timestamp())
        external_id = f"tribute_{payment_id}_{amount_kopeks}_{timestamp}"
        
        logger.info(f"Создан уникальный external_id для избежания дубликатов: {external_id}")
    
    return await create_transaction(
        db=db,
        user_id=user_id,
        type=TransactionType.DEPOSIT,
        amount_kopeks=amount_kopeks,
        description=description,
        payment_method=PaymentMethod.TRIBUTE,
        external_id=external_id,
        is_completed=True
    )


class TransactionCRUD:
    
    async def create_transaction(
        self, 
        db: AsyncSession, 
        transaction_data: dict
    ) -> Optional[Transaction]:
        try:
            transaction = Transaction(
                user_id=transaction_data['user_id'],
                type=transaction_data['transaction_type'],
                amount_kopeks=transaction_data['amount_kopeks'],
                description=transaction_data['description'],
                external_id=transaction_data.get('external_id'),
                payment_method=transaction_data.get('payment_system'),
                is_completed=transaction_data.get('status') == 'completed'
            )
            
            db.add(transaction)
            await db.commit()
            await db.refresh(transaction)
            
            logger.info(f"Создана транзакция: {transaction.id} на {transaction.amount_kopeks} коп.")
            return transaction
            
        except Exception as e:
            logger.error(f"Ошибка создания транзакции: {e}")
            await db.rollback()
            return None
    
    async def get_transaction_by_external_id(
        self, 
        db: AsyncSession, 
        external_id: str
    ) -> Optional[Transaction]:
        return await get_transaction_by_external_id(db, external_id)
    
    async def update_transaction_status(
        self, 
        db: AsyncSession, 
        transaction_id: int, 
        status: str
    ) -> bool:
        try:
            result = await db.execute(
                select(Transaction).where(Transaction.id == transaction_id)
            )
            transaction = result.scalar_one_or_none()
            
            if not transaction:
                logger.error(f"Транзакция {transaction_id} не найдена")
                return False
            
            transaction.is_completed = (status == 'completed')
            if transaction.is_completed:
                transaction.completed_at = datetime.utcnow()
            
            await db.commit()
            logger.info(f"Статус транзакции {transaction_id} обновлен на {status}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обновления статуса транзакции: {e}")
            await db.rollback()
            return False
