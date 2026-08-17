"""Единая точка привязки пользователя к рекламной кампании.

Логика раньше жила только в кабинетном auth-флоу; гостевой покупке с лендинга
нужна ровно она же, поэтому она вынесена в сервис. Тесты пиннят правила,
которые нельзя потерять при переносе.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.campaign_service import AdvertisingCampaignService


def _db() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _campaign(**kw: object) -> SimpleNamespace:
    base: dict[str, object] = {'id': 7, 'name': 'main', 'partner_user_id': None, 'bonus_type': 'balance'}
    base.update(kw)
    return SimpleNamespace(**base)


def _user(user_id: int = 42, referred_by_id: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, referred_by_id=referred_by_id)


@pytest.mark.asyncio
async def test_returns_none_for_empty_slug() -> None:
    service = AdvertisingCampaignService()
    assert await service.attribute_campaign(_db(), _user(), None) is None


@pytest.mark.asyncio
async def test_returns_none_when_campaign_not_found() -> None:
    service = AdvertisingCampaignService()
    with patch(
        'app.services.campaign_service.get_campaign_by_start_parameter',
        AsyncMock(return_value=None),
    ):
        assert await service.attribute_campaign(_db(), _user(), 'ghost') is None


@pytest.mark.asyncio
async def test_partner_cannot_be_attributed_to_own_campaign() -> None:
    """Иначе партнёр накрутит себе регистрацию по собственной ссылке."""
    service = AdvertisingCampaignService()
    with patch(
        'app.services.campaign_service.get_campaign_by_start_parameter',
        AsyncMock(return_value=_campaign(partner_user_id=42)),
    ):
        assert await service.attribute_campaign(_db(), _user(user_id=42), 'main') is None


@pytest.mark.asyncio
async def test_existing_registration_blocks_second_bonus() -> None:
    service = AdvertisingCampaignService()
    with (
        patch(
            'app.services.campaign_service.get_campaign_by_start_parameter',
            AsyncMock(return_value=_campaign()),
        ),
        patch(
            'app.services.campaign_service.get_campaign_registration_by_user',
            AsyncMock(return_value=SimpleNamespace(id=1)),
        ),
        patch.object(AdvertisingCampaignService, 'apply_campaign_bonus', AsyncMock()) as apply_mock,
    ):
        assert await service.attribute_campaign(_db(), _user(), 'main') is None
        apply_mock.assert_not_called()


@pytest.mark.asyncio
async def test_successful_attribution_applies_bonus() -> None:
    service = AdvertisingCampaignService()
    expected = SimpleNamespace(success=True, bonus_type='balance', is_new_registration=True)
    with (
        patch(
            'app.services.campaign_service.get_campaign_by_start_parameter',
            AsyncMock(return_value=_campaign()),
        ),
        patch(
            'app.services.campaign_service.get_campaign_registration_by_user',
            AsyncMock(return_value=None),
        ),
        patch.object(AdvertisingCampaignService, 'apply_campaign_bonus', AsyncMock(return_value=expected)),
    ):
        result = await service.attribute_campaign(_db(), _user(), 'main')

    assert result is expected


@pytest.mark.asyncio
async def test_unsuccessful_bonus_returns_none() -> None:
    service = AdvertisingCampaignService()
    with (
        patch(
            'app.services.campaign_service.get_campaign_by_start_parameter',
            AsyncMock(return_value=_campaign()),
        ),
        patch(
            'app.services.campaign_service.get_campaign_registration_by_user',
            AsyncMock(return_value=None),
        ),
        patch.object(
            AdvertisingCampaignService,
            'apply_campaign_bonus',
            AsyncMock(return_value=SimpleNamespace(success=False)),
        ),
    ):
        assert await service.attribute_campaign(_db(), _user(), 'main') is None


@pytest.mark.asyncio
async def test_partner_is_attached_as_referrer() -> None:
    """Кампания партнёра должна проставить его реферером — иначе он не
    получит комиссию за приведённого клиента."""
    service = AdvertisingCampaignService()
    user = _user(user_id=42, referred_by_id=None)
    with (
        patch(
            'app.services.campaign_service.get_campaign_by_start_parameter',
            AsyncMock(return_value=_campaign(partner_user_id=99)),
        ),
        patch(
            'app.services.campaign_service.get_campaign_registration_by_user',
            AsyncMock(return_value=None),
        ),
        patch.object(AdvertisingCampaignService, '_link_partner_referral', AsyncMock()) as link_mock,
        patch.object(
            AdvertisingCampaignService,
            'apply_campaign_bonus',
            AsyncMock(return_value=SimpleNamespace(success=True, bonus_type='balance')),
        ),
    ):
        await service.attribute_campaign(_db(), user, 'main')

    link_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_errors_are_swallowed_and_rolled_back() -> None:
    """Привязка кампании — побочный эффект: она не имеет права уронить
    вызывающий флоу (регистрацию в кабинете или доставку подписки)."""
    service = AdvertisingCampaignService()
    db = _db()
    with patch(
        'app.services.campaign_service.get_campaign_by_start_parameter',
        AsyncMock(side_effect=RuntimeError('boom')),
    ):
        assert await service.attribute_campaign(db, _user(), 'main') is None

    db.rollback.assert_awaited()
