"""Истёкшая подписка продлевается с баланса, даже если autopay не включали."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.database.models import Base, Subscription, SubscriptionStatus, Tariff, User
from tests.fixtures.sqlite_memory import memory_session


TABLES = list(Base.metadata.sorted_tables)


class _FakePanelSync:
    async def update_remnawave_user(self, db, subscription, **kwargs):
        return SimpleNamespace(id=9001, used_traffic_bytes=0)

    async def create_remnawave_user(self, db, subscription, **kwargs):
        return SimpleNamespace(id=9001, used_traffic_bytes=0)


@pytest.fixture(autouse=True)
def tariffs_mode(monkeypatch):
    monkeypatch.setattr(settings, 'SALES_MODE', 'tariffs')
    monkeypatch.setattr(settings, 'MULTI_TARIFF_ENABLED', True)
    monkeypatch.setattr(settings, 'DEFAULT_AUTOPAY_PERIOD_DAYS', 0)
    monkeypatch.setattr(settings, 'AUTO_RENEW_EXPIRED_FROM_BALANCE', True)
    monkeypatch.setattr(settings, 'AUTO_RENEW_EXPIRED_WINDOW_HOURS', 24)


def _rows(*, balance_kopeks: int, autopay_enabled: bool, expired_hours: int = 1) -> list:
    now = datetime.now(UTC)
    return [
        User(
            id=1,
            telegram_id=1001,
            first_name='U',
            language='ru',
            status='active',
            balance_kopeks=balance_kopeks,
            remnawave_id=9001,
        ),
        Tariff(
            id=1,
            name='Месяц',
            description='',
            is_active=True,
            is_daily=False,
            period_prices={'30': 30000},
            traffic_limit_gb=100,
            device_limit=1,
            allowed_squads=['squad-1'],
            display_order=1,
        ),
        Subscription(
            id=10,
            remnawave_short_id='exp1',
            remnawave_id=9001,
            user_id=1,
            status=SubscriptionStatus.EXPIRED.value,
            is_trial=False,
            start_date=now - timedelta(days=31),
            end_date=now - timedelta(hours=expired_hours),
            traffic_limit_gb=100,
            traffic_used_gb=0.0,
            device_limit=1,
            tariff_id=1,
            connected_squads=['squad-1'],
            autopay_enabled=autopay_enabled,
            autopay_days_before=3,
            autopay_period_days=30,
        ),
    ]


async def _run_autopay(db, monkeypatch, *, balance_kopeks: int, autopay_enabled: bool):
    import app.services.monitoring_service as monitoring_module
    import app.services.subscription_renewal_service as renewal_module

    monkeypatch.setattr(renewal_module, 'with_admin_notification_service', AsyncMock(return_value=None))

    db.add_all(_rows(balance_kopeks=balance_kopeks, autopay_enabled=autopay_enabled))
    await db.commit()
    before = (await db.get(Subscription, 10)).end_date

    service = monitoring_module.MonitoringService.__new__(monitoring_module.MonitoringService)
    service.bot = None
    service._notified_users = set()
    service._autopay_fail_state = {}
    service.subscription_service = _FakePanelSync()

    await service._process_autopayments(db)

    subscription = await db.get(Subscription, 10)
    await db.refresh(subscription)
    user = await db.get(User, 1)
    await db.refresh(user)
    return before, subscription, user


@pytest.mark.asyncio
async def test_expired_subscription_renews_from_balance_without_autopay(monkeypatch):
    async with memory_session(monkeypatch, TABLES) as db:
        before, subscription, user = await _run_autopay(
            db, monkeypatch, balance_kopeks=100000, autopay_enabled=False
        )

    assert subscription.end_date > before
    assert subscription.status == SubscriptionStatus.ACTIVE.value
    assert user.balance_kopeks == 70000


@pytest.mark.asyncio
async def test_expired_subscription_stays_untouched_when_balance_is_short(monkeypatch):
    async with memory_session(monkeypatch, TABLES) as db:
        before, subscription, user = await _run_autopay(
            db, monkeypatch, balance_kopeks=10000, autopay_enabled=False
        )

    assert subscription.end_date == before
    assert subscription.status == SubscriptionStatus.EXPIRED.value
    assert user.balance_kopeks == 10000


@pytest.mark.asyncio
async def test_expired_subscription_outside_window_is_not_renewed(monkeypatch):
    async with memory_session(monkeypatch, TABLES) as db:
        db.add_all(_rows(balance_kopeks=100000, autopay_enabled=False, expired_hours=48))
        await db.commit()
        before = (await db.get(Subscription, 10)).end_date

        import app.services.monitoring_service as monitoring_module

        service = monitoring_module.MonitoringService.__new__(monitoring_module.MonitoringService)
        service.bot = None
        service._notified_users = set()
        service._autopay_fail_state = {}
        service.subscription_service = _FakePanelSync()

        await service._process_autopayments(db)

        subscription = await db.get(Subscription, 10)
        await db.refresh(subscription)
        user = await db.get(User, 1)
        await db.refresh(user)

    assert subscription.end_date == before
    assert user.balance_kopeks == 100000
