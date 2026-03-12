from __future__ import annotations

import structlog
from dataclasses import dataclass, field

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
