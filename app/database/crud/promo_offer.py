from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    PromoOffer,
    PromoOfferActivation,
    PromoOfferDelivery,
)


async def create_promo_offer(
    db: AsyncSession,
    *,
    title: str,
    message_text: str,
    button_text: str,
    offer_type: str,
    target_segments: Iterable[str],
    starts_at: datetime,
    expires_at: datetime,
    discount_percent: int = 0,
    bonus_amount_kopeks: int = 0,
    discount_valid_hours: int = 0,
    test_access_hours: int = 0,
    test_squad_uuids: Optional[Iterable[str]] = None,
    created_by: Optional[int] = None,
) -> PromoOffer:
    offer = PromoOffer(
        title=title,
        message_text=message_text,
        button_text=button_text,
        offer_type=offer_type,
        target_segments=list(target_segments or []),
        starts_at=starts_at,
        expires_at=expires_at,
        discount_percent=discount_percent,
        bonus_amount_kopeks=bonus_amount_kopeks,
        discount_valid_hours=discount_valid_hours,
        test_access_hours=test_access_hours,
        test_squad_uuids=list(test_squad_uuids or []),
        created_by=created_by,
    )
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return offer


async def list_promo_offers(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[PromoOffer]:
    stmt = (
        select(PromoOffer)
        .order_by(PromoOffer.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def count_promo_offers(db: AsyncSession) -> int:
    from sqlalchemy import func

    result = await db.execute(select(func.count(PromoOffer.id)))
    return int(result.scalar_one() or 0)


async def get_promo_offer_by_id(
    db: AsyncSession,
    offer_id: int,
    *,
    with_relations: bool = False,
) -> Optional[PromoOffer]:
    stmt = select(PromoOffer).where(PromoOffer.id == offer_id)
    if with_relations:
        stmt = stmt.options(
            selectinload(PromoOffer.deliveries),
            selectinload(PromoOffer.activations),
        )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_promo_offer(
    db: AsyncSession,
    offer: PromoOffer,
    **fields,
) -> PromoOffer:
    for key, value in fields.items():
        setattr(offer, key, value)
    await db.commit()
    await db.refresh(offer)
    return offer


async def get_delivery_by_offer_and_user(
    db: AsyncSession,
    offer_id: int,
    user_id: int,
) -> Optional[PromoOfferDelivery]:
    result = await db.execute(
        select(PromoOfferDelivery)
        .where(
            PromoOfferDelivery.offer_id == offer_id,
            PromoOfferDelivery.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def create_delivery(
    db: AsyncSession,
    *,
    offer_id: int,
    user_id: int,
    discount_offer_id: Optional[int] = None,
    status: str = "sent",
    error_message: Optional[str] = None,
) -> PromoOfferDelivery:
    delivery = PromoOfferDelivery(
        offer_id=offer_id,
        user_id=user_id,
        discount_offer_id=discount_offer_id,
        status=status,
        error_message=error_message,
    )
    db.add(delivery)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        result = await db.execute(
            select(PromoOfferDelivery)
            .where(
                PromoOfferDelivery.offer_id == offer_id,
                PromoOfferDelivery.user_id == user_id,
            )
        )
        delivery = result.scalar_one_or_none()
        if delivery and error_message:
            delivery.status = status
            delivery.error_message = error_message
            await db.commit()
        return delivery
    await db.refresh(delivery)
    return delivery


async def mark_delivery_failed(
    db: AsyncSession,
    delivery: PromoOfferDelivery,
    error_message: str,
) -> PromoOfferDelivery:
    delivery.status = "failed"
    delivery.error_message = error_message
    await db.commit()
    await db.refresh(delivery)
    return delivery


async def mark_delivery_activated(
    db: AsyncSession,
    delivery: PromoOfferDelivery,
    activated_at: Optional[datetime] = None,
) -> PromoOfferDelivery:
    delivery.activated_at = activated_at or datetime.utcnow()
    await db.commit()
    await db.refresh(delivery)
    return delivery


async def create_activation(
    db: AsyncSession,
    *,
    offer_id: int,
    user_id: int,
    subscription_id: Optional[int],
    discount_offer_id: Optional[int],
    payload: Optional[dict] = None,
    expires_at: Optional[datetime] = None,
) -> PromoOfferActivation:
    activation = PromoOfferActivation(
        offer_id=offer_id,
        user_id=user_id,
        subscription_id=subscription_id,
        discount_offer_id=discount_offer_id,
        payload=payload or {},
        expires_at=expires_at,
    )
    db.add(activation)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        result = await db.execute(
            select(PromoOfferActivation)
            .where(
                PromoOfferActivation.offer_id == offer_id,
                PromoOfferActivation.user_id == user_id,
            )
        )
        activation = result.scalar_one_or_none()
        return activation
    await db.refresh(activation)
    return activation


async def get_activation_by_offer_and_user(
    db: AsyncSession,
    offer_id: int,
    user_id: int,
) -> Optional[PromoOfferActivation]:
    result = await db.execute(
        select(PromoOfferActivation)
        .where(
            PromoOfferActivation.offer_id == offer_id,
            PromoOfferActivation.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def get_delivery_by_discount_offer(
    db: AsyncSession,
    discount_offer_id: int,
) -> Optional[PromoOfferDelivery]:
    result = await db.execute(
        select(PromoOfferDelivery)
        .where(PromoOfferDelivery.discount_offer_id == discount_offer_id)
    )
    return result.scalar_one_or_none()


async def get_expired_test_activations(
    db: AsyncSession,
    *,
    now: Optional[datetime] = None,
) -> list[PromoOfferActivation]:
    now = now or datetime.utcnow()
    stmt = (
        select(PromoOfferActivation)
        .options(
            selectinload(PromoOfferActivation.offer),
            selectinload(PromoOfferActivation.user),
            selectinload(PromoOfferActivation.subscription),
        )
        .where(
            PromoOfferActivation.expires_at.is_not(None),
            PromoOfferActivation.expires_at <= now,
            PromoOfferActivation.revoked_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def mark_activation_revoked(
    db: AsyncSession,
    activation: PromoOfferActivation,
    *,
    revoked_at: Optional[datetime] = None,
) -> PromoOfferActivation:
    activation.revoked_at = revoked_at or datetime.utcnow()
    await db.commit()
    await db.refresh(activation)
    return activation
