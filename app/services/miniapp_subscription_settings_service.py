from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.server_squad import (
    add_user_to_servers,
    get_available_server_squads,
    get_server_ids_by_uuids,
    get_server_squad_by_uuid,
    remove_user_from_servers,
)
from app.database.crud.subscription import (
    add_subscription_servers,
    remove_subscription_squad,
)
from app.database.crud.transaction import create_transaction
from app.database.crud.user import subtract_user_balance
from app.database.models import Subscription, TransactionType, User
from app.services.subscription_service import SubscriptionService
from app.utils.pricing_utils import (
    apply_percentage_discount,
    calculate_prorated_price,
    get_remaining_months,
)


logger = logging.getLogger(__name__)


def _get_addon_discount_percent(
    user: Optional[User],
    category: str,
    period_days_hint: Optional[int] = None,
) -> int:
    if user is None:
        return 0

    promo_group = getattr(user, "promo_group", None)
    if promo_group is None:
        return 0

    if not getattr(promo_group, "apply_discounts_to_addons", True):
        return 0

    try:
        return int(user.get_promo_discount(category, period_days_hint) or 0)
    except AttributeError:  # pragma: no cover - defensive fallback
        return 0


def _get_period_hint_days(subscription: Optional[Subscription]) -> Optional[int]:
    if not subscription:
        return None

    months_remaining = get_remaining_months(subscription.end_date)
    if months_remaining <= 0:
        return None

    return months_remaining * 30


async def _resolve_current_servers(
    db: AsyncSession,
    squad_uuids: Sequence[str],
) -> List[Dict[str, str]]:
    resolved: List[Dict[str, str]] = []
    for squad_uuid in squad_uuids:
        if not squad_uuid:
            continue

        server = await get_server_squad_by_uuid(db, squad_uuid)
        name = getattr(server, "display_name", None) or getattr(server, "name", None)
        resolved.append({
            "uuid": squad_uuid,
            "name": name or squad_uuid,
        })

    return resolved


async def load_subscription_settings(
    db: AsyncSession,
    user: User,
    subscription: Subscription,
) -> Dict[str, object]:
    if subscription.is_trial:
        raise ValueError("Subscription settings are available for paid subscriptions only")

    current_squads = list(subscription.connected_squads or [])
    current_servers = await _resolve_current_servers(db, current_squads)
    server_set = set(server["uuid"] for server in current_servers if server.get("uuid"))

    period_hint_days = _get_period_hint_days(subscription)
    servers_discount_percent = _get_addon_discount_percent(user, "servers", period_hint_days)
    traffic_discount_percent = _get_addon_discount_percent(user, "traffic", period_hint_days)
    devices_discount_percent = _get_addon_discount_percent(user, "devices", period_hint_days)

    available_servers = await get_available_server_squads(
        db,
        promo_group_id=getattr(user, "promo_group_id", None),
    )

    server_options: List[Dict[str, object]] = []
    for server in available_servers:
        price_per_month = int(getattr(server, "price_kopeks", 0) or 0)
        discounted_per_month, discount_per_month = apply_percentage_discount(
            price_per_month,
            servers_discount_percent,
        )
        server_options.append({
            "uuid": server.squad_uuid,
            "name": getattr(server, "display_name", None) or server.squad_uuid,
            "price_kopeks": discounted_per_month,
            "price_label": settings.format_price(discounted_per_month),
            "discount_percent": servers_discount_percent if discount_per_month else 0,
            "is_connected": server.squad_uuid in server_set,
            "is_available": bool(server.is_available and not server.is_full),
            "disabled_reason": None,
        })

    traffic_options: List[Dict[str, object]] = []
    packages = [pkg for pkg in settings.get_traffic_packages() if pkg.get("enabled")]
    for package in packages:
        gb_value = int(package.get("gb", 0) or 0)
        price_per_month = int(package.get("price", 0) or 0)
        discounted_per_month, discount_per_month = apply_percentage_discount(
            price_per_month,
            traffic_discount_percent,
        )
        traffic_options.append({
            "value": gb_value,
            "label": "",
            "price_kopeks": discounted_per_month,
            "price_label": settings.format_price(discounted_per_month),
            "discount_percent": traffic_discount_percent if discount_per_month else 0,
            "is_current": gb_value == subscription.traffic_limit_gb,
            "is_available": True,
        })

    max_devices_limit = settings.MAX_DEVICES_LIMIT if settings.MAX_DEVICES_LIMIT > 0 else 0
    options_range: Iterable[int]
    if max_devices_limit:
        options_range = range(1, max_devices_limit + 1)
    else:
        # Fallback range when limit is not set – allow up to current + 10 devices
        options_range = range(1, max(subscription.device_limit + 10, settings.DEFAULT_DEVICE_LIMIT + 5))

    device_options: List[Dict[str, object]] = []
    for value in options_range:
        chargeable_current = max(0, subscription.device_limit - settings.DEFAULT_DEVICE_LIMIT)
        chargeable_candidate = max(0, value - settings.DEFAULT_DEVICE_LIMIT)
        additional_devices = max(0, chargeable_candidate - chargeable_current)
        price_per_month = additional_devices * settings.PRICE_PER_DEVICE
        discounted_per_month, discount_per_month = apply_percentage_discount(
            price_per_month,
            devices_discount_percent,
        )
        device_options.append({
            "value": value,
            "label": str(value),
            "price_kopeks": discounted_per_month,
            "price_label": settings.format_price(discounted_per_month) if discounted_per_month else None,
            "discount_percent": devices_discount_percent if discount_per_month else 0,
        })

    return {
        "subscription_id": subscription.id,
        "currency": (getattr(user, "balance_currency", None) or "RUB").upper(),
        "current": {
            "servers": current_servers,
            "traffic_limit_gb": subscription.traffic_limit_gb,
            "traffic_limit_label": None,
            "device_limit": subscription.device_limit,
        },
        "servers": {
            "available": server_options,
            "min": 1,
            "max": 0,
            "can_update": True,
            "hint": None,
        },
        "traffic": {
            "options": traffic_options,
            "can_update": settings.is_traffic_selectable(),
            "current_value": subscription.traffic_limit_gb,
        },
        "devices": {
            "options": device_options,
            "can_update": True,
            "min": 1,
            "max": max_devices_limit,
            "step": 1,
            "current": subscription.device_limit,
        },
    }


