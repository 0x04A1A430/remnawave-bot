from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.crud.promo_offer_log import log_promo_offer_action
from app.database.models import DiscountOffer


logger = structlog.get_logger(__name__)


async def upsert_discount_offer(
    db: AsyncSession,
    *,
    user_id: int,
    subscription_id: int | None,
    notification_type: str,
    discount_percent: int,
    bonus_amount_kopeks: int,
    valid_hours: int,
    effect_type: str = 'percent_discount',
    extra_data: dict | None = None,
) -> DiscountOffer:
    """Create or refresh a discount offer for a user."""

    expires_at = datetime.now(UTC) + timedelta(hours=valid_hours)

    result = await db.execute(
        select(DiscountOffer)
        .where(
            DiscountOffer.user_id == user_id,
            DiscountOffer.notification_type == notification_type,
            DiscountOffer.is_active == True,
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


async def get_offer_by_id(db: AsyncSession, offer_id: int) -> DiscountOffer | None:
    result = await db.execute(
        select(DiscountOffer)
        .options(
            selectinload(DiscountOffer.user),
            selectinload(DiscountOffer.subscription),
        )
        .where(DiscountOffer.id == offer_id)
    )
    return result.scalar_one_or_none()


async def list_discount_offers(
    db: AsyncSession,
    *,
    offset: int = 0,
    limit: int = 50,
    user_id: int | None = None,
    notification_type: str | None = None,
    is_active: bool | None = None,
) -> list[DiscountOffer]:
    stmt = (
        select(DiscountOffer)
        .options(
            selectinload(DiscountOffer.user),
            selectinload(DiscountOffer.subscription),
        )
        .order_by(DiscountOffer.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    if user_id is not None:
        stmt = stmt.where(DiscountOffer.user_id == user_id)
    if notification_type:
        stmt = stmt.where(DiscountOffer.notification_type == notification_type)
    if is_active is not None:
        stmt = stmt.where(DiscountOffer.is_active == is_active)

    result = await db.execute(stmt)
    return result.scalars().all()


async def list_active_discount_offers_for_user(
    db: AsyncSession,
    user_id: int,
) -> list[DiscountOffer]:
    """Return active (not yet claimed) offers for a user."""

    now = datetime.now(UTC)
    stmt = (
        select(DiscountOffer)
        .options(
            selectinload(DiscountOffer.user),
            selectinload(DiscountOffer.subscription),
        )
        .where(
            DiscountOffer.user_id == user_id,
            DiscountOffer.is_active == True,
            DiscountOffer.expires_at > now,
        )
        .order_by(DiscountOffer.expires_at.asc())
    )

    result = await db.execute(stmt)
    return result.scalars().all()


async def count_discount_offers(
    db: AsyncSession,
    *,
    user_id: int | None = None,
    notification_type: str | None = None,
    is_active: bool | None = None,
) -> int:
    stmt = select(func.count(DiscountOffer.id))

    if user_id is not None:
        stmt = stmt.where(DiscountOffer.user_id == user_id)
    if notification_type:
        stmt = stmt.where(DiscountOffer.notification_type == notification_type)
    if is_active is not None:
        stmt = stmt.where(DiscountOffer.is_active == is_active)

    result = await db.execute(stmt)
    return int(result.scalar() or 0)


async def mark_offer_claimed(
    db: AsyncSession,
    offer: DiscountOffer,
    *,
    details: dict | None = None,
) -> DiscountOffer:
    offer.claimed_at = datetime.now(UTC)
    offer.is_active = False
    await db.commit()
    await db.refresh(offer)

    try:
        await log_promo_offer_action(
            db,
            user_id=offer.user_id,
            offer_id=offer.id,
            action='claimed',
            source=offer.notification_type,
            percent=offer.discount_percent,
            effect_type=offer.effect_type,
            details=details,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning('Failed to record promo offer claim log for offer', offer_id=offer.id, exc=exc)
        try:
            await db.rollback()
        except Exception as rollback_error:  # pragma: no cover - defensive logging
            logger.warning(
                'Failed to rollback session after promo offer claim log failure', rollback_error=rollback_error
            )

    return offer


async def deactivate_expired_offers(db: AsyncSession) -> int:
    now = datetime.now(UTC)
    result = await db.execute(
        select(DiscountOffer).where(
            DiscountOffer.is_active == True,
            DiscountOffer.expires_at < now,
        )
    )
    offers = result.scalars().all()
    if not offers:
        return 0

    count = 0
    log_payloads = []
    for offer in offers:
        offer.is_active = False
        count += 1
        log_payloads.append(
            {
                'user_id': offer.user_id,
                'offer_id': offer.id,
                'source': offer.notification_type,
                'percent': offer.discount_percent,
                'effect_type': offer.effect_type,
            }
        )

    await db.commit()

    for payload in log_payloads:
        if not payload.get('user_id'):
            continue
        try:
            await log_promo_offer_action(
                db,
                user_id=payload['user_id'],
                offer_id=payload['offer_id'],
                action='disabled',
                source=payload.get('source'),
                percent=payload.get('percent'),
                effect_type=payload.get('effect_type'),
                details={'reason': 'offer_expired'},
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                'Failed to record promo offer disable log for offer', payload=payload.get('offer_id'), exc=exc
            )
            try:
                await db.rollback()
            except Exception as rollback_error:  # pragma: no cover - defensive logging
                logger.warning(
                    'Failed to rollback session after promo offer disable log failure', rollback_error=rollback_error
                )

    return count


async def get_latest_claimed_offer_for_user(
    db: AsyncSession,
    user_id: int,
    source: str | None = None,
) -> DiscountOffer | None:
    stmt = (
        select(DiscountOffer)
        .where(
            DiscountOffer.user_id == user_id,
            DiscountOffer.claimed_at.isnot(None),
        )
        .order_by(DiscountOffer.claimed_at.desc())
    )

    if source:
        stmt = stmt.where(DiscountOffer.notification_type == source)

    result = await db.execute(stmt)
    return result.scalars().first()
