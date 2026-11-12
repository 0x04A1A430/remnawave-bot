"""Неактивные: старая истёкшая подписка не спасает аккаунт от очистки."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.database.crud.user import get_inactive_users
from app.database.models import PromoGroup, Subscription, Tariff, User, UserPromoGroup
from tests.fixtures.sqlite_memory import memory_session


TABLES = (
    User.__table__,
    Subscription.__table__,
    Tariff.__table__,
    PromoGroup.__table__,
    UserPromoGroup.__table__,
)


def _user(user_id: int, *, inactive_days: int) -> User:
    return User(
        id=user_id,
        telegram_id=1000 + user_id,
        first_name=f'U{user_id}',
        language='ru',
        status='active',
        balance_kopeks=0,
        last_activity=datetime.now(UTC) - timedelta(days=inactive_days),
    )


def _sub(user_id: int, *, end_days_ago: int, status: str = 'expired') -> Subscription:
    return Subscription(
        user_id=user_id,
        remnawave_short_id=f'short-{user_id}',
        status=status,
        is_trial=False,
        end_date=datetime.now(UTC) - timedelta(days=end_days_ago),
    )


@pytest.mark.asyncio
async def test_old_expired_subscription_is_included_current_is_not(monkeypatch) -> None:
    async with memory_session(monkeypatch, TABLES) as db:
        db.add_all(
            [
                _user(1, inactive_days=40),  # без подписки — можно
                _user(2, inactive_days=40),  # истекла 30 дней назад — можно
                _user(3, inactive_days=40),  # истекла 5 дней назад — нельзя
                _user(4, inactive_days=40),  # статус active — нельзя
                _user(5, inactive_days=10),  # ещё активен в боте — нельзя
            ]
        )
        await db.flush()
        db.add_all(
            [
                _sub(2, end_days_ago=30),
                _sub(3, end_days_ago=5),
                _sub(4, end_days_ago=30, status='active'),
            ]
        )
        await db.commit()

        users = await get_inactive_users(
            db,
            months=1,
            include_deleted=True,
            expired_subscriptions_before_days=14,
        )

    assert sorted(user.id for user in users) == [1, 2]
