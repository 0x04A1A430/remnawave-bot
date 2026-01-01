"""Subscription management routes for cabinet."""

import base64
import json
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User, Subscription, ServerSquad
from app.database.crud.subscription import create_trial_subscription, get_subscription_by_user_id
from sqlalchemy import select
from app.config import settings, PERIOD_PRICES
from app.services.subscription_service import SubscriptionService
from app.services.subscription_purchase_service import (
    MiniAppSubscriptionPurchaseService,
    PurchaseValidationError,
    PurchaseBalanceError,
)

from ..dependencies import get_cabinet_db, get_current_cabinet_user
from ..schemas.subscription import (
    SubscriptionResponse,
    ServerInfo,
    RenewalOptionResponse,
    RenewalRequest,
    TrafficPackageResponse,
    TrafficPurchaseRequest,
    DevicePurchaseRequest,
    AutopayUpdateRequest,
    TrialInfoResponse,
    PurchaseSelectionRequest,
    PurchasePreviewRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscription", tags=["Cabinet Subscription"])


def _subscription_to_response(
    subscription: Subscription,
    servers: Optional[List[ServerInfo]] = None
) -> SubscriptionResponse:
    """Convert Subscription model to response."""
    now = datetime.utcnow()

    # Use actual_status property for correct status (same as bot uses)
    actual_status = subscription.actual_status
    is_expired = actual_status == "expired"
    is_active = actual_status in ("active", "trial")

    # Calculate time remaining
    days_left = 0
    hours_left = 0
    minutes_left = 0
    time_left_display = ""

    if subscription.end_date and not is_expired:
        time_delta = subscription.end_date - now
        total_seconds = max(0, int(time_delta.total_seconds()))

        days_left = total_seconds // 86400  # 86400 seconds in a day
        remaining_seconds = total_seconds % 86400
        hours_left = remaining_seconds // 3600
        minutes_left = (remaining_seconds % 3600) // 60

        # Create human-readable display
        if days_left > 0:
            time_left_display = f"{days_left}d {hours_left}h"
        elif hours_left > 0:
            time_left_display = f"{hours_left}h {minutes_left}m"
        elif minutes_left > 0:
            time_left_display = f"{minutes_left}m"
        else:
            time_left_display = "0m"
    else:
        time_left_display = "0m"

    traffic_limit_gb = subscription.traffic_limit_gb or 0
    traffic_used_gb = subscription.traffic_used_gb or 0.0

    if traffic_limit_gb > 0:
        traffic_used_percent = min(100, (traffic_used_gb / traffic_limit_gb) * 100)
    else:
        traffic_used_percent = 0

    return SubscriptionResponse(
        id=subscription.id,
        status=actual_status,  # Use actual_status instead of raw status
        is_trial=subscription.is_trial or actual_status == "trial",
        start_date=subscription.start_date,
        end_date=subscription.end_date,
        days_left=days_left,
        hours_left=hours_left,
        minutes_left=minutes_left,
        time_left_display=time_left_display,
        traffic_limit_gb=traffic_limit_gb,
        traffic_used_gb=round(traffic_used_gb, 2),
        traffic_used_percent=round(traffic_used_percent, 1),
        device_limit=subscription.device_limit or 1,
        connected_squads=subscription.connected_squads or [],
        servers=servers or [],
        autopay_enabled=subscription.autopay_enabled or False,
        autopay_days_before=subscription.autopay_days_before or 3,
        subscription_url=subscription.subscription_url,
        is_active=is_active,
        is_expired=is_expired,
    )


@router.get("", response_model=SubscriptionResponse)
async def get_subscription(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get current user's subscription details."""
    # Reload user from current session to get fresh data
    # (user object is from different session in get_current_cabinet_user)
    from app.database.crud.user import get_user_by_id
    fresh_user = await get_user_by_id(db, user.id)

    if not fresh_user or not fresh_user.subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found",
        )

    # Fetch server names for connected squads
    servers: List[ServerInfo] = []
    connected_squads = fresh_user.subscription.connected_squads or []
    if connected_squads:
        result = await db.execute(
            select(ServerSquad).where(ServerSquad.squad_uuid.in_(connected_squads))
        )
        server_squads = result.scalars().all()
        servers = [
            ServerInfo(
                uuid=sq.squad_uuid,
                name=sq.display_name,
                country_code=sq.country_code
            )
            for sq in server_squads
        ]

    return _subscription_to_response(fresh_user.subscription, servers)


@router.get("/renewal-options", response_model=List[RenewalOptionResponse])
async def get_renewal_options(
    user: User = Depends(get_current_cabinet_user),
):
    """Get available subscription renewal options with prices."""
    periods = settings.get_available_renewal_periods()
    options = []

    for period in periods:
        price_kopeks = PERIOD_PRICES.get(period, 0)
        if price_kopeks <= 0:
            continue

        # Apply user's discount if any
        discount_percent = 0
        if hasattr(user, "get_promo_discount"):
            discount_percent = user.get_promo_discount("period", period)

        if discount_percent > 0:
            original_price = price_kopeks
            price_kopeks = int(price_kopeks * (100 - discount_percent) / 100)
        else:
            original_price = None

        options.append(RenewalOptionResponse(
            period_days=period,
            price_kopeks=price_kopeks,
            price_rubles=price_kopeks / 100,
            discount_percent=discount_percent,
            original_price_kopeks=original_price,
        ))

    return options


@router.post("/renew")
async def renew_subscription(
    request: RenewalRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Renew subscription (pay from balance)."""
    await db.refresh(user, ["subscription"])

    if not user.subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found",
        )

    # Get price for requested period
    price_kopeks = PERIOD_PRICES.get(request.period_days, 0)
    if price_kopeks <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid renewal period",
        )

    # Apply discount
    discount_percent = 0
    if hasattr(user, "get_promo_discount"):
        discount_percent = user.get_promo_discount("period", request.period_days)

    if discount_percent > 0:
        price_kopeks = int(price_kopeks * (100 - discount_percent) / 100)

    # Check balance
    if user.balance_kopeks < price_kopeks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient balance. Need {price_kopeks / 100:.2f} RUB, have {user.balance_kopeks / 100:.2f} RUB",
        )

    # Deduct balance and extend subscription
    user.balance_kopeks -= price_kopeks

    # Extend from end_date or now if expired
    now = datetime.utcnow()
    if user.subscription.end_date and user.subscription.end_date > now:
        from datetime import timedelta
        user.subscription.end_date = user.subscription.end_date + timedelta(days=request.period_days)
    else:
        from datetime import timedelta
        user.subscription.end_date = now + timedelta(days=request.period_days)
        user.subscription.start_date = now

    user.subscription.status = "active"
    user.subscription.is_trial = False

    await db.commit()

    return {
        "message": "Subscription renewed successfully",
        "new_end_date": user.subscription.end_date.isoformat(),
        "amount_paid_kopeks": price_kopeks,
    }


@router.get("/traffic-packages", response_model=List[TrafficPackageResponse])
async def get_traffic_packages():
    """Get available traffic packages."""
    packages = settings.get_traffic_packages()
    result = []

    for pkg in packages:
        if not pkg.get("enabled", True):
            continue

        result.append(TrafficPackageResponse(
            gb=pkg["gb"],
            price_kopeks=pkg["price"],
            price_rubles=pkg["price"] / 100,
            is_unlimited=pkg["gb"] == 0,
        ))

    return result


@router.post("/traffic")
async def purchase_traffic(
    request: TrafficPurchaseRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Purchase additional traffic."""
    await db.refresh(user, ["subscription"])

    if not user.subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found",
        )

    # Find matching package
    packages = settings.get_traffic_packages()
    matching_pkg = next(
        (pkg for pkg in packages if pkg["gb"] == request.gb and pkg.get("enabled", True)),
        None
    )

    if not matching_pkg:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid traffic package",
        )

    price_kopeks = matching_pkg["price"]

    # Check balance
    if user.balance_kopeks < price_kopeks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient balance",
        )

    # Deduct balance and add traffic
    user.balance_kopeks -= price_kopeks

    if request.gb == 0:
        # Unlimited traffic
        user.subscription.traffic_limit = 0  # 0 means unlimited
    else:
        # Add GB to current limit
        current_limit = user.subscription.traffic_limit or 0
        additional_bytes = request.gb * (1024 ** 3)
        user.subscription.traffic_limit = current_limit + additional_bytes

    await db.commit()

    return {
        "message": "Traffic purchased successfully",
        "gb_added": request.gb,
        "amount_paid_kopeks": price_kopeks,
    }


