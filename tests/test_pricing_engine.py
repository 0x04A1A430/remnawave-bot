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


from unittest.mock import AsyncMock, MagicMock, patch


def _make_server(price_kopeks=5000, is_available=True, is_full=False, allowed_promo_groups=None):
    server = MagicMock()
    server.price_kopeks = price_kopeks
    server.is_available = is_available
    server.is_full = is_full
    server.allowed_promo_groups = allowed_promo_groups or []
    return server


class TestCalculateServersPrice:
    @pytest.mark.asyncio
    async def test_available_server(self):
        engine = PricingEngine()
        db = AsyncMock()
        server = _make_server(price_kopeks=5000)
        with patch("app.services.pricing_engine.get_server_squad_by_uuid", return_value=server):
            total, details = await engine._calculate_servers_price(["uuid-1"], db, promo_group_id=None)
        assert total == 5000
        assert len(details) == 1
        assert details[0]["price"] == 5000

    @pytest.mark.asyncio
    async def test_unavailable_server_uses_real_price(self):
        engine = PricingEngine()
        db = AsyncMock()
        server = _make_server(price_kopeks=7000, is_available=False)
        with patch("app.services.pricing_engine.get_server_squad_by_uuid", return_value=server):
            total, details = await engine._calculate_servers_price(["uuid-1"], db, promo_group_id=None)
        assert total == 7000  # NOT 0!
        assert details[0]["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_full_server_uses_real_price(self):
        engine = PricingEngine()
        db = AsyncMock()
        server = _make_server(price_kopeks=3000, is_full=True)
        with patch("app.services.pricing_engine.get_server_squad_by_uuid", return_value=server):
            total, details = await engine._calculate_servers_price(["uuid-1"], db, promo_group_id=None)
        assert total == 3000  # NOT 0!

    @pytest.mark.asyncio
    async def test_server_not_found_zero_price(self):
        engine = PricingEngine()
        db = AsyncMock()
        with patch("app.services.pricing_engine.get_server_squad_by_uuid", return_value=None):
            total, details = await engine._calculate_servers_price(["uuid-orphan"], db, promo_group_id=None)
        assert total == 0
        assert details[0]["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_multiple_servers(self):
        engine = PricingEngine()
        db = AsyncMock()
        s1 = _make_server(price_kopeks=5000)
        s2 = _make_server(price_kopeks=3000, is_available=False)
        with patch("app.services.pricing_engine.get_server_squad_by_uuid", side_effect=[s1, s2]):
            total, details = await engine._calculate_servers_price(["uuid-1", "uuid-2"], db, promo_group_id=None)
        assert total == 8000


class TestCalculateTrafficPrice:
    def test_base_only(self):
        engine = PricingEngine()
        with patch("app.services.pricing_engine.settings") as ms:
            ms.get_traffic_price.side_effect = lambda gb: {25: 3000, 50: 5000}.get(gb, 0)
            price = engine._calculate_traffic_price(traffic_limit_gb=25, purchased_traffic_gb=0)
        assert price == 3000

    def test_purchased_separated(self):
        engine = PricingEngine()
        with patch("app.services.pricing_engine.settings") as ms:
            ms.get_traffic_price.side_effect = lambda gb: {25: 3000, 100: 8000, 125: 12000}.get(gb, 0)
            price = engine._calculate_traffic_price(traffic_limit_gb=125, purchased_traffic_gb=100)
        assert price == 11000  # NOT 12000

    def test_zero_traffic(self):
        engine = PricingEngine()
        with patch("app.services.pricing_engine.settings") as ms:
            ms.get_traffic_price.return_value = 0
            price = engine._calculate_traffic_price(traffic_limit_gb=0, purchased_traffic_gb=0)
        assert price == 0

    def test_purchased_exceeds_total(self):
        engine = PricingEngine()
        with patch("app.services.pricing_engine.settings") as ms:
            ms.get_traffic_price.side_effect = lambda gb: {0: 0, 100: 8000}.get(gb, 0)
            price = engine._calculate_traffic_price(traffic_limit_gb=80, purchased_traffic_gb=100)
        assert price == 8000  # base_gb clamped to 0
