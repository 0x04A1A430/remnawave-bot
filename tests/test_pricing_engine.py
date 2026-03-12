import pytest
from app.services.pricing_engine import RenewalPricing, PricingEngine


def test_renewal_pricing_is_frozen():
    p = RenewalPricing(
        base_price=29000,
        servers_price=5000,
        traffic_price=0,
        devices_price=0,
        promo_group_discount=0,
        promo_offer_discount=0,
        final_total=34000,
        period_days=30,
        is_tariff_mode=False,
    )
    assert p.final_total == 34000
    with pytest.raises(AttributeError):
        p.final_total = 0


class TestApplyDiscount:
    def test_basic_discount(self):
        assert PricingEngine.apply_discount(10000, 20) == 8000

    def test_zero_discount(self):
        assert PricingEngine.apply_discount(10000, 0) == 10000

    def test_full_discount(self):
        assert PricingEngine.apply_discount(10000, 100) == 0

    def test_negative_clamped(self):
        assert PricingEngine.apply_discount(10000, -5) == 10000

    def test_over_100_clamped(self):
        assert PricingEngine.apply_discount(10000, 150) == 0

    def test_integer_floor_division(self):
        assert PricingEngine.apply_discount(99900, 30) == 69930


class TestStackedDiscounts:
    def test_group_then_offer(self):
        final, g_val, o_val = PricingEngine.apply_stacked_discounts(10000, 20, 10)
        assert final == 7200
        assert g_val == 2000
        assert o_val == 800

    def test_no_discounts(self):
        final, g_val, o_val = PricingEngine.apply_stacked_discounts(10000, 0, 0)
        assert final == 10000
        assert g_val == 0
        assert o_val == 0

    def test_only_offer(self):
        final, g_val, o_val = PricingEngine.apply_stacked_discounts(10000, 0, 15)
        assert final == 8500
        assert g_val == 0
        assert o_val == 1500