@router.post("/devices")
async def purchase_devices(
    request: DevicePurchaseRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Purchase additional device slots."""
    await db.refresh(user, ["subscription"])

    if not user.subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found",
        )

    price_per_device = settings.PRICE_PER_DEVICE
    total_price = price_per_device * request.devices

    # Check balance
    if user.balance_kopeks < total_price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient balance",
        )

    # Check max devices limit
    current_devices = user.subscription.device_limit or 1
    new_devices = current_devices + request.devices
    max_devices = settings.MAX_DEVICES_LIMIT

    if new_devices > max_devices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum device limit is {max_devices}",
        )

    # Deduct balance and add devices
    user.balance_kopeks -= total_price
    user.subscription.device_limit = new_devices

    await db.commit()

    return {
        "message": "Devices added successfully",
        "devices_added": request.devices,
        "new_device_limit": new_devices,
        "amount_paid_kopeks": total_price,
    }


@router.patch("/autopay")
async def update_autopay(
    request: AutopayUpdateRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update autopay settings."""
    await db.refresh(user, ["subscription"])

    if not user.subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found",
        )

    user.subscription.autopay_enabled = request.enabled

    if request.days_before is not None:
        user.subscription.autopay_days_before = request.days_before

    await db.commit()

    return {
        "message": "Autopay settings updated",
        "autopay_enabled": user.subscription.autopay_enabled,
        "autopay_days_before": user.subscription.autopay_days_before,
    }


