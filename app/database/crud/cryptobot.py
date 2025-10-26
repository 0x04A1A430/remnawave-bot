import logging
from datetime import datetime
from typing import Optional, List
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import CryptoBotPayment

logger = logging.getLogger(__name__)


async def create_cryptobot_payment(
    db: AsyncSession,
    user_id: int,
    invoice_id: str,
    amount: str,
    asset: str,
    status: str = "active",
    description: Optional[str] = None,
    payload: Optional[str] = None,
    bot_invoice_url: Optional[str] = None,
    mini_app_invoice_url: Optional[str] = None,
    web_app_invoice_url: Optional[str] = None
) -> CryptoBotPayment:
    
    payment = CryptoBotPayment(
        user_id=user_id,
        invoice_id=invoice_id,
        amount=amount,
        asset=asset,
        status=status,
        description=description,
        payload=payload,
        bot_invoice_url=bot_invoice_url,
        mini_app_invoice_url=mini_app_invoice_url,
        web_app_invoice_url=web_app_invoice_url
    )
    
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    
    logger.info(f"Создан CryptoBot платеж: {invoice_id} на {amount} {asset} для пользователя {user_id}")
    return payment


async def get_cryptobot_payment_by_invoice_id(
    db: AsyncSession, 
    invoice_id: str
) -> Optional[CryptoBotPayment]:
    
    result = await db.execute(
        select(CryptoBotPayment)
        .options(selectinload(CryptoBotPayment.user))
        .where(CryptoBotPayment.invoice_id == invoice_id)
    )
    return result.scalar_one_or_none()


async def get_cryptobot_payment_by_id(
    db: AsyncSession, 
    payment_id: int
) -> Optional[CryptoBotPayment]:
    
    result = await db.execute(
        select(CryptoBotPayment)
        .options(selectinload(CryptoBotPayment.user))
        .where(CryptoBotPayment.id == payment_id)
    )
    return result.scalar_one_or_none()


async def update_cryptobot_payment_status(
    db: AsyncSession,
    invoice_id: str,
    status: str,
    paid_at: Optional[datetime] = None
) -> Optional[CryptoBotPayment]:
    
    payment = await get_cryptobot_payment_by_invoice_id(db, invoice_id)
    
    if not payment:
        return None
    
    payment.status = status
    payment.updated_at = datetime.utcnow()
    
    if status == "paid" and paid_at:
        payment.paid_at = paid_at
    
    await db.commit()
    await db.refresh(payment)
    
    logger.info(f"Обновлен статус CryptoBot платежа {invoice_id}: {status}")
    return payment


async def link_cryptobot_payment_to_transaction(
    db: AsyncSession,
    invoice_id: str,
    transaction_id: int
) -> Optional[CryptoBotPayment]:
    
    payment = await get_cryptobot_payment_by_invoice_id(db, invoice_id)
    
    if not payment:
        return None
    
    payment.transaction_id = transaction_id
    payment.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(payment)
    
    logger.info(f"Связан CryptoBot платеж {invoice_id} с транзакцией {transaction_id}")
    return payment


async def get_user_cryptobot_payments(
    db: AsyncSession,
    user_id: int,
    limit: int = 50,
    offset: int = 0
) -> List[CryptoBotPayment]:
    
    result = await db.execute(
        select(CryptoBotPayment)
        .where(CryptoBotPayment.user_id == user_id)
        .order_by(CryptoBotPayment.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


async def get_pending_cryptobot_payments(
    db: AsyncSession,
    older_than_hours: int = 24
) -> List[CryptoBotPayment]:
    
    from datetime import timedelta
    cutoff_time = datetime.utcnow() - timedelta(hours=older_than_hours)
    
    result = await db.execute(
        select(CryptoBotPayment)
        .options(selectinload(CryptoBotPayment.user))
        .where(
            and_(
                CryptoBotPayment.status == "active",
                CryptoBotPayment.created_at < cutoff_time
            )
        )
        .order_by(CryptoBotPayment.created_at)
    )
    return result.scalars().all()
