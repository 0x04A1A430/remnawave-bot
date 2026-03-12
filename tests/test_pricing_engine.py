import pytest

from app.services.pricing_engine import PricingEngine, RenewalPricing


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
        with patch('app.services.pricing_engine.get_server_squad_by_uuid', return_value=server):
            total, details = await engine._calculate_servers_price(['uuid-1'], db, promo_group_id=None)
        assert total == 5000
        assert len(details) == 1
        assert details[0]['price'] == 5000

    @pytest.mark.asyncio
    async def test_unavailable_server_uses_real_price(self):
        engine = PricingEngine()
        db = AsyncMock()
        server = _make_server(price_kopeks=7000, is_available=False)
        with patch('app.services.pricing_engine.get_server_squad_by_uuid', return_value=server):
            total, details = await engine._calculate_servers_price(['uuid-1'], db, promo_group_id=None)
        assert total == 7000  # NOT 0!
        assert details[0]['status'] == 'unavailable'

    @pytest.mark.asyncio
    async def test_full_server_uses_real_price(self):
        engine = PricingEngine()
        db = AsyncMock()
        server = _make_server(price_kopeks=3000, is_full=True)
        with patch('app.services.pricing_engine.get_server_squad_by_uuid', return_value=server):
            total, details = await engine._calculate_servers_price(['uuid-1'], db, promo_group_id=None)
        assert total == 3000  # NOT 0!

    @pytest.mark.asyncio
    async def test_server_not_found_zero_price(self):
        engine = PricingEngine()
        db = AsyncMock()
        with patch('app.services.pricing_engine.get_server_squad_by_uuid', return_value=None):
            total, details = await engine._calculate_servers_price(['uuid-orphan'], db, promo_group_id=None)
        assert total == 0
        assert details[0]['status'] == 'not_found'

    @pytest.mark.asyncio
    async def test_multiple_servers(self):
        engine = PricingEngine()
        db = AsyncMock()
        s1 = _make_server(price_kopeks=5000)
        s2 = _make_server(price_kopeks=3000, is_available=False)
        with patch('app.services.pricing_engine.get_server_squad_by_uuid', side_effect=[s1, s2]):
            total, details = await engine._calculate_servers_price(['uuid-1', 'uuid-2'], db, promo_group_id=None)
        assert total == 8000


class TestCalculateTrafficPrice:
    def test_base_only(self):
        engine = PricingEngine()
        with patch('app.services.pricing_engine.settings') as ms:
            ms.get_traffic_price.side_effect = lambda gb: {25: 3000, 50: 5000}.get(gb, 0)
            price = engine._calculate_traffic_price(traffic_limit_gb=25, purchased_traffic_gb=0)
        assert price == 3000

    def test_purchased_separated(self):
        engine = PricingEngine()
        with patch('app.services.pricing_engine.settings') as ms:
            ms.get_traffic_price.side_effect = lambda gb: {25: 3000, 100: 8000, 125: 12000}.get(gb, 0)
            price = engine._calculate_traffic_price(traffic_limit_gb=125, purchased_traffic_gb=100)
        assert price == 11000  # NOT 12000

    def test_zero_traffic(self):
        engine = PricingEngine()
        with patch('app.services.pricing_engine.settings') as ms:
            ms.get_traffic_price.return_value = 0
            price = engine._calculate_traffic_price(traffic_limit_gb=0, purchased_traffic_gb=0)
        assert price == 0

    def test_purchased_exceeds_total(self):
        engine = PricingEngine()
        with patch('app.services.pricing_engine.settings') as ms:
            ms.get_traffic_price.side_effect = lambda gb: {0: 0, 100: 8000}.get(gb, 0)
            price = engine._calculate_traffic_price(traffic_limit_gb=80, purchased_traffic_gb=100)
        assert price == 8000  # base_gb clamped to 0