@router.get("/trial", response_model=TrialInfoResponse)
async def get_trial_info(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get trial subscription info and availability."""
    await db.refresh(user, ["subscription"])

    duration_days = settings.TRIAL_DURATION_DAYS
    traffic_limit_gb = settings.TRIAL_TRAFFIC_LIMIT_GB
    device_limit = settings.TRIAL_DEVICE_LIMIT
    requires_payment = bool(settings.TRIAL_PAYMENT_ENABLED)
    price_kopeks = settings.TRIAL_ACTIVATION_PRICE if requires_payment else 0

    # Check if user already has an active subscription
    if user.subscription:
        now = datetime.utcnow()
        is_active = (
            user.subscription.status == "active"
            and user.subscription.end_date
            and user.subscription.end_date > now
        )
        if is_active:
            return TrialInfoResponse(
                is_available=False,
                duration_days=duration_days,
                traffic_limit_gb=traffic_limit_gb,
                device_limit=device_limit,
                requires_payment=requires_payment,
                price_kopeks=price_kopeks,
                price_rubles=price_kopeks / 100,
                reason_unavailable="You already have an active subscription",
            )

        # Check if user already used trial
        if user.subscription.is_trial or user.has_had_paid_subscription:
            return TrialInfoResponse(
                is_available=False,
                duration_days=duration_days,
                traffic_limit_gb=traffic_limit_gb,
                device_limit=device_limit,
                requires_payment=requires_payment,
                price_kopeks=price_kopeks,
                price_rubles=price_kopeks / 100,
                reason_unavailable="Trial already used",
            )

    return TrialInfoResponse(
        is_available=True,
        duration_days=duration_days,
        traffic_limit_gb=traffic_limit_gb,
        device_limit=device_limit,
        requires_payment=requires_payment,
        price_kopeks=price_kopeks,
        price_rubles=price_kopeks / 100,
    )


@router.post("/trial", response_model=SubscriptionResponse)
async def activate_trial(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Activate trial subscription."""
    await db.refresh(user, ["subscription"])

    # Check if user already has an active subscription
    if user.subscription:
        now = datetime.utcnow()
        is_active = (
            user.subscription.status == "active"
            and user.subscription.end_date
            and user.subscription.end_date > now
        )
        if is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You already have an active subscription",
            )

        # Check if user already used trial
        if user.subscription.is_trial or user.has_had_paid_subscription:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Trial already used",
            )

    # Check if trial requires payment
    requires_payment = bool(settings.TRIAL_PAYMENT_ENABLED)
    if requires_payment:
        price_kopeks = settings.TRIAL_ACTIVATION_PRICE
        if user.balance_kopeks < price_kopeks:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient balance. Need {price_kopeks / 100:.2f} RUB",
            )
        user.balance_kopeks -= price_kopeks
        logger.info(f"User {user.id} paid {price_kopeks} kopeks for trial activation")

    # Create trial subscription
    subscription = await create_trial_subscription(
        db=db,
        user_id=user.id,
        duration_days=settings.TRIAL_DURATION_DAYS,
        traffic_limit_gb=settings.TRIAL_TRAFFIC_LIMIT_GB,
        device_limit=settings.TRIAL_DEVICE_LIMIT,
    )

    logger.info(f"Trial subscription activated for user {user.id}")

    # Create RemnaWave user
    try:
        subscription_service = SubscriptionService()
        if subscription_service.is_configured:
            await subscription_service.create_remnawave_user(db, subscription)
            await db.refresh(subscription)
    except Exception as e:
        logger.error(f"Failed to create RemnaWave user for trial: {e}")

    return _subscription_to_response(subscription)


