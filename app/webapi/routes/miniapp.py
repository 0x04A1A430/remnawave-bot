from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.user import get_user_by_telegram_id
from app.database.models import Subscription
from app.services.subscription_service import SubscriptionService
from app.utils.telegram_webapp import (
    TelegramWebAppAuthError,
    parse_webapp_init_data,
)

from ..dependencies import get_db_session
from ..schemas.miniapp import (
    MiniAppSubscriptionRequest,
    MiniAppSubscriptionResponse,
    MiniAppSubscriptionUser,
)


logger = logging.getLogger(__name__)

router = APIRouter()


def _format_gb(value: Optional[float]) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_gb_label(value: float) -> str:
    absolute = abs(value)
    if absolute >= 100:
        return f"{value:.0f} GB"
    if absolute >= 10:
        return f"{value:.1f} GB"
    return f"{value:.2f} GB"


def _format_limit_label(limit: Optional[int]) -> str:
    if not limit:
        return "Unlimited"
    return f"{limit} GB"


def _bytes_to_gb(bytes_value: Optional[int]) -> float:
    if not bytes_value:
        return 0.0
    return round(bytes_value / (1024 ** 3), 2)


def _status_label(status: str) -> str:
    mapping = {
        "active": "Active",
        "trial": "Trial",
        "expired": "Expired",
        "disabled": "Disabled",
    }
    return mapping.get(status, status.title())


def _resolve_display_name(user_data: Dict[str, Any]) -> str:
    username = user_data.get("username")
    if username:
        return username

    first = user_data.get("first_name")
    last = user_data.get("last_name")
    parts = [part for part in [first, last] if part]
    if parts:
        return " ".join(parts)

    telegram_id = user_data.get("telegram_id")
    return f"User {telegram_id}" if telegram_id else "User"


def _is_remnawave_configured() -> bool:
    params = settings.get_remnawave_auth_params()
    return bool(params.get("base_url") and params.get("api_key"))


async def _load_subscription_links(
    subscription: Subscription,
) -> Dict[str, Any]:
    if not subscription.remnawave_short_uuid or not _is_remnawave_configured():
        return {}

    try:
        service = SubscriptionService()
        info = await service.get_subscription_info(subscription.remnawave_short_uuid)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.warning("Failed to load subscription info from RemnaWave: %s", error)
        return {}

    if not info:
        return {}

    payload: Dict[str, Any] = {
        "links": list(info.links or []),
        "ss_conf_links": dict(info.ss_conf_links or {}),
        "subscription_url": info.subscription_url,
        "happ": info.happ,
        "happ_link": getattr(info, "happ_link", None),
        "happ_crypto_link": getattr(info, "happ_crypto_link", None),
    }

    return payload


@router.post("/subscription", response_model=MiniAppSubscriptionResponse)
async def get_subscription_details(
    payload: MiniAppSubscriptionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppSubscriptionResponse:
    try:
        webapp_data = parse_webapp_init_data(payload.init_data, settings.BOT_TOKEN)
    except TelegramWebAppAuthError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    telegram_user = webapp_data.get("user")
    if not isinstance(telegram_user, dict) or "id" not in telegram_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram user payload",
        )

    try:
        telegram_id = int(telegram_user["id"])
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram user identifier",
        ) from None

    user = await get_user_by_telegram_id(db, telegram_id)
    if not user or not user.subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )

    subscription = user.subscription
    traffic_used = _format_gb(subscription.traffic_used_gb)
    traffic_limit = subscription.traffic_limit_gb or 0
    lifetime_used = _bytes_to_gb(getattr(user, "lifetime_used_traffic_bytes", 0))

    status_actual = subscription.actual_status
    links_payload = await _load_subscription_links(subscription)

    subscription_url = links_payload.get("subscription_url") or subscription.subscription_url
    subscription_crypto_link = (
        links_payload.get("happ_crypto_link")
        or subscription.subscription_crypto_link
    )

    connected_squads: List[str] = list(subscription.connected_squads or [])
    links: List[str] = links_payload.get("links") or connected_squads
    ss_conf_links: Dict[str, str] = links_payload.get("ss_conf_links") or {}

    response_user = MiniAppSubscriptionUser(
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        display_name=_resolve_display_name(
            {
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "telegram_id": user.telegram_id,
            }
        ),
        language=user.language,
        status=user.status,
        subscription_status=subscription.status,
        subscription_actual_status=status_actual,
        status_label=_status_label(status_actual),
        expires_at=subscription.end_date,
        device_limit=subscription.device_limit,
        traffic_used_gb=round(traffic_used, 2),
        traffic_used_label=_format_gb_label(traffic_used),
        traffic_limit_gb=traffic_limit,
        traffic_limit_label=_format_limit_label(traffic_limit),
        lifetime_used_traffic_gb=lifetime_used,
        has_active_subscription=status_actual in {"active", "trial"},
    )

    return MiniAppSubscriptionResponse(
        subscription_id=subscription.id,
        remnawave_short_uuid=subscription.remnawave_short_uuid,
        user=response_user,
        subscription_url=subscription_url,
        subscription_crypto_link=subscription_crypto_link,
        links=links,
        ss_conf_links=ss_conf_links,
        connected_squads=connected_squads,
        happ=links_payload.get("happ"),
        happ_link=links_payload.get("happ_link"),
        happ_crypto_link=links_payload.get("happ_crypto_link"),
    )

