import pytest

from app.config import settings
from app.database.models import PromoGroup


@pytest.fixture
def base_discount_settings(monkeypatch):
    monkeypatch.setattr(settings, "BASE_PROMO_GROUP_PERIOD_DISCOUNTS_ENABLED", True)
    monkeypatch.setattr(settings, "BASE_PROMO_GROUP_PERIOD_DISCOUNTS", "60:15")
    yield


def test_base_promo_discount_applies_to_all_categories(base_discount_settings):
    promo_group = PromoGroup(name="Default", is_default=True)

    assert promo_group.get_discount_percent("period", 60) == 15
    assert promo_group.get_discount_percent("servers", 60) == 15
    assert promo_group.get_discount_percent("traffic", 60) == 15
    assert promo_group.get_discount_percent("devices", 60) == 15


def test_specific_category_discount_overrides_base(base_discount_settings):
    promo_group = PromoGroup(
        name="Default", is_default=True, server_discount_percent=5
    )

    assert promo_group.get_discount_percent("servers", 60) == 5
    assert promo_group.get_discount_percent("devices", 60) == 15