# ============ Full Purchase Flow (like MiniApp) ============

purchase_service = MiniAppSubscriptionPurchaseService()


@router.get("/purchase-options")
async def get_purchase_options(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> Dict[str, Any]:
    """Get all subscription purchase options (periods, servers, traffic, devices)."""
    try:
        context = await purchase_service.build_options(db, user)
        return context.payload
    except PurchaseValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to build purchase options for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load purchase options",
        )


@router.post("/purchase-preview")
async def preview_purchase(
    request: PurchasePreviewRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> Dict[str, Any]:
    """Calculate and preview the total price for selected options."""
    try:
        context = await purchase_service.build_options(db, user)

        # Convert request to dict for parsing
        selection_dict = {
            "period_id": request.selection.period_id,
            "period_days": request.selection.period_days,
            "traffic_value": request.selection.traffic_value,
            "servers": request.selection.servers,
            "devices": request.selection.devices,
        }

        selection = purchase_service.parse_selection(context, selection_dict)
        pricing = await purchase_service.calculate_pricing(db, context, selection)
        preview = purchase_service.build_preview_payload(context, pricing)

        return preview

    except PurchaseValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to calculate purchase preview for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to calculate price",
        )


@router.post("/purchase")
async def submit_purchase(
    request: PurchasePreviewRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> Dict[str, Any]:
    """Submit subscription purchase (deduct from balance)."""
    try:
        context = await purchase_service.build_options(db, user)

        # Convert request to dict for parsing
        selection_dict = {
            "period_id": request.selection.period_id,
            "period_days": request.selection.period_days,
            "traffic_value": request.selection.traffic_value,
            "servers": request.selection.servers,
            "devices": request.selection.devices,
        }

        selection = purchase_service.parse_selection(context, selection_dict)
        pricing = await purchase_service.calculate_pricing(db, context, selection)
        result = await purchase_service.submit_purchase(db, context, pricing)

        subscription = result["subscription"]

        return {
            "success": True,
            "message": result["message"],
            "subscription": _subscription_to_response(subscription),
            "was_trial_conversion": result.get("was_trial_conversion", False),
        }

    except PurchaseValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except PurchaseBalanceError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to submit purchase for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process purchase",
        )


# ============ App Config for Connection ============

def _load_app_config() -> Dict[str, Any]:
    """Load app-config.json file."""
    try:
        config_path = settings.get_app_config_path()
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.error(f"Failed to load app-config.json: {e}")
    return {}


def _create_deep_link(app: Dict[str, Any], subscription_url: str) -> Optional[str]:
    """Create deep link for app with subscription URL."""
    if not subscription_url or not isinstance(app, dict):
        return None

    scheme = str(app.get("urlScheme", "")).strip()
    if not scheme:
        return None

    payload = subscription_url

    if app.get("isNeedBase64Encoding"):
        try:
            payload = base64.b64encode(subscription_url.encode("utf-8")).decode("utf-8")
        except Exception as e:
            logger.warning(f"Failed to encode subscription URL to base64: {e}")
            payload = subscription_url

    return f"{scheme}{payload}"


# ============ Countries Management ============

