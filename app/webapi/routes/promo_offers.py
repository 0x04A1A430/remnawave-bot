from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.promo_offer import (
    count_promo_offers,
    create_promo_offer,
    get_promo_offer_by_id,
    list_promo_offers,
)
from app.database.models import PromoOffer, PromoOfferTarget
from app.services.promo_offer_service import promo_offer_service

from ..dependencies import get_db_session, require_api_token
from ..schemas.promo_offers import (
    PromoOfferCreateRequest,
    PromoOfferListResponse,
    PromoOfferResponse,
)


router = APIRouter()


def _serialize_offer(offer: PromoOffer) -> PromoOfferResponse:
    target_segments: list[PromoOfferTarget] = []
    for raw in offer.target_segments or []:
        try:
            target_segments.append(PromoOfferTarget(raw))
        except ValueError:
            continue

    return PromoOfferResponse(
        id=offer.id,
        title=offer.title,
        message_text=offer.message_text,
        button_text=offer.button_text,
        offer_type=offer.offer_type,
        target_segments=target_segments,
        starts_at=offer.starts_at,
        expires_at=offer.expires_at,
        discount_percent=offer.discount_percent,
        bonus_amount_kopeks=offer.bonus_amount_kopeks,
        discount_valid_hours=offer.discount_valid_hours,
        test_access_hours=offer.test_access_hours,
        test_squad_uuids=list(offer.test_squad_uuids or []),
        status=offer.status,
        total_count=offer.total_count,
        sent_count=offer.sent_count,
        failed_count=offer.failed_count,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
        started_at=offer.started_at,
        completed_at=offer.completed_at,
    )


@router.get("", response_model=PromoOfferListResponse)
async def list_promo_offers_endpoint(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PromoOfferListResponse:
    total = await count_promo_offers(db)
    offers = await list_promo_offers(db, limit=limit, offset=offset)

    return PromoOfferListResponse(
        items=[_serialize_offer(offer) for offer in offers],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{offer_id}", response_model=PromoOfferResponse)
async def get_promo_offer_endpoint(
    offer_id: int,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> PromoOfferResponse:
    offer = await get_promo_offer_by_id(db, offer_id)
    if not offer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Promo offer not found")
    return _serialize_offer(offer)


@router.post("", response_model=PromoOfferResponse, status_code=status.HTTP_201_CREATED)
async def create_promo_offer_endpoint(
    payload: PromoOfferCreateRequest,
    token: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> PromoOfferResponse:
    button_text = payload.button_text or "🎁 Активировать"
    offer = await create_promo_offer(
        db,
        title=payload.title,
        message_text=payload.message_text,
        button_text=button_text,
        offer_type=payload.offer_type.value,
        target_segments=[segment.value for segment in payload.target_segments],
        starts_at=payload.starts_at,
        expires_at=payload.expires_at,
        discount_percent=payload.discount_percent or 0,
        bonus_amount_kopeks=payload.bonus_amount_kopeks or 0,
        discount_valid_hours=payload.discount_valid_hours or 0,
        test_access_hours=payload.test_access_hours or 0,
        test_squad_uuids=payload.test_squad_uuids or [],
        created_by=getattr(token, "id", None),
    )

    await promo_offer_service.start_offer(offer.id)

    return _serialize_offer(offer)
