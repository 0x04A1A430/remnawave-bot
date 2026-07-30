"""Сортировка get_users_list по ближайшему окончанию активной подписки."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.database.crud.user import get_users_list
from app.database.models import PromoGroup, Subscription, SubscriptionStatus, Tariff, User, UserStatus
from tests.fixtures.sqlite_memory import memory_session


TABLES = (
    User.__table__,
    Subscription.__table__,
    Tariff.__table__,
    PromoGroup.__table__,
)


@pytest.mark.asyncio
async def test_order_by_subscription_end_soonest_first_then_no_sub(monkeypatch):
    async with memory_session(monkeypatch, TABLES) as db:
        now = datetime.now(UTC)

        soon = User(
            telegram_id=101,
            username='soon',
            first_name='Soon',
            status=UserStatus.ACTIVE.value,
            language='ru',
        )
        later = User(
            telegram_id=102,
            username='later',
            first_name='Later',
            status=UserStatus.ACTIVE.value,
            language='ru',
        )
        no_sub = User(
            telegram_id=103,
            username='nosub',
            first_name='NoSub',
            status=UserStatus.ACTIVE.value,
            language='ru',
        )
        db.add_all([soon, later, no_sub])
        await db.commit()

        db.add_all(
            [
                Subscription(
                    user_id=soon.id,
                    status=SubscriptionStatus.ACTIVE.value,
                    is_trial=False,
                    start_date=now - timedelta(days=10),
                    end_date=now + timedelta(days=2),
                    remnawave_short_id='short-soon',
                ),
                Subscription(
                    user_id=later.id,
                    status=SubscriptionStatus.ACTIVE.value,
                    is_trial=False,
                    start_date=now - timedelta(days=10),
                    end_date=now + timedelta(days=30),
                    remnawave_short_id='short-later',
                ),
            ]
        )
        await db.commit()

        users = await get_users_list(db, order_by_subscription_end=True, limit=50)

        assert [u.username for u in users] == ['soon', 'later', 'nosub']
