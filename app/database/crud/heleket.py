import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import HeleketPayment

logger = logging.getLogger(__name__)


async def create_heleket_payment(
    db: AsyncSession,
    *,
    user_id: int,
    uuid: str,
    order_id: str,
    amount: str,
    currency: str,
    status: str,
    payer_amount: Optional[str] = None,
    payer_currency: Optional[str] = None,
    exchange_rate: Optional[float] = None,
    discount_percent: Optional[int] = None,
    payment_url: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> HeleketPayment:
    payment = HeleketPayment(
        user_id=user_id,
        uuid=uuid,
        order_id=order_id,
        amount=amount,
        currency=currency,
        status=status,
        payer_amount=payer_amount,
        payer_currency=payer_currency,
        exchange_rate=exchange_rate,
        discount_percent=discount_percent,
        payment_url=payment_url,
        expires_at=expires_at,
        metadata_json=metadata or {},
    )

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Создан Heleket платеж: uuid=%s order_id=%s amount=%s %s для пользователя %s",
        uuid,
        order_id,
        amount,
        currency,
        user_id,
    )

    return payment


async def get_heleket_payment_by_uuid(
    db: AsyncSession,
    uuid: str,
) -> Optional[HeleketPayment]:
    result = await db.execute(
        select(HeleketPayment)
        .options(selectinload(HeleketPayment.user))
        .where(HeleketPayment.uuid == uuid)
    )
    return result.scalar_one_or_none()


async def get_heleket_payment_by_order_id(
    db: AsyncSession,
    order_id: str,
) -> Optional[HeleketPayment]:
    result = await db.execute(
        select(HeleketPayment)
        .options(selectinload(HeleketPayment.user))
        .where(HeleketPayment.order_id == order_id)
    )
    return result.scalar_one_or_none()


async def get_heleket_payment_by_id(
    db: AsyncSession,
    payment_id: int,
) -> Optional[HeleketPayment]:
    result = await db.execute(
        select(HeleketPayment)
        .options(selectinload(HeleketPayment.user))
        .where(HeleketPayment.id == payment_id)
    )
    return result.scalar_one_or_none()


async def update_heleket_payment(
    db: AsyncSession,
    uuid: str,
    *,
    status: Optional[str] = None,
    payer_amount: Optional[str] = None,
    payer_currency: Optional[str] = None,
    exchange_rate: Optional[float] = None,
    discount_percent: Optional[int] = None,
    paid_at: Optional[datetime] = None,
    payment_url: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[HeleketPayment]:
    payment = await get_heleket_payment_by_uuid(db, uuid)

    if not payment:
        logger.error("Heleket платеж с uuid=%s не найден", uuid)
        return None

    if status is not None:
        payment.status = status
    if payer_amount is not None:
        payment.payer_amount = payer_amount
    if payer_currency is not None:
        payment.payer_currency = payer_currency
    if exchange_rate is not None:
        payment.exchange_rate = exchange_rate
    if discount_percent is not None:
        payment.discount_percent = discount_percent
    if payment_url is not None:
        payment.payment_url = payment_url
    if metadata is not None:
        existing = dict(payment.metadata_json or {})
        existing.update(metadata)
        payment.metadata_json = existing
    if paid_at is not None:
        payment.paid_at = paid_at

    payment.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Обновлен Heleket платеж %s: статус=%s payer_amount=%s %s",
        uuid,
        payment.status,
        payment.payer_amount,
        payment.payer_currency,
    )

    return payment


async def link_heleket_payment_to_transaction(
    db: AsyncSession,
    uuid: str,
    transaction_id: int,
) -> Optional[HeleketPayment]:
    payment = await get_heleket_payment_by_uuid(db, uuid)

    if not payment:
        logger.error("Не найден Heleket платеж для связи с транзакцией: %s", uuid)
        return None

    payment.transaction_id = transaction_id
    payment.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Heleket платеж %s связан с транзакцией %s",
        uuid,
        transaction_id,
    )

    return payment
