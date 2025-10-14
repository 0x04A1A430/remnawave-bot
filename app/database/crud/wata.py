"""CRUD-операции для платежей Wata Pay."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import WataPayment

logger = logging.getLogger(__name__)


async def create_wata_payment(
    db: AsyncSession,
    *,
    user_id: int,
    amount_kopeks: int,
    order_id: str,
    description: Optional[str],
    status: str,
    currency: str,
    payment_url: Optional[str],
    wata_link_id: Optional[str],
    success_redirect_url: Optional[str],
    fail_redirect_url: Optional[str],
    expiration_at: Optional[datetime],
    metadata: Optional[dict] = None,
) -> WataPayment:
    payment = WataPayment(
        user_id=user_id,
        amount_kopeks=amount_kopeks,
        order_id=order_id,
        description=description,
        status=status,
        currency=currency,
        payment_url=payment_url,
        wata_link_id=wata_link_id,
        success_redirect_url=success_redirect_url,
        fail_redirect_url=fail_redirect_url,
        expiration_at=expiration_at,
        metadata_json=metadata or {},
    )

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Создан Wata платеж #%s (order=%s) на сумму %s копеек для пользователя %s",
        payment.id,
        order_id,
        amount_kopeks,
        user_id,
    )

    return payment


async def get_wata_payment_by_local_id(
    db: AsyncSession,
    payment_id: int,
) -> Optional[WataPayment]:
    result = await db.execute(
        select(WataPayment).where(WataPayment.id == payment_id)
    )
    return result.scalar_one_or_none()


async def get_wata_payment_by_order_id(
    db: AsyncSession,
    order_id: str,
) -> Optional[WataPayment]:
    result = await db.execute(
        select(WataPayment).where(WataPayment.order_id == order_id)
    )
    return result.scalar_one_or_none()


async def get_wata_payment_by_link_id(
    db: AsyncSession,
    link_id: str,
) -> Optional[WataPayment]:
    result = await db.execute(
        select(WataPayment).where(WataPayment.wata_link_id == link_id)
    )
    return result.scalar_one_or_none()


async def update_wata_payment_status(
    db: AsyncSession,
    *,
    payment: WataPayment,
    status: Optional[str] = None,
    transaction_status: Optional[str] = None,
    is_paid: Optional[bool] = None,
    paid_at: Optional[datetime] = None,
    callback_payload: Optional[dict] = None,
    external_transaction_id: Optional[str] = None,
    payment_url: Optional[str] = None,
    last_status_payload: Optional[dict] = None,
) -> WataPayment:
    if status is not None:
        payment.status = status
    if transaction_status is not None:
        payment.transaction_status = transaction_status
    if is_paid is not None:
        payment.is_paid = is_paid
    if paid_at is not None:
        payment.paid_at = paid_at
    if callback_payload is not None:
        payment.callback_payload = callback_payload
    if external_transaction_id is not None:
        payment.external_transaction_id = external_transaction_id
    if payment_url is not None:
        payment.payment_url = payment_url
    if last_status_payload is not None:
        payment.last_status_payload = last_status_payload

    payment.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(payment)
    return payment


async def link_wata_payment_to_transaction(
    db: AsyncSession,
    *,
    payment: WataPayment,
    transaction_id: int,
) -> WataPayment:
    payment.transaction_id = transaction_id
    payment.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(payment)
    return payment
