"""
Fixtures for promocode and promo group testing
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timedelta

from app.database.models import PromoCodeType


@pytest.fixture
def sample_promo_group():
    """Sample PromoGroup object for testing"""
    return SimpleNamespace(
        id=1,
        name="Test VIP Group",
        priority=50,
        server_discount_percent=20,
        traffic_discount_percent=15,
        device_discount_percent=10,
        period_discounts={30: 10, 60: 15, 90: 20},
        is_default=False,
        auto_assign_total_spent_kopeks=None,
        auto_assign_enabled=False,
        addon_discount_enabled=True
    )


@pytest.fixture
def sample_user():
    """Sample User object for testing"""
    return SimpleNamespace(
        id=1,
        telegram_id=123456789,
        username="testuser",
        full_name="Test User",
        balance_kopeks=0,
        language="ru",
        has_had_paid_subscription=False,
        total_spent_kopeks=0
    )


@pytest.fixture
def sample_promocode_balance():
    """Balance type promocode"""
    return SimpleNamespace(
        id=1,
        code="BALANCE100",
        type=PromoCodeType.BALANCE.value,
        balance_bonus_kopeks=10000,  # 100 rubles
        subscription_days=0,
        max_uses=100,
        current_uses=10,
        is_active=True,
        promo_group_id=None,
        promo_group=None,
        valid_until=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        created_by=1
    )


@pytest.fixture
def sample_promocode_subscription():
    """Subscription days type promocode"""
    return SimpleNamespace(
        id=2,
        code="SUB30",
        type=PromoCodeType.SUBSCRIPTION_DAYS.value,
        balance_bonus_kopeks=0,
        subscription_days=30,
        max_uses=50,
        current_uses=5,
        is_active=True,
        promo_group_id=None,
        promo_group=None,
        valid_until=datetime.utcnow() + timedelta(days=60),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        created_by=1
    )


@pytest.fixture
def sample_promocode_promo_group(sample_promo_group):
    """Promo group type promocode"""
    return SimpleNamespace(
        id=3,
        code="VIPGROUP",
        type=PromoCodeType.PROMO_GROUP.value,
        balance_bonus_kopeks=0,
        subscription_days=0,
        max_uses=100,
        current_uses=20,
        is_active=True,
        promo_group_id=sample_promo_group.id,
        promo_group=sample_promo_group,
        valid_until=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        created_by=1
    )


@pytest.fixture
def sample_promocode_invalid():
    """Invalid/expired promocode"""
    return SimpleNamespace(
        id=4,
        code="EXPIRED",
        type=PromoCodeType.BALANCE.value,
        balance_bonus_kopeks=5000,
        subscription_days=0,
        max_uses=10,
        current_uses=10,  # Used up
        is_active=False,
        promo_group_id=None,
        promo_group=None,
        valid_until=datetime.utcnow() - timedelta(days=1),  # Expired
        created_at=datetime.utcnow() - timedelta(days=30),
        updated_at=datetime.utcnow(),
        created_by=1
    )


@pytest.fixture
def mock_db_session():
    """Mock AsyncSession"""
    db = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    db.get = AsyncMock()
    db.execute = AsyncMock()
    db.add = AsyncMock()
    return db


@pytest.fixture
def mock_has_user_promo_group():
    """Mock has_user_promo_group function"""
    return AsyncMock(return_value=False)


@pytest.fixture
def mock_add_user_to_promo_group():
    """Mock add_user_to_promo_group function"""
    return AsyncMock()


@pytest.fixture
def mock_get_promo_group_by_id(sample_promo_group):
    """Mock get_promo_group_by_id function"""
    return AsyncMock(return_value=sample_promo_group)


@pytest.fixture
def mock_get_user_by_id(sample_user):
    """Mock get_user_by_id function"""
    return AsyncMock(return_value=sample_user)


@pytest.fixture
def mock_get_promocode_by_code():
    """Mock get_promocode_by_code function"""
    return AsyncMock()


@pytest.fixture
def mock_check_user_promocode_usage():
    """Mock check_user_promocode_usage function"""
    return AsyncMock(return_value=False)


@pytest.fixture
def mock_create_promocode_use():
    """Mock create_promocode_use function"""
    return AsyncMock()


@pytest.fixture
def mock_remnawave_service():
    """Mock RemnaWaveService"""
    service = AsyncMock()
    service.create_remnawave_user = AsyncMock()
    service.update_remnawave_user = AsyncMock()
    return service


@pytest.fixture
def mock_subscription_service():
    """Mock SubscriptionService"""
    service = AsyncMock()
    service.create_remnawave_user = AsyncMock()
    service.update_remnawave_user = AsyncMock()
    return service


# Helper function to create a valid promocode property mock
def make_promocode_valid(promocode):
    """Helper to make promocode appear valid (is_valid property)"""
    promocode.is_valid = True
    return promocode