@router.get("/countries")
async def get_available_countries(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> Dict[str, Any]:
    """Get available countries/servers for the user."""
    from app.database.crud.server_squad import get_available_server_squads

    await db.refresh(user, ["subscription"])

    promo_group_id = user.promo_group_id
    available_servers = await get_available_server_squads(db, promo_group_id=promo_group_id)

    connected_squads = []
    if user.subscription:
        connected_squads = user.subscription.connected_squads or []

    countries = []
    for server in available_servers:
        countries.append({
            "uuid": server.squad_uuid,
            "name": server.display_name,
            "country_code": server.country_code,
            "price_kopeks": server.price_kopeks,
            "price_rubles": server.price_kopeks / 100,
            "is_available": server.is_available and not server.is_full,
            "is_connected": server.squad_uuid in connected_squads,
            "is_trial_eligible": server.is_trial_eligible,
        })

    return {
        "countries": countries,
        "connected_count": len(connected_squads),
        "has_subscription": user.subscription is not None,
    }


@router.post("/countries")
async def update_countries(
    request: Dict[str, Any],
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> Dict[str, Any]:
    """Update subscription countries/servers."""
    from app.database.crud.server_squad import get_available_server_squads, get_server_ids_by_uuids, add_user_to_servers
    from app.database.crud.subscription import add_subscription_servers
    from app.database.crud.transaction import create_transaction
    from app.database.crud.user import subtract_user_balance
    from app.database.models import TransactionType
    from app.utils.pricing_utils import calculate_prorated_price, apply_percentage_discount

    await db.refresh(user, ["subscription"])

    if not user.subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found",
        )

    if user.subscription.is_trial:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Country management is not available for trial subscriptions",
        )

    selected_countries = request.get("countries", [])
    if not selected_countries:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one country must be selected",
        )

    current_countries = user.subscription.connected_squads or []
    promo_group_id = user.promo_group_id

    available_servers = await get_available_server_squads(db, promo_group_id=promo_group_id)
    allowed_country_ids = {server.squad_uuid for server in available_servers}

    # Validate selected countries
    for country_uuid in selected_countries:
        if country_uuid not in allowed_country_ids and country_uuid not in current_countries:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Country {country_uuid} is not available",
            )

    added = [c for c in selected_countries if c not in current_countries]
    removed = [c for c in current_countries if c not in selected_countries]

    if not added and not removed:
        return {
            "message": "No changes detected",
            "connected_squads": current_countries,
        }

    # Calculate cost for added servers
    total_cost = 0
    added_names = []
    removed_names = []

    servers_discount_percent = 0
    promo_group = user.get_primary_promo_group() if hasattr(user, 'get_primary_promo_group') else None
    if promo_group:
        servers_discount_percent = promo_group.get_discount_percent("servers", None)

    added_server_prices = []

    for server in available_servers:
        if server.squad_uuid in added:
            server_price_per_month = server.price_kopeks
            if servers_discount_percent > 0:
                discounted_per_month, _ = apply_percentage_discount(
                    server_price_per_month,
                    servers_discount_percent,
                )
            else:
                discounted_per_month = server_price_per_month

            charged_price, charged_months = calculate_prorated_price(
                discounted_per_month,
                user.subscription.end_date,
            )

            total_cost += charged_price
            added_names.append(server.display_name)
            added_server_prices.append(charged_price)

        if server.squad_uuid in removed:
            removed_names.append(server.display_name)

    # Check balance
    if total_cost > 0 and user.balance_kopeks < total_cost:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient balance. Need {total_cost / 100:.2f} RUB, have {user.balance_kopeks / 100:.2f} RUB",
        )

    # Deduct balance and update subscription
    if added and total_cost > 0:
        success = await subtract_user_balance(
            db, user, total_cost,
            f"Adding countries: {', '.join(added_names)}"
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to charge balance",
            )

        await create_transaction(
            db=db,
            user_id=user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=total_cost,
            description=f"Adding countries to subscription: {', '.join(added_names)}"
        )

    # Add servers to subscription
    if added:
        added_server_ids = await get_server_ids_by_uuids(db, added)
        if added_server_ids:
            await add_subscription_servers(db, user.subscription, added_server_ids, added_server_prices)
            await add_user_to_servers(db, added_server_ids)

    # Update connected squads
    user.subscription.connected_squads = selected_countries
    user.subscription.updated_at = datetime.utcnow()
    await db.commit()

    # Sync with RemnaWave
    try:
        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, user.subscription)
    except Exception as e:
        logger.error(f"Failed to sync countries with RemnaWave: {e}")

    await db.refresh(user.subscription)

    return {
        "message": "Countries updated successfully",
        "added": added_names,
        "removed": removed_names,
        "amount_paid_kopeks": total_cost,
        "connected_squads": user.subscription.connected_squads,
    }


# ============ Connection Link ============

