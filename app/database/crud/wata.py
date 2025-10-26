"""CRUD helpers for WATA payment records."""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import WataPayment

logger = logging.getLogger(__name__)


async def create_wata_payment(
    db: AsyncSession,
    *,
    user_id: int,
    payment_link_id: str,
    amount_kopeks: int,
    currency: str,
    description: Optional[str],
    status: str,
    type_: Optional[str],
    url: Optional[str],
    order_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    expires_at: Optional[datetime] = None,
    terminal_public_id: Optional[str] = None,
    success_redirect_url: Optional[str] = None,
    fail_redirect_url: Optional[str] = None,
) -> WataPayment:
    payment = WataPayment(
        user_id=user_id,
        payment_link_id=payment_link_id,
        order_id=order_id,
        amount_kopeks=amount_kopeks,
        currency=currency,
        description=description,
        status=status,
        type=type_,
        url=url,
        metadata_json=metadata or {},
        expires_at=expires_at,
        terminal_public_id=terminal_public_id,
        success_redirect_url=success_redirect_url,
        fail_redirect_url=fail_redirect_url,
    )

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Создан Wata платеж #%s для пользователя %s: %s копеек (статус %s)",
        payment.id,
        user_id,
        amount_kopeks,
        status,
    )

    return payment


async def get_wata_payment_by_id(
    db: AsyncSession,
    payment_id: int,
) -> Optional[WataPayment]:
    result = await db.execute(
        select(WataPayment).where(WataPayment.id == payment_id)
    )
    return result.scalar_one_or_none()


async def get_wata_payment_by_link_id(
    db: AsyncSession,
    payment_link_id: str,
) -> Optional[WataPayment]:
    result = await db.execute(
        select(WataPayment).where(WataPayment.payment_link_id == payment_link_id)
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


async def update_wata_payment_status(
    db: AsyncSession,
    payment: WataPayment,
    *,
    status: Optional[str] = None,
    is_paid: Optional[bool] = None,
    paid_at: Optional[datetime] = None,
    last_status: Optional[str] = None,
    url: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    callback_payload: Optional[Dict[str, Any]] = None,
    terminal_public_id: Optional[str] = None,
) -> WataPayment:
    update_values: Dict[str, Any] = {}

    if status is not None:
        update_values["status"] = status
    if is_paid is not None:
        update_values["is_paid"] = is_paid
    if paid_at is not None:
        update_values["paid_at"] = paid_at
    if last_status is not None:
        update_values["last_status"] = last_status
    if url is not None:
        update_values["url"] = url
    if metadata is not None:
        update_values["metadata_json"] = metadata
    if callback_payload is not None:
        update_values["callback_payload"] = callback_payload
    if terminal_public_id is not None:
        update_values["terminal_public_id"] = terminal_public_id

    if not update_values:
        return payment

    await db.execute(
        update(WataPayment)
        .where(WataPayment.id == payment.id)
        .values(**update_values)
    )

    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Обновлен Wata платеж %s: статус=%s, is_paid=%s",
        payment.payment_link_id,
        payment.status,
        payment.is_paid,
    )

    return payment


async def link_wata_payment_to_transaction(
    db: AsyncSession,
    payment: WataPayment,
    transaction_id: int,
) -> WataPayment:
    await db.execute(
        update(WataPayment)
        .where(WataPayment.id == payment.id)
        .values(transaction_id=transaction_id)
    )
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Wata платеж %s привязан к транзакции %s",
        payment.payment_link_id,
        transaction_id,
    )

    return payment
