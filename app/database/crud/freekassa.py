"""CRUD операции для платежей Freekassa."""

import json
import logging
from datetime import datetime
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import FreekassaPayment

logger = logging.getLogger(__name__)


async def create_freekassa_payment(
    db: AsyncSession,
    *,
    user_id: int,
    order_id: str,
    amount_kopeks: int,
    currency: str = "RUB",
    description: Optional[str] = None,
    payment_url: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    metadata_json: Optional[str] = None,
) -> FreekassaPayment:
    """Создает запись о платеже Freekassa."""
    payment = FreekassaPayment(
        user_id=user_id,
        order_id=order_id,
        amount_kopeks=amount_kopeks,
        currency=currency,
        description=description,
        payment_url=payment_url,
        expires_at=expires_at,
        metadata_json=json.loads(metadata_json) if metadata_json else None,
        status="pending",
        is_paid=False,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    logger.info(f"Создан платеж Freekassa: order_id={order_id}, user_id={user_id}")
    return payment


async def get_freekassa_payment_by_order_id(
    db: AsyncSession, order_id: str
) -> Optional[FreekassaPayment]:
    """Получает платеж по order_id."""
    result = await db.execute(
        select(FreekassaPayment).where(FreekassaPayment.order_id == order_id)
    )
    return result.scalar_one_or_none()


async def get_freekassa_payment_by_fk_order_id(
    db: AsyncSession, freekassa_order_id: str
) -> Optional[FreekassaPayment]:
    """Получает платеж по ID от Freekassa (intid)."""
    result = await db.execute(
        select(FreekassaPayment).where(
            FreekassaPayment.freekassa_order_id == freekassa_order_id
        )
    )
    return result.scalar_one_or_none()


async def get_freekassa_payment_by_id(
    db: AsyncSession, payment_id: int
) -> Optional[FreekassaPayment]:
    """Получает платеж по ID."""
    result = await db.execute(
        select(FreekassaPayment).where(FreekassaPayment.id == payment_id)
    )
    return result.scalar_one_or_none()


async def update_freekassa_payment_status(
    db: AsyncSession,
    payment: FreekassaPayment,
    *,
    status: str,
    is_paid: bool = False,
    freekassa_order_id: Optional[str] = None,
    payment_system_id: Optional[int] = None,
    callback_payload: Optional[dict] = None,
    transaction_id: Optional[int] = None,
) -> FreekassaPayment:
    """Обновляет статус платежа."""
    payment.status = status
    payment.is_paid = is_paid
    payment.updated_at = datetime.utcnow()

    if is_paid:
        payment.paid_at = datetime.utcnow()
    if freekassa_order_id:
        payment.freekassa_order_id = freekassa_order_id
    if payment_system_id is not None:
        payment.payment_system_id = payment_system_id
    if callback_payload:
        payment.callback_payload = callback_payload
    if transaction_id:
        payment.transaction_id = transaction_id

    await db.commit()
    await db.refresh(payment)
    logger.info(
        f"Обновлен статус платежа Freekassa: order_id={payment.order_id}, "
        f"status={status}, is_paid={is_paid}"
    )
    return payment


async def get_pending_freekassa_payments(
    db: AsyncSession, user_id: int
) -> List[FreekassaPayment]:
    """Получает незавершенные платежи пользователя."""
    result = await db.execute(
        select(FreekassaPayment).where(
            FreekassaPayment.user_id == user_id,
            FreekassaPayment.status == "pending",
            FreekassaPayment.is_paid == False,
        )
    )
    return list(result.scalars().all())


async def get_user_freekassa_payments(
    db: AsyncSession,
    user_id: int,
    limit: int = 10,
    offset: int = 0,
) -> List[FreekassaPayment]:
    """Получает платежи пользователя с пагинацией."""
    result = await db.execute(
        select(FreekassaPayment)
        .where(FreekassaPayment.user_id == user_id)
        .order_by(FreekassaPayment.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_expired_pending_payments(
    db: AsyncSession,
) -> List[FreekassaPayment]:
    """Получает просроченные платежи в статусе pending."""
    now = datetime.utcnow()
    result = await db.execute(
        select(FreekassaPayment).where(
            FreekassaPayment.status == "pending",
            FreekassaPayment.is_paid == False,
            FreekassaPayment.expires_at < now,
        )
    )
    return list(result.scalars().all())
