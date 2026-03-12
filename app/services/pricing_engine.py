from __future__ import annotations

import structlog
from dataclasses import dataclass, field

from app.config import settings
from app.database.crud.server_squad import get_server_squad_by_uuid

logger = structlog.get_logger()


@dataclass(frozen=True)
class RenewalPricing:
    """Immutable result of a renewal price calculation."""

    base_price: int  # kopeks
    servers_price: int  # kopeks
    traffic_price: int  # kopeks
    devices_price: int  # kopeks
    promo_group_discount: int  # kopeks deducted
    promo_offer_discount: int  # kopeks deducted
    final_total: int  # kopeks — amount to charge
    period_days: int
    is_tariff_mode: bool
    breakdown: dict = field(default_factory=dict)


class PricingEngine:
    """Unified pricing engine for all subscription renewal calculations."""

    @staticmethod
    def apply_discount(amount_kopeks: int, percent: int) -> int:
        """Apply percentage discount with integer arithmetic.
        Clamps percent to [0, 100]. Uses floor division."""
        percent = max(0, min(100, percent))
        discount = amount_kopeks * percent // 100
        return amount_kopeks - discount

    @staticmethod
    def apply_stacked_discounts(
        amount: int,
        group_percent: int,
        offer_percent: int,
    ) -> tuple[int, int, int]:
        """Apply promo-group discount, then promo-offer discount sequentially.
        Returns (final_amount, group_discount_value, offer_discount_value)."""
        after_group = PricingEngine.apply_discount(amount, group_percent)
        group_discount_value = amount - after_group
        after_offer = PricingEngine.apply_discount(after_group, offer_percent)
        offer_discount_value = after_group - after_offer
        return after_offer, group_discount_value, offer_discount_value

    async def _calculate_servers_price(
        self,
        country_uuids: list[str],
        db,  # AsyncSession
        *,
        promo_group_id: int | None = None,
    ) -> tuple[int, list[dict]]:
        """Calculate total server price from connected squad UUIDs.

        Unlike the old implementation, ALWAYS uses real price_kopeks
        even when server is unavailable or full. Only orphaned UUIDs
        (not found in DB) get price=0.
        """
        total_price = 0
        details: list[dict] = []

        for uuid in country_uuids:
            try:
                server = await get_server_squad_by_uuid(db, uuid)
            except Exception as e:
                logger.error("Ошибка загрузки сервера", squad_uuid=uuid, error=str(e))
                details.append({"uuid": uuid, "price": 0, "status": "error"})
                continue

            if server is None:
                logger.error("Сервер не найден в БД", squad_uuid=uuid)
                details.append({"uuid": uuid, "price": 0, "status": "not_found"})
                continue

            price = server.price_kopeks or 0
            status = "available"

            if not server.is_available:
                status = "unavailable"
                logger.warning(
                    "Сервер недоступен, используем реальную цену",
                    squad_uuid=uuid,
                    price_kopeks=price,
                )
            elif server.is_full:
                status = "full"
                logger.warning(
                    "Сервер переполнен, используем реальную цену",
                    squad_uuid=uuid,
                    price_kopeks=price,
                )
            elif promo_group_id is not None:
                allowed_ids = [pg.id for pg in (server.allowed_promo_groups or [])]
                if allowed_ids and promo_group_id not in allowed_ids:
                    status = "not_allowed"
                    logger.warning(
                        "Сервер недоступен для промогруппы, используем реальную цену",
                        squad_uuid=uuid,
                        promo_group_id=promo_group_id,
                        price_kopeks=price,
                    )

            total_price += price
            details.append({"uuid": uuid, "price": price, "status": status})

        return total_price, details

    def _calculate_traffic_price(
        self,
        traffic_limit_gb: int,
        purchased_traffic_gb: int,
    ) -> int:
        """Calculate traffic price, separating base from purchased GB.
        Prevents purchased top-ups from inflating the tier lookup."""
        total_gb = traffic_limit_gb or 0
        purchased_gb = purchased_traffic_gb or 0
        base_gb = max(0, total_gb - purchased_gb)

        base_price = settings.get_traffic_price(base_gb) if base_gb > 0 else 0
        purchased_price = settings.get_traffic_price(purchased_gb) if purchased_gb > 0 else 0

        return base_price + purchased_price
