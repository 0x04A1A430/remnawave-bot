"""CRUD операции для платежей KassaAI."""

import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import KassaAiPayment


logger = logging.getLogger(__name__)


async def create_kassa_ai_payment(
    db: AsyncSession,
    *,
    user_id: int,
    order_id: str,
    amount_kopeks: int,
    currency: str = 'RUB',
    description: str | None = None,
    payment_url: str | None = None,
    payment_system_id: int | None = None,
    expires_at: datetime | None = None,
    metadata_json: str | None = None,
) -> KassaAiPayment:
    """Создает запись о платеже KassaAI."""
    payment = KassaAiPayment(
        user_id=user_id,
        order_id=order_id,
        amount_kopeks=amount_kopeks,
        currency=currency,
        description=description,
        payment_url=payment_url,
        payment_system_id=payment_system_id,
        expires_at=expires_at,
        metadata_json=json.loads(metadata_json) if metadata_json else None,
        status='pending',
        is_paid=False,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    logger.info(f'Создан платеж KassaAI: order_id={order_id}, user_id={user_id}')
    return payment


async def get_kassa_ai_payment_by_order_id(db: AsyncSession, order_id: str) -> KassaAiPayment | None:
    """Получает платеж по order_id."""
    result = await db.execute(select(KassaAiPayment).where(KassaAiPayment.order_id == order_id))
    return result.scalar_one_or_none()


async def get_kassa_ai_payment_by_external_order_id(db: AsyncSession, kassa_ai_order_id: str) -> KassaAiPayment | None:
    """Получает платеж по ID от KassaAI (orderId)."""
    result = await db.execute(select(KassaAiPayment).where(KassaAiPayment.kassa_ai_order_id == kassa_ai_order_id))
    return result.scalar_one_or_none()


async def get_kassa_ai_payment_by_id(db: AsyncSession, payment_id: int) -> KassaAiPayment | None:
    """Получает платеж по ID."""
    result = await db.execute(select(KassaAiPayment).where(KassaAiPayment.id == payment_id))
    return result.scalar_one_or_none()


async def update_kassa_ai_payment_status(
    db: AsyncSession,
    payment: KassaAiPayment,
    *,
    status: str,
    is_paid: bool = False,
    kassa_ai_order_id: str | None = None,
    payment_system_id: int | None = None,
    callback_payload: dict | None = None,
    transaction_id: int | None = None,
) -> KassaAiPayment:
    """Обновляет статус платежа."""
    payment.status = status
    payment.is_paid = is_paid
    payment.updated_at = datetime.utcnow()

    if is_paid:
        payment.paid_at = datetime.utcnow()
    if kassa_ai_order_id:
        payment.kassa_ai_order_id = kassa_ai_order_id
    if payment_system_id is not None:
        payment.payment_system_id = payment_system_id
    if callback_payload:
        payment.callback_payload = callback_payload
    if transaction_id:
        payment.transaction_id = transaction_id

    await db.commit()
    await db.refresh(payment)
    logger.info(f'Обновлен статус платежа KassaAI: order_id={payment.order_id}, status={status}, is_paid={is_paid}')
    return payment


async def get_pending_kassa_ai_payments(db: AsyncSession, user_id: int) -> list[KassaAiPayment]:
    """Получает незавершенные платежи пользователя."""
    result = await db.execute(
        select(KassaAiPayment).where(
            KassaAiPayment.user_id == user_id,
            KassaAiPayment.status == 'pending',
            KassaAiPayment.is_paid == False,
        )
    )
    return list(result.scalars().all())


async def get_user_kassa_ai_payments(
    db: AsyncSession,
    user_id: int,
    limit: int = 10,
    offset: int = 0,
) -> list[KassaAiPayment]:
    """Получает платежи пользователя с пагинацией."""
    result = await db.execute(
        select(KassaAiPayment)
        .where(KassaAiPayment.user_id == user_id)
        .order_by(KassaAiPayment.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_expired_pending_kassa_ai_payments(
    db: AsyncSession,
) -> list[KassaAiPayment]:
    """Получает просроченные платежи в статусе pending."""
    now = datetime.utcnow()
    result = await db.execute(
        select(KassaAiPayment).where(
            KassaAiPayment.status == 'pending',
            KassaAiPayment.is_paid == False,
            KassaAiPayment.expires_at < now,
        )
    )
    return list(result.scalars().all())
