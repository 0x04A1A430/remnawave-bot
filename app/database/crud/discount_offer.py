from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import DiscountOffer


async def upsert_discount_offer(
    db: AsyncSession,
    *,
    user_id: int,
    subscription_id: Optional[int],
    notification_type: str,
    discount_percent: int,
    bonus_amount_kopeks: int,
    valid_hours: int,
    effect_type: str = "balance_bonus",
    extra_data: Optional[dict] = None,
) -> DiscountOffer:
    """Create or refresh a discount offer for a user."""

    expires_at = datetime.utcnow() + timedelta(hours=valid_hours)

    result = await db.execute(
        select(DiscountOffer)
        .where(
            DiscountOffer.user_id == user_id,
            DiscountOffer.notification_type == notification_type,
            DiscountOffer.is_active == True,  # noqa: E712
        )
        .order_by(DiscountOffer.created_at.desc())
    )
    offer = result.scalars().first()

    if offer and offer.claimed_at is None:
        offer.discount_percent = discount_percent
        offer.bonus_amount_kopeks = bonus_amount_kopeks
        offer.expires_at = expires_at
        offer.subscription_id = subscription_id
        offer.effect_type = effect_type
        offer.extra_data = extra_data
    else:
        offer = DiscountOffer(
            user_id=user_id,
            subscription_id=subscription_id,
            notification_type=notification_type,
            discount_percent=discount_percent,
            bonus_amount_kopeks=bonus_amount_kopeks,
            expires_at=expires_at,
            is_active=True,
            effect_type=effect_type,
            extra_data=extra_data,
        )
        db.add(offer)

    await db.commit()
    await db.refresh(offer)
    return offer


async def get_offer_by_id(db: AsyncSession, offer_id: int) -> Optional[DiscountOffer]:
    result = await db.execute(
        select(DiscountOffer).where(DiscountOffer.id == offer_id)
    )
    return result.scalar_one_or_none()


async def mark_offer_claimed(db: AsyncSession, offer: DiscountOffer) -> DiscountOffer:
    offer.claimed_at = datetime.utcnow()
    offer.is_active = False
    await db.commit()
    await db.refresh(offer)
    return offer


async def deactivate_expired_offers(db: AsyncSession) -> int:
    now = datetime.utcnow()
    result = await db.execute(
        select(DiscountOffer).where(
            DiscountOffer.is_active == True,  # noqa: E712
            DiscountOffer.expires_at < now,
        )
    )
    offers = result.scalars().all()
    if not offers:
        return 0

    count = 0
    for offer in offers:
        offer.is_active = False
        count += 1

    await db.commit()
    return count
