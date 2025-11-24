from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.database.crud.referral import get_user_referral_stats
from app.database.crud.user import (
    get_user_by_id,
    get_user_by_telegram_id,
    update_user,
)
from app.database.models import User
from app.utils.user_utils import (
    get_detailed_referral_list,
    get_effective_referral_commission_percent,
)

from ..dependencies import get_db_session, require_api_token
from ..schemas.partners import (
    PartnerReferralItem,
    PartnerReferralList,
    PartnerReferralCommissionUpdate,
    PartnerReferrerDetail,
    PartnerReferrerItem,
    PartnerReferrerListResponse,
)

router = APIRouter()


def _apply_search_filter(query, search: str):
    search_lower = f"%{search.lower()}%"
    conditions = [
        func.lower(User.username).like(search_lower),
        func.lower(User.first_name).like(search_lower),
        func.lower(User.last_name).like(search_lower),
        func.lower(User.referral_code).like(search_lower),
    ]

    if search.isdigit():
        conditions.append(User.telegram_id == int(search))
        conditions.append(User.id == int(search))

    return query.where(or_(*conditions))


def _serialize_referrer(user: User, stats: dict) -> PartnerReferrerItem:
    total_earned_kopeks = int(stats.get("total_earned_kopeks") or 0)
    month_earned_kopeks = int(stats.get("month_earned_kopeks") or 0)

    return PartnerReferrerItem(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        referral_code=user.referral_code,
        referral_commission_percent=getattr(user, "referral_commission_percent", None),
        effective_referral_commission_percent=get_effective_referral_commission_percent(user),
        invited_count=int(stats.get("invited_count") or 0),
        active_referrals=int(stats.get("active_referrals") or 0),
        total_earned_kopeks=total_earned_kopeks,
        total_earned_rubles=round(total_earned_kopeks / 100, 2),
        month_earned_kopeks=month_earned_kopeks,
        month_earned_rubles=round(month_earned_kopeks / 100, 2),
        created_at=user.created_at,
        last_activity=user.last_activity,
    )


def _serialize_referral_item(referral: dict) -> PartnerReferralItem:
    balance_kopeks = int(referral.get("balance_kopeks") or 0)
    total_earned_kopeks = int(referral.get("total_earned_kopeks") or 0)

    return PartnerReferralItem(
        id=int(referral.get("id")),
        telegram_id=int(referral.get("telegram_id")),
        full_name=str(referral.get("full_name")),
        username=referral.get("username"),
        created_at=referral.get("created_at"),
        last_activity=referral.get("last_activity"),
        has_made_first_topup=bool(referral.get("has_made_first_topup", False)),
        balance_kopeks=balance_kopeks,
        balance_rubles=round(balance_kopeks / 100, 2),
        total_earned_kopeks=total_earned_kopeks,
        total_earned_rubles=round(total_earned_kopeks / 100, 2),
        topups_count=int(referral.get("topups_count") or 0),
        days_since_registration=int(referral.get("days_since_registration") or 0),
        days_since_activity=referral.get("days_since_activity"),
        status=str(referral.get("status") or "inactive"),
    )


@router.get("/referrers", response_model=PartnerReferrerListResponse)
async def list_referrers(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(default=None),
) -> PartnerReferrerListResponse:
    referral_alias = aliased(User)
    has_referrals = (
        select(referral_alias.id)
        .where(referral_alias.referred_by_id == User.id)
        .exists()
    )

    base_query = select(User).options(selectinload(User.referrer)).where(
        or_(User.referral_code.isnot(None), has_referrals)
    )

    if search:
        base_query = _apply_search_filter(base_query, search)

    total_query = base_query.with_only_columns(func.count()).order_by(None)
    total = await db.scalar(total_query) or 0

    result = await db.execute(
        base_query.order_by(User.created_at.desc()).offset(offset).limit(limit)
    )
    referrers = result.scalars().unique().all()

    items: list[PartnerReferrerItem] = []
    for referrer in referrers:
        stats = await get_user_referral_stats(db, referrer.id)
        items.append(_serialize_referrer(referrer, stats))

    return PartnerReferrerListResponse(
        items=items,
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/referrers/{user_id}", response_model=PartnerReferrerDetail)
async def get_referrer_detail(
    user_id: int,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PartnerReferrerDetail:
    user = await get_user_by_telegram_id(db, user_id)
    if not user:
        user = await get_user_by_id(db, user_id)

    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    stats = await get_user_referral_stats(db, user.id)
    referrer_item = _serialize_referrer(user, stats)

    referrals_data = await get_detailed_referral_list(db, user.id, limit=limit, offset=offset)
    referral_items = [
        _serialize_referral_item(referral) for referral in referrals_data.get("referrals", [])
    ]

    referrals_list = PartnerReferralList(
        items=referral_items,
        total=int(referrals_data.get("total_count") or 0),
        limit=limit,
        offset=offset,
        has_next=bool(referrals_data.get("has_next")),
        has_prev=bool(referrals_data.get("has_prev")),
        current_page=int(referrals_data.get("current_page") or 1),
        total_pages=int(referrals_data.get("total_pages") or 1),
    )

    return PartnerReferrerDetail(referrer=referrer_item, referrals=referrals_list)


@router.patch("/referrers/{user_id}/commission", response_model=PartnerReferrerItem)
async def update_referrer_commission(
    user_id: int,
    payload: PartnerReferralCommissionUpdate,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> PartnerReferrerItem:
    user = await get_user_by_telegram_id(db, user_id)
    if not user:
        user = await get_user_by_id(db, user_id)

    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    await update_user(
        db,
        user,
        referral_commission_percent=payload.referral_commission_percent,
    )

    stats = await get_user_referral_stats(db, user.id)
    return _serialize_referrer(user, stats)