async def _charge_user(
    db: AsyncSession,
    user: User,
    amount_kopeks: int,
    description: str,
) -> None:
    if amount_kopeks <= 0:
        return

    success = await subtract_user_balance(
        db,
        user,
        amount_kopeks,
        description,
        create_transaction=False,
        payment_method=None,
    )

    if not success:
        raise ValueError("insufficient_funds")

    await create_transaction(
        db=db,
        user_id=user.id,
        type=TransactionType.SUBSCRIPTION_PAYMENT,
        amount_kopeks=amount_kopeks,
        description=description,
    )


async def update_subscription_servers(
    db: AsyncSession,
    user: User,
    subscription: Subscription,
    requested_squads: Sequence[str],
) -> Dict[str, object]:
    if subscription.is_trial:
        raise ValueError("Subscription settings are available for paid subscriptions only")

    requested = [squad for squad in requested_squads if squad]
    if not requested:
        raise ValueError("At least one server must be selected")

    current_set = set(subscription.connected_squads or [])

    available_servers = await get_available_server_squads(
        db,
        promo_group_id=getattr(user, "promo_group_id", None),
    )
    available_map = {server.squad_uuid: server for server in available_servers}

    desired_set = []
    for squad_uuid in requested:
        if squad_uuid in available_map or squad_uuid in current_set:
            desired_set.append(squad_uuid)

    if not desired_set:
        raise ValueError("No selectable servers provided")

    added = [uuid for uuid in desired_set if uuid not in current_set]
    removed = [uuid for uuid in current_set if uuid not in desired_set]

    period_hint_days = _get_period_hint_days(subscription)
    discount_percent = _get_addon_discount_percent(user, "servers", period_hint_days)

    total_monthly_cost = 0
    added_server_prices: List[int] = []
    added_names: List[str] = []
    total_discount_per_month = 0

    for uuid in added:
        server = available_map.get(uuid)
        price_per_month = int(getattr(server, "price_kopeks", 0) or 0)
        discounted_per_month, discount_per_month = apply_percentage_discount(
            price_per_month,
            discount_percent,
        )
        total_monthly_cost += discounted_per_month
        total_discount_per_month += discount_per_month
        added_names.append(getattr(server, "display_name", None) or uuid)
        added_server_prices.append(discounted_per_month)

    total_cost = 0
    charged_months = 0
    total_discount = 0

    if total_monthly_cost > 0:
        total_cost, charged_months = calculate_prorated_price(
            total_monthly_cost,
            subscription.end_date,
        )
        if total_cost < 0:
            total_cost = 0
        total_discount = total_discount_per_month * charged_months
        if added_server_prices:
            added_server_prices = [price * charged_months for price in added_server_prices]

    description = ""
    if added_names:
        description = "Добавление серверов: " + ", ".join(added_names)

    if total_cost > 0:
        await _charge_user(db, user, total_cost, description or "Добавление серверов")

    if added:
        server_ids = await get_server_ids_by_uuids(db, added)
        if server_ids:
            await add_subscription_servers(db, subscription, server_ids, added_server_prices)
            await add_user_to_servers(db, server_ids)

    if removed:
        for uuid in removed:
            await remove_subscription_squad(db, subscription, uuid)
        server_ids = await get_server_ids_by_uuids(db, removed)
        if server_ids:
            await remove_user_from_servers(db, server_ids)

    subscription.connected_squads = list(desired_set)
    subscription.updated_at = datetime.utcnow()
    await db.commit()

    service = SubscriptionService()
    await service.update_remnawave_user(db, subscription)
    await db.refresh(subscription)
    await db.refresh(user)

    return {
        "added": added,
        "removed": removed,
        "charged_amount": total_cost,
        "charged_months": charged_months,
        "discount_percent": discount_percent if total_discount else 0,
        "discount_amount": total_discount,
    }


