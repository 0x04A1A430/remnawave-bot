from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

if TYPE_CHECKING:  # pragma: no cover
    from app.database.models import User, PromoGroup

logger = logging.getLogger(__name__)


def calculate_months_from_days(days: int) -> int:
    return max(1, round(days / 30))


def get_remaining_months(end_date: datetime) -> int:
    current_time = datetime.utcnow()
    if end_date <= current_time:
        return 1 
    
    remaining_days = (end_date - current_time).days
    return max(1, round(remaining_days / 30))


def calculate_period_multiplier(period_days: int) -> Tuple[int, float]:
    exact_months = period_days / 30
    months_count = max(1, round(exact_months))
    
    logger.debug(f"Период {period_days} дней = {exact_months:.2f} точных месяцев ≈ {months_count} месяцев для расчета")
    
    return months_count, exact_months


def calculate_prorated_price(
    monthly_price: int,
    end_date: datetime,
    min_charge_months: int = 1
) -> Tuple[int, int]:
    months_remaining = get_remaining_months(end_date)
    months_to_charge = max(min_charge_months, months_remaining)
    
    total_price = monthly_price * months_to_charge
    
    logger.debug(f"Расчет пропорциональной цены: {monthly_price/100}₽/мес × {months_to_charge} мес = {total_price/100}₽")
    
    return total_price, months_to_charge


def apply_percentage_discount(amount: int, percent: int) -> Tuple[int, int]:
    if amount <= 0 or percent <= 0:
        return amount, 0

    clamped_percent = max(0, min(100, percent))
    discount_value = amount * clamped_percent // 100
    discounted_amount = amount - discount_value

    # Round the discounted price up to the nearest full ruble (100 kopeks)
    # to avoid undercharging users because of fractional kopeks.
    if discount_value >= 100 and discounted_amount % 100:
        discounted_amount += 100 - (discounted_amount % 100)
        discounted_amount = min(discounted_amount, amount)
        discount_value = amount - discounted_amount

    logger.debug(
        "Применена скидка %s%%: %s → %s (скидка %s)",
        clamped_percent,
        amount,
        discounted_amount,
        discount_value,
    )

    return discounted_amount, discount_value


def resolve_discount_percent(
    user: Optional["User"],
    promo_group: Optional["PromoGroup"],
    category: str,
    *,
    period_days: Optional[int] = None,
) -> int:
    """Определяет размер скидки для указанной категории."""

    if user is not None:
        try:
            return user.get_promo_discount(category, period_days)
        except AttributeError:  # pragma: no cover - defensive guard
            pass

    if promo_group is not None:
        return promo_group.get_discount_percent(category, period_days)

    return 0