class TestCalculateRenewalPriceTariffMode:
    @pytest.mark.asyncio
    async def test_tariff_basic(self):
        engine = PricingEngine()
        db = AsyncMock()
        subscription = MagicMock()
        subscription.tariff_id = 2
        subscription.tariff = MagicMock()
        subscription.tariff.period_prices = {'30': 19000}
        subscription.tariff.device_limit = 2
        subscription.tariff.id = 2
        subscription.device_limit = 2
        subscription.connected_squads = []
        subscription.traffic_limit_gb = 50
        subscription.purchased_traffic_gb = 0
        user = MagicMock()
        user.promo_group = None
        user.promo_offer_discount_percent = 0
        user.promo_offer_expires_at = None
        with (
            patch('app.services.pricing_engine.get_user_active_promo_discount_percent', return_value=0),
            patch('app.services.pricing_engine.settings') as ms,
        ):
            ms.PRICE_PER_DEVICE = 5000
            result = await engine.calculate_renewal_price(db, subscription, 30, user=user)
        assert result.is_tariff_mode is True
        assert result.final_total == 19000

    @pytest.mark.asyncio
    async def test_tariff_extra_devices(self):
        engine = PricingEngine()
        db = AsyncMock()
        subscription = MagicMock()
        subscription.tariff_id = 2
        subscription.tariff = MagicMock()
        subscription.tariff.period_prices = {'30': 19000}
        subscription.tariff.device_limit = 2
        subscription.tariff.id = 2
        subscription.device_limit = 4
        subscription.connected_squads = []
        subscription.traffic_limit_gb = 50
        subscription.purchased_traffic_gb = 0
        user = MagicMock()
        user.promo_group = None
        user.promo_offer_discount_percent = 0
        user.promo_offer_expires_at = None
        with (
            patch('app.services.pricing_engine.get_user_active_promo_discount_percent', return_value=0),
            patch('app.services.pricing_engine.settings') as ms,
        ):
            ms.PRICE_PER_DEVICE = 5000
            result = await engine.calculate_renewal_price(db, subscription, 30, user=user)
        assert result.devices_price == 10000
        assert result.final_total == 29000

    @pytest.mark.asyncio
    async def test_tariff_with_discounts(self):
        engine = PricingEngine()
        db = AsyncMock()
        subscription = MagicMock()
        subscription.tariff_id = 1
        subscription.tariff = MagicMock()
        subscription.tariff.period_prices = {'30': 20000}
        subscription.tariff.device_limit = 1
        subscription.tariff.id = 1
        subscription.device_limit = 1
        promo_group = MagicMock()
        promo_group.get_discount_percent.return_value = 10
        user = MagicMock()
        user.promo_group = promo_group
        with (
            patch('app.services.pricing_engine.get_user_active_promo_discount_percent', return_value=5),
            patch('app.services.pricing_engine.settings') as ms,
        ):
            ms.PRICE_PER_DEVICE = 5000
            result = await engine.calculate_renewal_price(db, subscription, 30, user=user)
        assert result.base_price == 20000
        assert result.promo_group_discount == 2000
        # After group: 18000, then 5% off 18000 = 900
        assert result.promo_offer_discount == 900
        assert result.final_total == 17100

    @pytest.mark.asyncio
    async def test_tariff_missing_period_returns_zero_base(self):
        engine = PricingEngine()
        db = AsyncMock()
        subscription = MagicMock()
        subscription.tariff_id = 1
        subscription.tariff = MagicMock()
        subscription.tariff.period_prices = {'30': 19000}
        subscription.tariff.device_limit = 1
        subscription.tariff.id = 1
        subscription.device_limit = 1
        user = MagicMock()
        user.promo_group = None
        with (
            patch('app.services.pricing_engine.get_user_active_promo_discount_percent', return_value=0),
            patch('app.services.pricing_engine.settings') as ms,
        ):
            ms.PRICE_PER_DEVICE = 5000
            result = await engine.calculate_renewal_price(db, subscription, 60, user=user)
        assert result.base_price == 0
        assert result.final_total == 0


