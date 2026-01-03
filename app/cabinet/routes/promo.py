"""Promo offers routes for cabinet - personal discounts and offers."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.database.models import User, DiscountOffer
from app.database.crud.discount_offer import (
    get_offer_by_id,
    mark_offer_claimed,
)
from app.database.crud.promo_offer_template import get_promo_offer_template_by_id
from app.services.promo_offer_service import promo_offer_service
from app.config import settings

from ..dependencies import get_cabinet_db, get_current_cabinet_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/promo", tags=["Cabinet Promo"])


# ============ Schemas ============

class PromoOfferInfo(BaseModel):
    """Promo offer info."""
    id: int
    notification_type: str
    discount_percent: Optional[int] = None
    effect_type: str
    expires_at: datetime
    is_active: bool
    is_claimed: bool
    claimed_at: Optional[datetime] = None
    extra_data: Optional[Dict[str, Any]] = None


class ActiveDiscountInfo(BaseModel):
    """User's active discount info."""
    discount_percent: int
    source: Optional[str] = None
    expires_at: Optional[datetime] = None
    is_active: bool


class ClaimOfferRequest(BaseModel):
    """Request to claim an offer."""
    offer_id: int


class ClaimOfferResponse(BaseModel):
    """Response after claiming offer."""
    success: bool
    message: str
    discount_percent: Optional[int] = None
    expires_at: Optional[datetime] = None


class PromoGroupDiscounts(BaseModel):
    """User's promo group discounts."""
    group_name: Optional[str] = None
    server_discount_percent: int = 0
    traffic_discount_percent: int = 0
    device_discount_percent: int = 0
    period_discounts: Dict[str, int] = {}


# ============ Routes ============

@router.get("/offers", response_model=List[PromoOfferInfo])
async def get_promo_offers(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get list of available promo offers for the user."""
    now = datetime.utcnow()

    result = await db.execute(
        select(DiscountOffer)
        .where(
            and_(
                DiscountOffer.user_id == user.id,
                DiscountOffer.expires_at > now,
            )
        )
        .order_by(DiscountOffer.created_at.desc())
    )
    offers = result.scalars().all()

    return [
        PromoOfferInfo(
            id=offer.id,
            notification_type=offer.notification_type or "",
            discount_percent=offer.discount_percent,
            effect_type=offer.effect_type or "percent_discount",
            expires_at=offer.expires_at,
            is_active=offer.is_active and offer.claimed_at is None,
            is_claimed=offer.claimed_at is not None,
            claimed_at=offer.claimed_at,
            extra_data=offer.extra_data,
        )
        for offer in offers
    ]


@router.get("/active-discount", response_model=ActiveDiscountInfo)
async def get_active_discount(
    user: User = Depends(get_current_cabinet_user),
):
    """Get user's currently active discount."""
    discount_percent = user.promo_offer_discount_percent or 0
    expires_at = user.promo_offer_discount_expires_at
    source = user.promo_offer_discount_source

    now = datetime.utcnow()
    is_active = discount_percent > 0 and (expires_at is None or expires_at > now)

    return ActiveDiscountInfo(
        discount_percent=discount_percent if is_active else 0,
        source=source,
        expires_at=expires_at,
        is_active=is_active,
    )


@router.get("/group-discounts", response_model=PromoGroupDiscounts)
async def get_promo_group_discounts(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get user's promo group discounts."""
    await db.refresh(user, ["promo_groups"])

    promo_group = user.get_primary_promo_group() if hasattr(user, 'get_primary_promo_group') else None

    if not promo_group:
        return PromoGroupDiscounts()

    # Get period discounts
    period_discounts = {}
    raw_period_discounts = getattr(promo_group, "period_discounts", None)
    if isinstance(raw_period_discounts, dict):
        for key, value in raw_period_discounts.items():
            try:
                period_discounts[str(key)] = int(value)
            except (TypeError, ValueError):
                continue

    return PromoGroupDiscounts(
        group_name=promo_group.name,
        server_discount_percent=promo_group.server_discount_percent or 0,
        traffic_discount_percent=promo_group.traffic_discount_percent or 0,
        device_discount_percent=promo_group.device_discount_percent or 0,
        period_discounts=period_discounts,
    )


@router.post("/claim", response_model=ClaimOfferResponse)
async def claim_promo_offer(
    request: ClaimOfferRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Claim a promo offer."""
    offer = await get_offer_by_id(db, request.offer_id)

    if not offer or offer.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Offer not found",
        )

    now = datetime.utcnow()

    if offer.claimed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This offer has already been claimed",
        )

    if not offer.is_active or offer.expires_at <= now:
        offer.is_active = False
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This offer has expired",
        )

    effect_type = (offer.effect_type or "percent_discount").lower()

    # Handle test access offers
    if effect_type == "test_access":
        await db.refresh(user, ["subscription"])
        success, newly_added, expires_at, error_code = await promo_offer_service.grant_test_access(
            db,
            user,
            offer,
        )

        if not success:
            error_messages = {
                "subscription_missing": "Active subscription required for this offer",
                "squads_missing": "Could not determine servers for test access",
                "already_connected": "These servers are already connected",
                "remnawave_sync_failed": "Failed to connect servers. Please try again later",
            }
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_messages.get(error_code, "Failed to activate offer"),
            )

        await mark_offer_claimed(
            db,
            offer,
            details={
                "context": "test_access_claim",
                "new_squads": newly_added,
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
        )

        return ClaimOfferResponse(
            success=True,
            message=f"Test access activated until {expires_at.strftime('%Y-%m-%d %H:%M') if expires_at else 'unlimited'}",
            expires_at=expires_at,
        )

    # Handle discount offers
    discount_percent = int(offer.discount_percent or 0)
    if discount_percent <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid offer",
        )

    user.promo_offer_discount_percent = discount_percent
    user.promo_offer_discount_source = offer.notification_type
    user.updated_at = now

    # Calculate expiration
    extra_data = offer.extra_data or {}
    raw_duration = extra_data.get("active_discount_hours")
    template_id = extra_data.get("template_id")

    if raw_duration in (None, "") and template_id:
        try:
            template = await get_promo_offer_template_by_id(db, int(template_id))
        except (ValueError, TypeError):
            template = None
        if template and template.active_discount_hours:
            raw_duration = template.active_discount_hours

    try:
        duration_hours = int(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError):
        duration_hours = None

    if duration_hours and duration_hours > 0:
        discount_expires_at = now + timedelta(hours=duration_hours)
    else:
        discount_expires_at = None

    user.promo_offer_discount_expires_at = discount_expires_at

    await mark_offer_claimed(
        db,
        offer,
        details={
            "context": "discount_claim",
            "discount_percent": discount_percent,
            "discount_expires_at": discount_expires_at.isoformat() if discount_expires_at else None,
        },
    )
    await db.refresh(user)

    expires_text = ""
    if discount_expires_at:
        expires_text = f" Valid until {discount_expires_at.strftime('%Y-%m-%d %H:%M')}"

    return ClaimOfferResponse(
        success=True,
        message=f"Discount of {discount_percent}% activated!{expires_text}",
        discount_percent=discount_percent,
        expires_at=discount_expires_at,
    )


@router.delete("/active-discount")
async def clear_active_discount(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Clear user's active discount."""
    user.promo_offer_discount_percent = 0
    user.promo_offer_discount_source = None
    user.promo_offer_discount_expires_at = None
    user.updated_at = datetime.utcnow()

    await db.commit()

    return {"message": "Active discount cleared"}
