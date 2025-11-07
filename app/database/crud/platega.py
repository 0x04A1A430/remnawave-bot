"""CRUD-операции для платежей Platega."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PlategaPayment

logger = logging.getLogger(__name__)


async def create_platega_payment(
    db: AsyncSession,
    *,
    user_id: int,
    amount_kopeks: int,
    currency: str,
    description: Optional[str],
    status: str,
    payment_method_code: int,
    correlation_id: str,
    platega_transaction_id: Optional[str],
    redirect_url: Optional[str],
    return_url: Optional[str],
    failed_url: Optional[str],
    payload: Optional[str],
    metadata: Optional[dict[str, Any]] = None,
    expires_at: Optional[datetime] = None,
) -> PlategaPayment:
    payment = PlategaPayment(
        user_id=user_id,
        amount_kopeks=amount_kopeks,
        currency=currency,
        description=description,
        status=status,
        payment_method_code=payment_method_code,
        correlation_id=correlation_id,
        platega_transaction_id=platega_transaction_id,
        redirect_url=redirect_url,
        return_url=return_url,
        failed_url=failed_url,
        payload=payload,
        metadata_json=metadata or {},
        expires_at=expires_at,
    )

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Создан Platega платеж #%s (tx=%s) на сумму %s копеек для пользователя %s",
        payment.id,
        platega_transaction_id,
        amount_kopeks,
        user_id,
    )

    return payment


async def get_platega_payment_by_id(
    db: AsyncSession, payment_id: int
) -> Optional[PlategaPayment]:
    result = await db.execute(
        select(PlategaPayment).where(PlategaPayment.id == payment_id)
    )
    return result.scalar_one_or_none()


async def get_platega_payment_by_id_for_update(
    db: AsyncSession, payment_id: int
) -> Optional[PlategaPayment]:
    result = await db.execute(
        select(PlategaPayment)
        .where(PlategaPayment.id == payment_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def get_platega_payment_by_transaction_id(
    db: AsyncSession, transaction_id: str
) -> Optional[PlategaPayment]:
    result = await db.execute(
        select(PlategaPayment).where(
            PlategaPayment.platega_transaction_id == transaction_id
        )
    )
    return result.scalar_one_or_none()


async def get_platega_payment_by_correlation_id(
    db: AsyncSession, correlation_id: str
) -> Optional[PlategaPayment]:
    result = await db.execute(
        select(PlategaPayment).where(
            PlategaPayment.correlation_id == correlation_id
        )
    )
    return result.scalar_one_or_none()


async def update_platega_payment(
    db: AsyncSession,
    *,
    payment: PlategaPayment,
    status: Optional[str] = None,
    is_paid: Optional[bool] = None,
    paid_at: Optional[datetime] = None,
    platega_transaction_id: Optional[str] = None,
    redirect_url: Optional[str] = None,
    callback_payload: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    expires_at: Optional[datetime] = None,
) -> PlategaPayment:
    if status is not None:
        payment.status = status
    if is_paid is not None:
        payment.is_paid = is_paid
    if paid_at is not None:
        payment.paid_at = paid_at
    if platega_transaction_id and not payment.platega_transaction_id:
        payment.platega_transaction_id = platega_transaction_id
    if redirect_url is not None:
        payment.redirect_url = redirect_url
    if callback_payload is not None:
        payment.callback_payload = callback_payload
    if metadata is not None:
        payment.metadata_json = metadata
    if expires_at is not None:
        payment.expires_at = expires_at

    payment.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(payment)
    return payment


async def link_platega_payment_to_transaction(
    db: AsyncSession,
    *,
    payment: PlategaPayment,
    transaction_id: int,
) -> PlategaPayment:
    payment.transaction_id = transaction_id
    payment.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(payment)
    return payment
