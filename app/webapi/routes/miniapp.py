from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.discount_offer import get_offer_by_id, list_discount_offers
from app.database.crud.promo_offer_template import get_promo_offer_template_by_id
from app.database.crud.server_squad import get_server_squad_by_uuid
from app.database.crud.promo_group import get_auto_assign_promo_groups
from app.database.crud.transaction import get_user_total_spent_kopeks
from app.database.crud.user import get_user_by_telegram_id
from app.database.models import PromoGroup, Subscription, Transaction, User
from app.services.promo_offer_service import promo_offer_service
from app.services.remnawave_service import (
    RemnaWaveConfigurationError,
    RemnaWaveService,
)
from app.services.subscription_service import SubscriptionService
from app.utils.subscription_utils import get_happ_cryptolink_redirect_link
from app.utils.telegram_webapp import (
    TelegramWebAppAuthError,
    parse_webapp_init_data,
)

from ..dependencies import get_db_session
from ..schemas.miniapp import (
    MiniAppConnectedServer,
    MiniAppDevice,
    MiniAppAutoPromoGroupLevel,
    MiniAppPromoOffer,
    MiniAppPromoGroup,
    MiniAppPromoOfferClaimRequest,
    MiniAppPromoOfferClaimResponse,
    MiniAppSubscriptionRequest,
    MiniAppSubscriptionResponse,
    MiniAppSubscriptionUser,
    MiniAppTransaction,
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


def _parse_datetime_string(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    try:
        cleaned = value.strip()
        if cleaned.endswith("Z"):
            cleaned = f"{cleaned[:-1]}+00:00"
        # Normalize duplicated timezone suffixes like +00:00+00:00
        if "+00:00+00:00" in cleaned:
            cleaned = cleaned.replace("+00:00+00:00", "+00:00")

        datetime.fromisoformat(cleaned)
        return cleaned
    except Exception:  # pragma: no cover - defensive
        return value


async def _resolve_connected_servers(
    db: AsyncSession,
    squad_uuids: List[str],
) -> List[MiniAppConnectedServer]:
    if not squad_uuids:
        return []

    resolved: Dict[str, str] = {}
    missing: List[str] = []

    for squad_uuid in squad_uuids:
        if squad_uuid in resolved:
            continue
        server = await get_server_squad_by_uuid(db, squad_uuid)
        if server and server.display_name:
            resolved[squad_uuid] = server.display_name
        else:
            missing.append(squad_uuid)

    if missing:
        try:
            service = RemnaWaveService()
            if service.is_configured:
                squads = await service.get_all_squads()
                for squad in squads:
                    uuid = squad.get("uuid")
                    name = squad.get("name")
                    if uuid in missing and name:
                        resolved[uuid] = name
        except RemnaWaveConfigurationError:
            logger.debug("RemnaWave is not configured; skipping server name enrichment")
        except Exception as error:  # pragma: no cover - defensive logging
            logger.warning("Failed to resolve server names from RemnaWave: %s", error)

    connected_servers: List[MiniAppConnectedServer] = []
    for squad_uuid in squad_uuids:
        name = resolved.get(squad_uuid, squad_uuid)
        connected_servers.append(MiniAppConnectedServer(uuid=squad_uuid, name=name))

    return connected_servers


async def _load_devices_info(user: User) -> Tuple[int, List[MiniAppDevice]]:
    remnawave_uuid = getattr(user, "remnawave_uuid", None)
    if not remnawave_uuid:
        return 0, []

    try:
        service = RemnaWaveService()
    except Exception as error:  # pragma: no cover - defensive logging
        logger.warning("Failed to initialise RemnaWave service: %s", error)
        return 0, []

    if not service.is_configured:
        return 0, []

    try:
        async with service.get_api_client() as api:
            response = await api.get_user_devices(remnawave_uuid)
    except RemnaWaveConfigurationError:
        logger.debug("RemnaWave configuration missing while loading devices")
        return 0, []
    except Exception as error:  # pragma: no cover - defensive logging
        logger.warning("Failed to load devices from RemnaWave: %s", error)
        return 0, []

    total_devices = int(response.get("total") or 0)
    devices_payload = response.get("devices") or []

    devices: List[MiniAppDevice] = []
    for device in devices_payload:
        platform = device.get("platform") or device.get("platformType")
        model = device.get("deviceModel") or device.get("model") or device.get("name")
        app_version = device.get("appVersion") or device.get("version")
        last_seen_raw = (
            device.get("updatedAt")
            or device.get("lastSeen")
            or device.get("lastActiveAt")
            or device.get("createdAt")
        )
        last_ip = device.get("ip") or device.get("ipAddress")

        devices.append(
            MiniAppDevice(
                platform=platform,
                device_model=model,
                app_version=app_version,
                last_seen=_parse_datetime_string(last_seen_raw),
                last_ip=last_ip,
            )
        )

    if total_devices == 0:
        total_devices = len(devices)

    return total_devices, devices


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


def _sanitize_offer_message(text: Optional[str]) -> Optional[str]:
    if not text:
        return None

    normalized = text.replace("\r\n", "\n")
    normalized = re.sub(r"<br\s*/?>", "\n", normalized, flags=re.IGNORECASE)
    stripped = re.sub(r"<[^>]+>", "", normalized)
    unescaped = html.unescape(stripped)
    cleaned = unescaped.strip()
    return cleaned or None


def _parse_init_payload(init_data: str) -> int:
    try:
        webapp_data = parse_webapp_init_data(init_data, settings.BOT_TOKEN)
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
        return int(telegram_user["id"])
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram user identifier",
        ) from error


async def _load_available_promo_offers(
    db: AsyncSession,
    user: User,
) -> List[MiniAppPromoOffer]:
    offers = await list_discount_offers(
        db,
        user_id=user.id,
        is_active=True,
        limit=20,
    )

    now = datetime.utcnow()
    templates_cache: Dict[int, Optional[Any]] = {}
    result: List[MiniAppPromoOffer] = []

    for offer in offers:
        if offer.claimed_at is not None:
            continue
        if offer.expires_at <= now:
            continue

        extra_data = offer.extra_data if isinstance(offer.extra_data, dict) else {}

        template_id: Optional[int] = None
        raw_template_id = extra_data.get("template_id")
        if raw_template_id is not None:
            try:
                template_id = int(raw_template_id)
            except (TypeError, ValueError):
                template_id = None

        template = None
        if template_id is not None:
            if template_id in templates_cache:
                template = templates_cache[template_id]
            else:
                template = await get_promo_offer_template_by_id(db, template_id)
                templates_cache[template_id] = template

        name = None
        button_text = None
        message_text = None

        if template is not None:
            name = template.name
            button_text = template.button_text
            message_text = template.message_text
        else:
            name = extra_data.get("title") or extra_data.get("name")
            button_text = extra_data.get("button_text") or extra_data.get("buttonText")
            message_text = (
                extra_data.get("message_text")
                or extra_data.get("messageText")
                or extra_data.get("text")
            )

        sanitized_message = _sanitize_offer_message(message_text)

        try:
            discount_percent = int(offer.discount_percent or 0)
        except (TypeError, ValueError):
            discount_percent = 0

        try:
            bonus_amount = int(offer.bonus_amount_kopeks or 0)
        except (TypeError, ValueError):
            bonus_amount = 0

        result.append(
            MiniAppPromoOffer(
                id=offer.id,
                template_id=template_id,
                name=name,
                message=sanitized_message,
                button_text=button_text,
                discount_percent=discount_percent,
                bonus_amount_kopeks=bonus_amount,
                expires_at=offer.expires_at,
                effect_type=offer.effect_type or "percent_discount",
                extra_data=extra_data,
            )
        )

    return result


def _serialize_transaction(transaction: Transaction) -> MiniAppTransaction:
    return MiniAppTransaction(
        id=transaction.id,
        type=transaction.type,
        amount_kopeks=transaction.amount_kopeks,
        amount_rubles=round(transaction.amount_kopeks / 100, 2),
        description=transaction.description,
        payment_method=transaction.payment_method,
        external_id=transaction.external_id,
        is_completed=transaction.is_completed,
        created_at=transaction.created_at,
        completed_at=transaction.completed_at,
    )


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
    telegram_id = _parse_init_payload(payload.init_data)

    user = await get_user_by_telegram_id(db, telegram_id)
    purchase_url = (settings.MINIAPP_PURCHASE_URL or "").strip()
    if not user or not user.subscription:
        detail: Union[str, Dict[str, str]] = "Subscription not found"
        if purchase_url:
            detail = {
                "message": "Subscription not found",
                "purchase_url": purchase_url,
            }
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
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

    happ_redirect_link = get_happ_cryptolink_redirect_link(subscription_crypto_link)

    connected_squads: List[str] = list(subscription.connected_squads or [])
    connected_servers = await _resolve_connected_servers(db, connected_squads)
    devices_count, devices = await _load_devices_info(user)
    links: List[str] = links_payload.get("links") or connected_squads
    ss_conf_links: Dict[str, str] = links_payload.get("ss_conf_links") or {}

    transactions_query = (
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.created_at.desc())
        .limit(10)
    )
    transactions_result = await db.execute(transactions_query)
    transactions = list(transactions_result.scalars().all())

    balance_currency = getattr(user, "balance_currency", None)
    if isinstance(balance_currency, str):
        balance_currency = balance_currency.upper()

    promo_group = getattr(user, "promo_group", None)
    total_spent_kopeks = await get_user_total_spent_kopeks(db, user.id)
    auto_assign_groups = await get_auto_assign_promo_groups(db)

    auto_promo_levels: List[MiniAppAutoPromoGroupLevel] = []
    for group in auto_assign_groups:
        threshold = group.auto_assign_total_spent_kopeks or 0
        if threshold <= 0:
            continue

        auto_promo_levels.append(
            MiniAppAutoPromoGroupLevel(
                id=group.id,
                name=group.name,
                threshold_kopeks=threshold,
                threshold_rubles=round(threshold / 100, 2),
                threshold_label=settings.format_price(threshold),
                is_reached=total_spent_kopeks >= threshold,
                is_current=bool(promo_group and promo_group.id == group.id),
                **_extract_promo_discounts(group),
            )
        )

    available_promo_offers = await _load_available_promo_offers(db, user)

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
        promo_offer_discount_percent=
            int(getattr(user, "promo_offer_discount_percent", 0) or 0),
        promo_offer_discount_source=getattr(user, "promo_offer_discount_source", None),
        promo_offer_discount_expires_at=getattr(
            user,
            "promo_offer_discount_expires_at",
            None,
        ),
    )

    return MiniAppSubscriptionResponse(
        subscription_id=subscription.id,
        remnawave_short_uuid=subscription.remnawave_short_uuid,
        user=response_user,
        subscription_url=subscription_url,
        subscription_crypto_link=subscription_crypto_link,
        subscription_purchase_url=purchase_url or None,
        links=links,
        ss_conf_links=ss_conf_links,
        connected_squads=connected_squads,
        connected_servers=connected_servers,
        connected_devices_count=devices_count,
        connected_devices=devices,
        happ=links_payload.get("happ"),
        happ_link=links_payload.get("happ_link"),
        happ_crypto_link=links_payload.get("happ_crypto_link"),
        happ_cryptolink_redirect_link=happ_redirect_link,
        balance_kopeks=user.balance_kopeks,
        balance_rubles=round(user.balance_rubles, 2),
        balance_currency=balance_currency,
        transactions=[_serialize_transaction(tx) for tx in transactions],
        promo_group=(
            MiniAppPromoGroup(
                id=promo_group.id,
                name=promo_group.name,
                **_extract_promo_discounts(promo_group),
            )
            if promo_group
            else None
        ),
        auto_assign_promo_groups=auto_promo_levels,
        promo_offers=available_promo_offers,
        total_spent_kopeks=total_spent_kopeks,
        total_spent_rubles=round(total_spent_kopeks / 100, 2),
        total_spent_label=settings.format_price(total_spent_kopeks),
        subscription_type="trial" if subscription.is_trial else "paid",
        autopay_enabled=bool(subscription.autopay_enabled),
        branding=settings.get_miniapp_branding(),
    )


