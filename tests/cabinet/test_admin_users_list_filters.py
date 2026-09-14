"""Фильтры списка пользователей для сегментов кабинета.

Раздел «Пользователи» в кабинете получил готовые выборки: «Истекают за 7 дней»,
«Онлайн», «Без покупок», «Без подписки», «С ограничениями», а поиск стал одним
полем. Список и счётчик обязаны фильтровать одинаково — иначе «показано 12 из 40»
врёт, а лента не знает, когда остановиться.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from app.database.crud.user import get_users_count, get_users_list
from app.database.models import (
    Subscription,
    SubscriptionStatus,
    Tariff,
    Transaction,
    TransactionType,
    User,
    UserStatus,
)
from tests.fixtures.sqlite_memory import memory_session


TABLES = (User.__table__, Subscription.__table__, Tariff.__table__, Transaction.__table__)
NOW = datetime.now(UTC)


def _user(telegram_id: int, username: str, **extra) -> User:
    return User(
        telegram_id=telegram_id,
        username=username,
        first_name=username.capitalize(),
        status=UserStatus.ACTIVE.value,
        language='ru',
        balance_kopeks=0,
        **extra,
    )


def _subscription(user: User, days_left: int, status: str = SubscriptionStatus.ACTIVE.value) -> Subscription:
    return Subscription(
        user_id=user.id,
        status=status,
        start_date=NOW - timedelta(days=20),
        end_date=NOW + timedelta(days=days_left),
        traffic_limit_gb=100,
        device_limit=1,
        # Колонка уникальна; по умолчанию генератор даёт одно и то же на SQLite.
        remnawave_short_id=f'short{user.id}',
    )


async def _seed(db) -> None:
    soon = _user(1, 'soon', last_activity=NOW - timedelta(minutes=2), email='soon@example.com')
    later = _user(2, 'later', last_activity=NOW - timedelta(hours=3), restriction_topup=True)
    # last_activity по умолчанию ставится «сейчас», поэтому давность задаём явно.
    nobody = _user(3, 'nobody', last_activity=NOW - timedelta(days=30))
    lapsed = _user(4, 'lapsed', last_activity=NOW - timedelta(days=9))
    db.add_all([soon, later, nobody, lapsed])
    await db.flush()
    db.add_all(
        [
            _subscription(soon, days_left=3),
            _subscription(later, days_left=40),
            # Истёкшая неделю назад в «истекают» не попадает, хоть дата и близко.
            _subscription(lapsed, days_left=-7, status=SubscriptionStatus.EXPIRED.value),
            Transaction(
                user_id=later.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT.value,
                amount_kopeks=-50000,
                description='Покупка',
                is_completed=True,
            ),
        ]
    )
    await db.commit()


async def _usernames(db, **filters) -> list[str]:
    return sorted(str(u.username) for u in await get_users_list(db, **filters))


async def test_expires_within_days(monkeypatch: pytest.MonkeyPatch) -> None:
    async with memory_session(monkeypatch, TABLES) as db:
        await _seed(db)
        assert await _usernames(db, expires_within_days=7) == ['soon']
        assert await get_users_count(db, expires_within_days=7) == 1


async def test_active_within_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    async with memory_session(monkeypatch, TABLES) as db:
        await _seed(db)
        assert await _usernames(db, active_within_minutes=5) == ['soon']
        assert await get_users_count(db, active_within_minutes=5) == 1


async def test_has_restrictions(monkeypatch: pytest.MonkeyPatch) -> None:
    async with memory_session(monkeypatch, TABLES) as db:
        await _seed(db)
        assert await _usernames(db, has_restrictions=True) == ['later']
        assert await get_users_count(db, has_restrictions=True) == 1
        assert await _usernames(db, has_restrictions=False) == ['lapsed', 'nobody', 'soon']


async def test_has_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    async with memory_session(monkeypatch, TABLES) as db:
        await _seed(db)
        assert await _usernames(db, has_subscription=False) == ['nobody']
        assert await get_users_count(db, has_subscription=False) == 1
        assert await _usernames(db, has_subscription=True) == ['lapsed', 'later', 'soon']


async def test_no_purchases(monkeypatch: pytest.MonkeyPatch) -> None:
    async with memory_session(monkeypatch, TABLES) as db:
        await _seed(db)
        assert await _usernames(db, purchase_count=0) == ['lapsed', 'nobody', 'soon']
        assert await get_users_count(db, purchase_count=0) == 3


async def test_search_matches_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """Одно поле поиска: адрес целиком и его кусок находят человека через `search`."""
    async with memory_session(monkeypatch, TABLES) as db:
        await _seed(db)
        assert await _usernames(db, search='soon@example.com') == ['soon']
        assert await _usernames(db, search='example.com') == ['soon']
        assert await get_users_count(db, search='example.com') == 1


async def test_filters_combine(monkeypatch: pytest.MonkeyPatch) -> None:
    async with memory_session(monkeypatch, TABLES) as db:
        await _seed(db)
        assert await _usernames(db, expires_within_days=7, active_within_minutes=5) == ['soon']
        assert await _usernames(db, expires_within_days=7, has_restrictions=True) == []


def test_route_declares_new_filters() -> None:
    from app.cabinet.routes.admin_users import list_users

    params = set(inspect.signature(list_users).parameters)
    assert {
        'expires_within_days',
        'active_within_minutes',
        'has_restrictions',
        'has_subscription',
        'purchase_count',
    } <= params