async def compute_simple_subscription_price(
    db: AsyncSession,
    params: Dict[str, Any],
    *,
    user: Optional["User"] = None,
    resolved_squad_uuids: Optional[Sequence[str]] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Вычисляет стоимость простой подписки с учетом всех доплат и скидок."""

    period_days = int(params.get("period_days", 30) or 30)
    attr_name = f"PRICE_{period_days}_DAYS"
    base_price_original = getattr(settings, attr_name, settings.BASE_SUBSCRIPTION_PRICE)

    traffic_limit_raw = params.get("traffic_limit_gb")
    try:
        traffic_limit = int(traffic_limit_raw) if traffic_limit_raw is not None else None
    except (TypeError, ValueError):  # pragma: no cover - defensive conversion
        traffic_limit = None

    if traffic_limit is None or traffic_limit <= 0:
        # Default simple subscriptions already include unlimited traffic.
        traffic_price_original = 0
    else:
        traffic_price_original = settings.get_traffic_price(traffic_limit)

    device_limit_raw = params.get("device_limit", settings.DEFAULT_DEVICE_LIMIT)
    try:
        device_limit = int(device_limit_raw)
    except (TypeError, ValueError):  # pragma: no cover - defensive conversion
        device_limit = settings.DEFAULT_DEVICE_LIMIT
    additional_devices = max(0, device_limit - settings.DEFAULT_DEVICE_LIMIT)
    devices_price_original = additional_devices * settings.PRICE_PER_DEVICE

    promo_group: Optional["PromoGroup"] = params.get("promo_group")

    if promo_group is None:
        promo_group_id = params.get("promo_group_id")
        if promo_group_id:
            from app.database.crud.promo_group import get_promo_group_by_id

            promo_group = await get_promo_group_by_id(db, int(promo_group_id))

    if promo_group is None and user is not None:
        promo_group = user.get_primary_promo_group()

    period_discount_percent = resolve_discount_percent(
        user,
        promo_group,
        "period",
        period_days=period_days,
    )
    base_discount = base_price_original * period_discount_percent // 100
    discounted_base = base_price_original - base_discount

    traffic_discount_percent = resolve_discount_percent(
        user,
        promo_group,
        "traffic",
        period_days=period_days,
    )
    traffic_discount = traffic_price_original * traffic_discount_percent // 100
    discounted_traffic = traffic_price_original - traffic_discount

    devices_discount_percent = resolve_discount_percent(
        user,
        promo_group,
        "devices",
        period_days=period_days,
    )
    devices_discount = devices_price_original * devices_discount_percent // 100
    discounted_devices = devices_price_original - devices_discount

    servers_discount_percent = resolve_discount_percent(
        user,
        promo_group,
        "servers",
        period_days=period_days,
    )

    resolved_uuids: List[str] = []
    if resolved_squad_uuids:
        resolved_uuids.extend([uuid for uuid in resolved_squad_uuids if uuid])
    else:
        raw_squad = params.get("squad_uuid")
        if isinstance(raw_squad, (list, tuple, set)):
            resolved_uuids.extend([str(uuid) for uuid in raw_squad if uuid])
        elif raw_squad:
            resolved_uuids.append(str(raw_squad))

    from app.database.crud.server_squad import get_server_squad_by_uuid

    server_breakdown: List[Dict[str, Any]] = []
    servers_price_original = 0
    servers_discount_total = 0

    for squad_uuid in resolved_uuids:
        server = await get_server_squad_by_uuid(db, squad_uuid)
        if not server:
            logger.warning(
                "SIMPLE_SUBSCRIPTION_PRICE_SERVER_NOT_FOUND | squad=%s",
                squad_uuid,
            )
            server_breakdown.append(
                {
                    "uuid": squad_uuid,
                    "name": None,
                    "available": False,
                    "original_price": 0,
                    "discount": 0,
                    "final_price": 0,
                }
            )
            continue

        if not server.is_available or server.is_full:
            logger.warning(
                "SIMPLE_SUBSCRIPTION_PRICE_SERVER_UNAVAILABLE | squad=%s | available=%s | full=%s",
                squad_uuid,
                server.is_available,
                server.is_full,
            )
            server_breakdown.append(
                {
                    "uuid": squad_uuid,
                    "name": server.display_name,
                    "available": False,
                    "original_price": 0,
                    "discount": 0,
                    "final_price": 0,
                }
            )
            continue

        original_price = server.price_kopeks
        discount_value = original_price * servers_discount_percent // 100
        final_price = original_price - discount_value

        servers_price_original += original_price
        servers_discount_total += discount_value

        server_breakdown.append(
            {
                "uuid": squad_uuid,
                "name": server.display_name,
                "available": True,
                "original_price": original_price,
                "discount": discount_value,
                "final_price": final_price,
            }
        )

    total_before_discount = (
        base_price_original
        + traffic_price_original
        + devices_price_original
        + servers_price_original
    )

    total_discount = (
        base_discount
        + traffic_discount
        + devices_discount
        + servers_discount_total
    )

    total_price = max(0, total_before_discount - total_discount)

    breakdown = {
        "base_price": base_price_original,
        "base_discount": base_discount,
        "traffic_price": traffic_price_original,
        "traffic_discount": traffic_discount,
        "devices_price": devices_price_original,
        "devices_discount": devices_discount,
        "servers_price": servers_price_original,
        "servers_discount": servers_discount_total,
        "servers_final": sum(item["final_price"] for item in server_breakdown),
        "server_details": server_breakdown,
        "total_before_discount": total_before_discount,
        "total_discount": total_discount,
        "resolved_squad_uuids": resolved_uuids,
        "applied_promo_group_id": getattr(promo_group, "id", None) if promo_group else None,
        "period_discount_percent": period_discount_percent,
        "traffic_discount_percent": traffic_discount_percent,
        "devices_discount_percent": devices_discount_percent,
        "servers_discount_percent": servers_discount_percent,
    }

    return total_price, breakdown


def format_period_description(days: int, language: str = "ru") -> str:
    months = calculate_months_from_days(days)
    
    if language == "ru":
        if days == 14:
            return "14 дней"
        if days == 30:
            return "1 месяц"
        elif days == 60:
            return "2 месяца"
        elif days == 90:
            return "3 месяца"
        elif days == 180:
            return "6 месяцев"
        elif days == 360:
            return "12 месяцев"
        else:
            month_word = "месяц" if months == 1 else ("месяца" if 2 <= months <= 4 else "месяцев")
            return f"{days} дней ({months} {month_word})"
    else:
        if days == 14:
            return "14 days"
        month_word = "month" if months == 1 else "months"
        return f"{days} days ({months} {month_word})"


def validate_pricing_calculation(
    base_price: int,
    monthly_additions: int,
    months: int,
    total_calculated: int
) -> bool:
    expected_total = base_price + (monthly_additions * months)
    is_valid = expected_total == total_calculated
    
    if not is_valid:
        logger.warning(f"Несоответствие в расчете цены: ожидалось {expected_total/100}₽, получено {total_calculated/100}₽")
        logger.warning(f"Детали: базовая цена {base_price/100}₽ + месячные дополнения {monthly_additions/100}₽ × {months} мес")
    
    return is_valid


STANDARD_PERIODS = {
    14: {"months": 0.5, "display_ru": "2 недели", "display_en": "2 weeks"},
    30: {"months": 1, "display_ru": "1 месяц", "display_en": "1 month"},
    60: {"months": 2, "display_ru": "2 месяца", "display_en": "2 months"},
    90: {"months": 3, "display_ru": "3 месяца", "display_en": "3 months"},
    180: {"months": 6, "display_ru": "6 месяцев", "display_en": "6 months"},
    360: {"months": 12, "display_ru": "1 год", "display_en": "1 year"},
}


def get_period_info(days: int) -> dict:
    return STANDARD_PERIODS.get(days)