async def update_subscription_traffic(
    db: AsyncSession,
    user: User,
    subscription: Subscription,
    new_limit_gb: int,
) -> Dict[str, object]:
    if subscription.is_trial:
        raise ValueError("Subscription settings are available for paid subscriptions only")

    if not settings.is_traffic_selectable():
        raise ValueError("Traffic limit cannot be changed in the current mode")

    current_limit = subscription.traffic_limit_gb
    if new_limit_gb == current_limit:
        return {"charged_amount": 0, "charged_months": 0, "discount_percent": 0, "discount_amount": 0}

    old_price_per_month = settings.get_traffic_price(current_limit)
    new_price_per_month = settings.get_traffic_price(new_limit_gb)

    period_hint_days = _get_period_hint_days(subscription)
    discount_percent = _get_addon_discount_percent(user, "traffic", period_hint_days)

    discounted_old_per_month, _ = apply_percentage_discount(
        old_price_per_month,
        discount_percent,
    )
    discounted_new_per_month, discount_per_month = apply_percentage_discount(
        new_price_per_month,
        discount_percent,
    )

    price_difference_per_month = discounted_new_per_month - discounted_old_per_month
    charged_months = get_remaining_months(subscription.end_date)
    if charged_months <= 0:
        charged_months = 1

    total_discount = discount_per_month * charged_months
    total_cost = 0

    if price_difference_per_month > 0:
        total_cost = price_difference_per_month * charged_months
        description = (
            f"Переключение трафика с {current_limit}ГБ на {new_limit_gb}ГБ"
        )
        await _charge_user(db, user, total_cost, description)

    subscription.traffic_limit_gb = new_limit_gb
    subscription.updated_at = datetime.utcnow()
    await db.commit()

    service = SubscriptionService()
    await service.update_remnawave_user(db, subscription)
    await db.refresh(subscription)
    await db.refresh(user)

    return {
        "charged_amount": total_cost,
        "charged_months": charged_months if price_difference_per_month > 0 else 0,
        "discount_percent": discount_percent if total_discount else 0,
        "discount_amount": total_discount if price_difference_per_month > 0 else 0,
    }


async def update_subscription_devices(
    db: AsyncSession,
    user: User,
    subscription: Subscription,
    new_limit: int,
) -> Dict[str, object]:
    if subscription.is_trial:
        raise ValueError("Subscription settings are available for paid subscriptions only")

    if new_limit <= 0:
        raise ValueError("Device limit must be positive")

    if settings.MAX_DEVICES_LIMIT > 0 and new_limit > settings.MAX_DEVICES_LIMIT:
        raise ValueError("Device limit exceeds allowed maximum")

    current_limit = subscription.device_limit
    if new_limit == current_limit:
        return {"charged_amount": 0, "charged_months": 0, "discount_percent": 0, "discount_amount": 0}

    additional_devices = new_limit - current_limit
    chargeable_current = max(0, current_limit - settings.DEFAULT_DEVICE_LIMIT)
    chargeable_new = max(0, new_limit - settings.DEFAULT_DEVICE_LIMIT)
    chargeable_difference = max(0, chargeable_new - chargeable_current)

    period_hint_days = _get_period_hint_days(subscription)
    discount_percent = _get_addon_discount_percent(user, "devices", period_hint_days)

    price_per_month = chargeable_difference * settings.PRICE_PER_DEVICE
    discounted_per_month, discount_per_month = apply_percentage_discount(
        price_per_month,
        discount_percent,
    )

    charged_months = 0
    total_cost = 0
    total_discount = 0

    if additional_devices > 0 and discounted_per_month > 0:
        total_cost, charged_months = calculate_prorated_price(
            discounted_per_month,
            subscription.end_date,
        )
        total_discount = discount_per_month * charged_months
        description = (
            f"Изменение устройств с {current_limit} до {new_limit}"
        )
        await _charge_user(db, user, total_cost, description)

    subscription.device_limit = new_limit
    subscription.updated_at = datetime.utcnow()
    await db.commit()

    service = SubscriptionService()
    await service.update_remnawave_user(db, subscription)
    await db.refresh(subscription)
    await db.refresh(user)

    return {
        "charged_amount": total_cost,
        "charged_months": charged_months,
        "discount_percent": discount_percent if total_discount else 0,
        "discount_amount": total_discount,
    }