@router.post(
    "/promo-offers/{offer_id}/claim",
    response_model=MiniAppPromoOfferClaimResponse,
)
async def claim_promo_offer(
    offer_id: int,
    payload: MiniAppPromoOfferClaimRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppPromoOfferClaimResponse:
    telegram_id = _parse_init_payload(payload.init_data)

    user = await get_user_by_telegram_id(db, telegram_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    offer = await get_offer_by_id(db, offer_id)
    if not offer or offer.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Promo offer not found")

    result = await promo_offer_service.claim_offer(db, user, offer)

    if not result.success:
        return MiniAppPromoOfferClaimResponse(
            success=False,
            effect_type=result.effect_type,
            error_code=result.error_code or "unknown",
        )

    return MiniAppPromoOfferClaimResponse(
        success=True,
        effect_type=result.effect_type,
        discount_percent=result.discount_percent or None,
        discount_expires_at=result.discount_expires_at,
        test_access_expires_at=result.test_access_expires_at,
        newly_added_squads=result.newly_added_squads,
    )


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_period_discounts(
    raw: Optional[Dict[Any, Any]]
) -> Dict[int, int]:
    if not isinstance(raw, dict):
        return {}

    normalized: Dict[int, int] = {}
    for key, value in raw.items():
        try:
            period = int(key)
            normalized[period] = int(value)
        except (TypeError, ValueError):
            continue

    return normalized


def _extract_promo_discounts(group: Optional[PromoGroup]) -> Dict[str, Any]:
    if not group:
        return {
            "server_discount_percent": 0,
            "traffic_discount_percent": 0,
            "device_discount_percent": 0,
            "period_discounts": {},
            "apply_discounts_to_addons": True,
        }

    return {
        "server_discount_percent": max(0, _safe_int(getattr(group, "server_discount_percent", 0))),
        "traffic_discount_percent": max(0, _safe_int(getattr(group, "traffic_discount_percent", 0))),
        "device_discount_percent": max(0, _safe_int(getattr(group, "device_discount_percent", 0))),
        "period_discounts": _normalize_period_discounts(getattr(group, "period_discounts", None)),
        "apply_discounts_to_addons": bool(
            getattr(group, "apply_discounts_to_addons", True)
        ),
    }