class TestCalculateRenewalPriceClassicMode:
    @pytest.mark.asyncio
    async def test_classic_all_components(self):
        engine = PricingEngine()
        db = AsyncMock()
        subscription = MagicMock()
        subscription.tariff_id = None
        subscription.tariff = None
        subscription.connected_squads = ['uuid-1']
        subscription.traffic_limit_gb = 50
        subscription.purchased_traffic_gb = 0
        subscription.device_limit = 2
        user = MagicMock()
        user.promo_group = None
        user.promo_group_id = None
        user.promo_offer_discount_percent = 0
        user.promo_offer_expires_at = None
        server = _make_server(price_kopeks=5000)
        with (
            patch('app.services.pricing_engine.get_server_squad_by_uuid', return_value=server),
            patch('app.services.pricing_engine.get_user_active_promo_discount_percent', return_value=0),
            patch('app.services.pricing_engine.settings') as ms,
            patch('app.services.pricing_engine.CLASSIC_PERIOD_PRICES', {30: 29000}),
            patch('app.services.pricing_engine.PERIOD_PRICES', {30: 29000}),
        ):
            ms.get_traffic_price.return_value = 3000
            ms.PRICE_PER_DEVICE = 0
            ms.DEFAULT_DEVICE_LIMIT = 2
            result = await engine.calculate_renewal_price(db, subscription, 30, user=user)
        assert result.is_tariff_mode is False
        assert result.base_price == 29000
        assert result.servers_price == 5000
        assert result.traffic_price == 3000
        assert result.final_total == 37000

    @pytest.mark.asyncio
    async def test_classic_with_discounts(self):
        engine = PricingEngine()
        db = AsyncMock()
        subscription = MagicMock()
        subscription.tariff_id = None
        subscription.tariff = None
        subscription.connected_squads = []
        subscription.traffic_limit_gb = 0
        subscription.purchased_traffic_gb = 0
        subscription.device_limit = 2
        promo_group = MagicMock()
        promo_group.id = 1
        promo_group.get_discount_percent.return_value = 20
        user = MagicMock()
        user.promo_group = promo_group
        user.promo_group_id = 1
        user.promo_offer_discount_percent = 10
        user.promo_offer_expires_at = None
        with (
            patch('app.services.pricing_engine.get_user_active_promo_discount_percent', return_value=10),
            patch('app.services.pricing_engine.settings') as ms,
            patch('app.services.pricing_engine.CLASSIC_PERIOD_PRICES', {30: 10000}),
            patch('app.services.pricing_engine.PERIOD_PRICES', {30: 10000}),
        ):
            ms.get_traffic_price.return_value = 0
            ms.PRICE_PER_DEVICE = 0
            ms.DEFAULT_DEVICE_LIMIT = 2
            result = await engine.calculate_renewal_price(db, subscription, 30, user=user)
        assert result.final_total == 7200
        assert result.promo_group_discount == 2000
        assert result.promo_offer_discount == 800

    @pytest.mark.asyncio
    async def test_classic_fallback_to_period_prices(self):
        """When CLASSIC_PERIOD_PRICES has no entry, falls back to PERIOD_PRICES."""
        engine = PricingEngine()
        db = AsyncMock()
        subscription = MagicMock()
        subscription.tariff_id = None
        subscription.tariff = None
        subscription.connected_squads = []
        subscription.traffic_limit_gb = 0
        subscription.purchased_traffic_gb = 0
        subscription.device_limit = 1
        user = MagicMock()
        user.promo_group = None
        user.promo_group_id = None
        with (
            patch('app.services.pricing_engine.get_user_active_promo_discount_percent', return_value=0),
            patch('app.services.pricing_engine.settings') as ms,
            patch('app.services.pricing_engine.CLASSIC_PERIOD_PRICES', {}),
            patch('app.services.pricing_engine.PERIOD_PRICES', {30: 99000}),
        ):
            ms.get_traffic_price.return_value = 0
            ms.PRICE_PER_DEVICE = 0
            ms.DEFAULT_DEVICE_LIMIT = 1
            result = await engine.calculate_renewal_price(db, subscription, 30, user=user)
        assert result.base_price == 99000
        assert result.final_total == 99000

    @pytest.mark.asyncio
    async def test_classic_extra_devices(self):
        engine = PricingEngine()
        db = AsyncMock()
        subscription = MagicMock()
        subscription.tariff_id = None
        subscription.tariff = None
        subscription.connected_squads = []
        subscription.traffic_limit_gb = 0
        subscription.purchased_traffic_gb = 0
        subscription.device_limit = 5
        user = MagicMock()
        user.promo_group = None
        user.promo_group_id = None
        with (
            patch('app.services.pricing_engine.get_user_active_promo_discount_percent', return_value=0),
            patch('app.services.pricing_engine.settings') as ms,
            patch('app.services.pricing_engine.CLASSIC_PERIOD_PRICES', {30: 10000}),
            patch('app.services.pricing_engine.PERIOD_PRICES', {}),
        ):
            ms.get_traffic_price.return_value = 0
            ms.PRICE_PER_DEVICE = 3000
            ms.DEFAULT_DEVICE_LIMIT = 2
            result = await engine.calculate_renewal_price(db, subscription, 30, user=user)
        # 5 - 2 = 3 extra devices * 3000 = 9000
        assert result.devices_price == 9000
        assert result.final_total == 19000
