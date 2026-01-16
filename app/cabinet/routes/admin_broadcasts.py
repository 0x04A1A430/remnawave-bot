"""Admin routes for broadcasts in cabinet."""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BroadcastHistory, Tariff, User, Subscription, SubscriptionStatus
from app.keyboards.admin import BROADCAST_BUTTONS, DEFAULT_BROADCAST_BUTTONS
from app.services.broadcast_service import (
    BroadcastConfig,
    BroadcastMediaConfig,
    broadcast_service,
)
from app.handlers.admin.messages import get_target_users_count

from ..dependencies import get_cabinet_db, get_current_admin_user
from ..schemas.broadcasts import (
    BroadcastButton,
    BroadcastButtonsResponse,
    BroadcastCreateRequest,
    BroadcastFilter,
    BroadcastFiltersResponse,
    BroadcastListResponse,
    BroadcastPreviewRequest,
    BroadcastPreviewResponse,
    BroadcastResponse,
    BroadcastTariffsResponse,
    TariffFilter,
    TariffForBroadcast,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/broadcasts", tags=["Cabinet Admin Broadcasts"])


# ============ Filter Labels ============

FILTER_LABELS = {
    "all": "Все пользователи",
    "active": "Активные подписки",
    "trial": "Триальные",
    "no": "Без подписки",
    "expiring": "Истекают (3 дня)",
    "expired": "Истекшие",
    "zero": "Нулевой трафик",
    "active_zero": "Активные с нулевым трафиком",
    "trial_zero": "Триальные с нулевым трафиком",
}

FILTER_GROUPS = {
    "all": "basic",
    "active": "subscription",
    "trial": "subscription",
    "no": "subscription",
    "expiring": "subscription",
    "expired": "subscription",
    "zero": "traffic",
    "active_zero": "traffic",
    "trial_zero": "traffic",
}

CUSTOM_FILTER_LABELS = {
    "custom_today": "Регистрация сегодня",
    "custom_week": "Регистрация за неделю",
    "custom_month": "Регистрация за месяц",
    "custom_active_today": "Активны сегодня",
    "custom_inactive_week": "Неактивны 7+ дней",
    "custom_inactive_month": "Неактивны 30+ дней",
    "custom_referrals": "Пришли по рефералу",
    "custom_direct": "Прямая регистрация",
}

CUSTOM_FILTER_GROUPS = {
    "custom_today": "registration",
    "custom_week": "registration",
    "custom_month": "registration",
    "custom_active_today": "activity",
    "custom_inactive_week": "activity",
    "custom_inactive_month": "activity",
    "custom_referrals": "source",
    "custom_direct": "source",
}


# ============ Helper Functions ============

def _serialize_broadcast(broadcast: BroadcastHistory) -> BroadcastResponse:
    """Serialize broadcast to response model."""
    progress = 0.0
    if broadcast.total_count > 0:
        progress = round((broadcast.sent_count + broadcast.failed_count) / broadcast.total_count * 100, 1)

    return BroadcastResponse(
        id=broadcast.id,
        target_type=broadcast.target_type,
        message_text=broadcast.message_text,
        has_media=broadcast.has_media,
        media_type=broadcast.media_type,
        media_file_id=broadcast.media_file_id,
        media_caption=broadcast.media_caption,
        total_count=broadcast.total_count,
        sent_count=broadcast.sent_count,
        failed_count=broadcast.failed_count,
        status=broadcast.status,
        admin_id=broadcast.admin_id,
        admin_name=broadcast.admin_name,
        created_at=broadcast.created_at,
        completed_at=broadcast.completed_at,
        progress_percent=progress,
    )


async def _get_tariff_user_counts(db: AsyncSession) -> dict:
    """Get count of active users per tariff."""
    result = await db.execute(
        select(
            Subscription.tariff_id,
            func.count(func.distinct(Subscription.user_id)).label("count")
        )
        .join(User, User.id == Subscription.user_id)
        .where(
            User.status == "active",
            Subscription.status == SubscriptionStatus.ACTIVE.value,
        )
        .group_by(Subscription.tariff_id)
    )
    return {row.tariff_id: row.count for row in result.all()}


def _validate_target(target: str, tariff_ids: set) -> bool:
    """Validate target value."""
    if target in FILTER_LABELS:
        return True
    if target in CUSTOM_FILTER_LABELS:
        return True
    if target.startswith("tariff_"):
        try:
            tariff_id = int(target.split("_")[1])
            return tariff_id in tariff_ids
        except (ValueError, IndexError):
            return False
    return False


def _validate_buttons(buttons: List[str]) -> bool:
    """Validate button keys."""
    for button in buttons:
        if button not in BROADCAST_BUTTONS:
            return False
    return True


# ============ Endpoints ============

@router.get("/filters", response_model=BroadcastFiltersResponse)
async def get_filters(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> BroadcastFiltersResponse:
    """Get all available filters with user counts."""
    # Basic filters
    filters = []
    for key, label in FILTER_LABELS.items():
        try:
            count = await get_target_users_count(db, key)
        except Exception as e:
            logger.warning(f"Failed to get count for filter {key}: {e}")
            count = 0
        filters.append(BroadcastFilter(
            key=key,
            label=label,
            count=count,
            group=FILTER_GROUPS.get(key),
        ))

    # Custom filters
    custom_filters = []
    for key, label in CUSTOM_FILTER_LABELS.items():
        try:
            count = await get_target_users_count(db, key)
        except Exception as e:
            logger.warning(f"Failed to get count for custom filter {key}: {e}")
            count = 0
        custom_filters.append(BroadcastFilter(
            key=key,
            label=label,
            count=count,
            group=CUSTOM_FILTER_GROUPS.get(key),
        ))

    # Tariff filters
    tariff_counts = await _get_tariff_user_counts(db)
    result = await db.execute(
        select(Tariff).where(Tariff.is_active == True).order_by(Tariff.name)
    )
    tariffs = result.scalars().all()

    tariff_filters = []
    for tariff in tariffs:
        tariff_filters.append(TariffFilter(
            key=f"tariff_{tariff.id}",
            label=tariff.name,
            tariff_id=tariff.id,
            count=tariff_counts.get(tariff.id, 0),
        ))

    return BroadcastFiltersResponse(
        filters=filters,
        tariff_filters=tariff_filters,
        custom_filters=custom_filters,
    )


@router.get("/tariffs", response_model=BroadcastTariffsResponse)
async def get_tariffs(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> BroadcastTariffsResponse:
    """Get tariffs for broadcast filtering."""
    tariff_counts = await _get_tariff_user_counts(db)
    result = await db.execute(
        select(Tariff).where(Tariff.is_active == True).order_by(Tariff.name)
    )
    tariffs = result.scalars().all()

    return BroadcastTariffsResponse(
        tariffs=[
            TariffForBroadcast(
                id=t.id,
                name=t.name,
                filter_key=f"tariff_{t.id}",
                active_users_count=tariff_counts.get(t.id, 0),
            )
            for t in tariffs
        ]
    )


@router.get("/buttons", response_model=BroadcastButtonsResponse)
async def get_buttons(
    admin: User = Depends(get_current_admin_user),
) -> BroadcastButtonsResponse:
    """Get available buttons for broadcasts."""
    default_buttons = set(DEFAULT_BROADCAST_BUTTONS)
    buttons = []
    for key, config in BROADCAST_BUTTONS.items():
        buttons.append(BroadcastButton(
            key=key,
            label=config.get("default_text", key),
            default=key in default_buttons,
        ))
    return BroadcastButtonsResponse(buttons=buttons)


@router.post("/preview", response_model=BroadcastPreviewResponse)
async def preview_broadcast(
    request: BroadcastPreviewRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> BroadcastPreviewResponse:
    """Preview broadcast recipients count."""
    # Get tariff IDs for validation
    result = await db.execute(select(Tariff.id))
    tariff_ids = {row[0] for row in result.all()}

    if not _validate_target(request.target, tariff_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid target: {request.target}",
        )

    try:
        count = await get_target_users_count(db, request.target)
    except Exception as e:
        logger.error(f"Failed to get count for target {request.target}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to count recipients",
        )

    return BroadcastPreviewResponse(target=request.target, count=count)


@router.post("", response_model=BroadcastResponse, status_code=status.HTTP_201_CREATED)
async def create_broadcast(
    request: BroadcastCreateRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> BroadcastResponse:
    """Create and start a broadcast."""
    # Validate target
    result = await db.execute(select(Tariff.id))
    tariff_ids = {row[0] for row in result.all()}

    if not _validate_target(request.target, tariff_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid target: {request.target}",
        )

    # Validate buttons
    if not _validate_buttons(request.selected_buttons):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid button key",
        )

    message_text = request.message_text.strip()
    if not message_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message text must not be empty",
        )

    media_payload = request.media

    # Create broadcast record
    broadcast = BroadcastHistory(
        target_type=request.target,
        message_text=message_text,
        has_media=media_payload is not None,
        media_type=media_payload.type if media_payload else None,
        media_file_id=media_payload.file_id if media_payload else None,
        media_caption=media_payload.caption if media_payload else None,
        total_count=0,
        sent_count=0,
        failed_count=0,
        status="queued",
        admin_id=admin.id,
        admin_name=admin.username or f"Admin #{admin.id}",
    )
    db.add(broadcast)
    await db.commit()
    await db.refresh(broadcast)

    # Prepare media config
    media_config = None
    if media_payload:
        media_config = BroadcastMediaConfig(
            type=media_payload.type,
            file_id=media_payload.file_id,
            caption=media_payload.caption or message_text,
        )

    # Create broadcast config
    config = BroadcastConfig(
        target=request.target,
        message_text=message_text,
        selected_buttons=request.selected_buttons,
        media=media_config,
        initiator_name=admin.username or f"Admin #{admin.id}",
    )

    # Start broadcast
    await broadcast_service.start_broadcast(broadcast.id, config)
    await db.refresh(broadcast)

    logger.info(f"Admin {admin.id} created broadcast {broadcast.id} for target '{request.target}'")

    return _serialize_broadcast(broadcast)


@router.get("", response_model=BroadcastListResponse)
async def list_broadcasts(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> BroadcastListResponse:
    """Get list of broadcasts with pagination."""
    total = await db.scalar(select(func.count(BroadcastHistory.id))) or 0

    result = await db.execute(
        select(BroadcastHistory)
        .order_by(BroadcastHistory.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    broadcasts = result.scalars().all()

    return BroadcastListResponse(
        items=[_serialize_broadcast(b) for b in broadcasts],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/{broadcast_id}", response_model=BroadcastResponse)
async def get_broadcast(
    broadcast_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> BroadcastResponse:
    """Get broadcast details."""
    broadcast = await db.get(BroadcastHistory, broadcast_id)
    if not broadcast:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Broadcast not found",
        )
    return _serialize_broadcast(broadcast)


@router.post("/{broadcast_id}/stop", response_model=BroadcastResponse)
async def stop_broadcast(
    broadcast_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> BroadcastResponse:
    """Stop a running broadcast."""
    broadcast = await db.get(BroadcastHistory, broadcast_id)
    if not broadcast:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Broadcast not found",
        )

    if broadcast.status not in {"queued", "in_progress", "cancelling"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Broadcast is not running",
        )

    is_running = await broadcast_service.request_stop(broadcast_id)

    if is_running:
        broadcast.status = "cancelling"
    else:
        broadcast.status = "cancelled"
        broadcast.completed_at = datetime.utcnow()

    await db.commit()
    await db.refresh(broadcast)

    logger.info(f"Admin {admin.id} stopped broadcast {broadcast_id}")

    return _serialize_broadcast(broadcast)
