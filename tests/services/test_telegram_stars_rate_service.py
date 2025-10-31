from pathlib import Path
from unittest.mock import AsyncMock
import sys

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings
from app.services import telegram_stars_rate_service as rate_module
from app.services.telegram_stars_rate_service import TelegramStarsRateService


@pytest.mark.parametrize(
    "payload,expected",
    [
        (
            {"rate": 1.79},
            pytest.approx(1.79),
        ),
        (
            {"pricePerStar": "1.85"},
            pytest.approx(1.85),
        ),
        (
            {
                "packs": [
                    {
                        "stars": 250,
                        "price": {"currency": "RUB", "amount": 44900},
                    }
                ]
            },
            pytest.approx(1.796, rel=1e-3),
        ),
        (
            {
                "data": {
                    "options": [
                        {
                            "star_count": "500",
                            "total_price": {"value": "895", "currency": "RUB"},
                        }
                    ]
                }
            },
            pytest.approx(1.79, rel=1e-3),
        ),
    ],
)
def test_extract_rate(payload, expected):
    assert TelegramStarsRateService._extract_rate(payload) == expected


@pytest.mark.asyncio
async def test_refresh_rate_updates_settings(monkeypatch):
    service = TelegramStarsRateService()
    monkeypatch.setattr(settings, "TELEGRAM_STARS_CUSTOM_RATE_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "TELEGRAM_STARS_RATE_RUB", 1.3, raising=False)
    monkeypatch.setattr(service, "_fetch_rate", AsyncMock(return_value=1.91))

    rate = await service.refresh_rate(force=True)

    assert rate == pytest.approx(1.91)
    assert settings.TELEGRAM_STARS_RATE_RUB == pytest.approx(1.91)


@pytest.mark.asyncio
async def test_refresh_rate_respects_custom_setting(monkeypatch):
    service = TelegramStarsRateService()
    monkeypatch.setattr(settings, "TELEGRAM_STARS_CUSTOM_RATE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "TELEGRAM_STARS_RATE_RUB", 2.05, raising=False)
    fetch_mock = AsyncMock(return_value=1.5)
    monkeypatch.setattr(service, "_fetch_rate", fetch_mock)

    rate = await service.refresh_rate(force=True)

    assert rate == pytest.approx(2.05)
    fetch_mock.assert_not_awaited()


def test_settings_get_stars_rate_uses_dynamic(monkeypatch):
    custom_service = TelegramStarsRateService()
    monkeypatch.setattr(rate_module, "telegram_stars_rate_service", custom_service, raising=False)
    monkeypatch.setattr(settings, "TELEGRAM_STARS_CUSTOM_RATE_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "TELEGRAM_STARS_RATE_RUB", 1.3, raising=False)

    custom_service._rate = 2.34

    assert settings.get_stars_rate() == pytest.approx(2.34)


def test_settings_get_stars_rate_uses_custom(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_STARS_CUSTOM_RATE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "TELEGRAM_STARS_RATE_RUB", 3.21, raising=False)

    assert settings.get_stars_rate() == pytest.approx(3.21)

