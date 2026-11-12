"""Удалённые пользователи не попадают в обычный список и поиск админки."""

from __future__ import annotations

import pytest

from app.database.crud.user import get_users_count, get_users_list
from app.database.models import PromoGroup, Subscription, Tariff, User, UserStatus
from tests.fixtures.sqlite_memory import memory_session


TABLES = (
    User.__table__,
    Subscription.__table__,
    Tariff.__table__,
    PromoGroup.__table__,
)


def _user(user_id: int, status: str) -> User:
    return User(
        id=user_id,
        telegram_id=1000 + user_id,
        username=f'user{user_id}',
        first_name=f'U{user_id}',
        language='ru',
        status=status,
        balance_kopeks=0,
    )


@pytest.mark.asyncio
async def test_deleted_users_are_hidden_until_explicit_filter(monkeypatch) -> None:
    async with memory_session(monkeypatch, TABLES) as db:
        db.add_all(
            [
                _user(1, UserStatus.ACTIVE.value),
                _user(2, UserStatus.BLOCKED.value),
                *(_user(i, UserStatus.DELETED.value) for i in range(3, 6)),
            ]
        )
        await db.commit()

        visible = await get_users_list(db)
        assert sorted(user.id for user in visible) == [1, 2]
        assert await get_users_count(db) == 2

        search = await get_users_list(db, search='user4')
        assert search == [], 'удалённый пользователь не должен находиться поиском'

        deleted = await get_users_list(db, status=UserStatus.DELETED)
        assert sorted(user.id for user in deleted) == [3, 4, 5]