@router.get("/connection-link")
async def get_connection_link(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> Dict[str, Any]:
    """Get subscription connection link and instructions."""
    from app.utils.subscription_utils import (
        get_display_subscription_link,
        get_happ_cryptolink_redirect_link,
        convert_subscription_link_to_happ_scheme,
    )

    await db.refresh(user, ["subscription"])

    if not user.subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found",
        )

    subscription_url = user.subscription.subscription_url
    if not subscription_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription link not yet generated",
        )

    display_link = get_display_subscription_link(user.subscription)
    happ_redirect = get_happ_cryptolink_redirect_link(subscription_url) if settings.is_happ_cryptolink_mode() else None
    happ_scheme_link = convert_subscription_link_to_happ_scheme(subscription_url) if settings.is_happ_cryptolink_mode() else None

    connect_mode = settings.CONNECT_BUTTON_MODE
    hide_subscription_link = settings.should_hide_subscription_link()

    return {
        "subscription_url": subscription_url if not hide_subscription_link else None,
        "display_link": display_link if not hide_subscription_link else None,
        "happ_redirect_link": happ_redirect,
        "happ_scheme_link": happ_scheme_link,
        "connect_mode": connect_mode,
        "hide_link": hide_subscription_link,
        "instructions": {
            "steps": [
                "Copy the subscription link",
                "Open your VPN application",
                "Find 'Add subscription' or 'Import' option",
                "Paste the copied link",
            ]
        }
    }


# ============ hApp Downloads ============

@router.get("/happ-downloads")
async def get_happ_downloads(
    user: User = Depends(get_current_cabinet_user),
) -> Dict[str, Any]:
    """Get hApp download links for different platforms."""
    platforms = {
        "ios": {
            "name": "iOS (iPhone/iPad)",
            "icon": "🍎",
            "link": settings.get_happ_download_link("ios"),
        },
        "android": {
            "name": "Android",
            "icon": "🤖",
            "link": settings.get_happ_download_link("android"),
        },
        "macos": {
            "name": "macOS",
            "icon": "🖥️",
            "link": settings.get_happ_download_link("macos"),
        },
        "windows": {
            "name": "Windows",
            "icon": "💻",
            "link": settings.get_happ_download_link("windows"),
        },
    }

    # Filter out platforms without links
    available_platforms = {
        k: v for k, v in platforms.items() if v["link"]
    }

    return {
        "platforms": available_platforms,
        "happ_enabled": bool(available_platforms),
    }


@router.get("/app-config")
async def get_app_config(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> Dict[str, Any]:
    """Get app configuration for connection with deep links."""
    await db.refresh(user, ["subscription"])

    subscription_url = None
    if user.subscription:
        subscription_url = user.subscription.subscription_url

    config = _load_app_config()
    platforms_raw = config.get("platforms", {})

    if not isinstance(platforms_raw, dict):
        platforms_raw = {}

    # Build response with deep links
    platforms = {}
    for platform_key, apps in platforms_raw.items():
        if not isinstance(apps, list):
            continue

        platform_apps = []
        for app in apps:
            if not isinstance(app, dict):
                continue

            app_data = {
                "id": app.get("id"),
                "name": app.get("name"),
                "isFeatured": app.get("isFeatured", False),
                "installationStep": app.get("installationStep"),
                "addSubscriptionStep": app.get("addSubscriptionStep"),
                "connectAndUseStep": app.get("connectAndUseStep"),
                "additionalBeforeAddSubscriptionStep": app.get("additionalBeforeAddSubscriptionStep"),
                "additionalAfterAddSubscriptionStep": app.get("additionalAfterAddSubscriptionStep"),
            }

            # Add deep link if subscription exists
            if subscription_url:
                app_data["deepLink"] = _create_deep_link(app, subscription_url)

            platform_apps.append(app_data)

        if platform_apps:
            platforms[platform_key] = platform_apps

    # Platform display names for UI
    platform_names = {
        "ios": {"ru": "iPhone/iPad", "en": "iPhone/iPad"},
        "android": {"ru": "Android", "en": "Android"},
        "macos": {"ru": "macOS", "en": "macOS"},
        "windows": {"ru": "Windows", "en": "Windows"},
        "linux": {"ru": "Linux", "en": "Linux"},
        "androidTV": {"ru": "Android TV", "en": "Android TV"},
        "appleTV": {"ru": "Apple TV", "en": "Apple TV"},
    }

    return {
        "platforms": platforms,
        "platformNames": platform_names,
        "hasSubscription": bool(subscription_url),
        "subscriptionUrl": subscription_url,
        "branding": config.get("config", {}).get("branding", {}),
    }
