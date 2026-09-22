from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.database.crud import user_reminder as crud
from app.database.models import Base, User, UserReminder, UserReminderState
from app.services.user_reminders.conditions import parse_conditions
from tests.fixtures.sqlite_memory import memory_session


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
TABLES = list(Base.metadata.sorted_tables)


def _reminder(**kw) -> UserReminder:
    base = dict(
        name='r',
        channels='both',
        category='service',
        conditions={},
        repeat_every_days=7,
        max_sends=3,
        texts={'ru': {'title': 't', 'body': 'b'}},
        button_kind='none',
        is_active=True,
    )
    base.update(kw)
    return UserReminder(**base)


@pytest.mark.asyncio
async def test_order_is_builtin_first_then_id(monkeypatch):
    async with memory_session(monkeypatch, TABLES) as db:
        db.add_all([_reminder(name='a'), _reminder(name='b', builtin_key='k'), _reminder(name='c', channels='bot')])
        await db.commit()

        assert [r.name for r in await crud.list_reminders(db)] == ['b', 'a', 'c']
        assert [r.name for r in await crud.list_active_reminders(db, crud.CHANNELS_CABINET)] == ['b', 'a']


@pytest.mark.asyncio
async def test_attempts_and_stats(monkeypatch):
    async with memory_session(monkeypatch, TABLES) as db:
        db.add(User(id=1, telegram_id=10, first_name='U', language='ru', status='active', balance_kopeks=0))
        reminder = _reminder()
        db.add(reminder)
        await db.commit()

        await crud.record_bot_attempt(db, reminder.id, 1, now=NOW, success=False)
        await crud.record_bot_attempt(db, reminder.id, 1, now=NOW + timedelta(days=1), success=True)
        state = await crud.get_or_create_state(db, reminder.id, 1)
        state.dismissed_at = NOW
        await db.commit()

        state = (await db.execute(UserReminderState.__table__.select())).one()
        assert state.sends_count == 1
        assert state.last_sent_at == NOW + timedelta(days=1)
        assert state.last_success_at == NOW + timedelta(days=1)
        assert await crud.reminder_stats(db) == {reminder.id: {'sent_total': 1, 'dismissed_total': 1}}


@pytest.mark.asyncio
async def test_audience_counts(monkeypatch):
    async with memory_session(monkeypatch, TABLES) as db:
        db.add_all(
            [
                User(id=1, telegram_id=10, first_name='A', language='ru', status='active', balance_kopeks=0),
                User(
                    id=2,
                    email='e@x',
                    password_hash='h',
                    first_name='B',
                    language='ru',
                    status='active',
                    balance_kopeks=0,
                ),
                User(id=3, telegram_id=30, first_name='C', language='ru', status='deleted', balance_kopeks=0),
            ]
        )
        await db.commit()
        conditions = parse_conditions({'auth': 'single_method'})

        assert await crud.count_audience(db, conditions, now=NOW, telegram_only=False) == 2
        assert await crud.count_audience(db, conditions, now=NOW, telegram_only=True) == 1


@pytest.mark.asyncio
async def test_concurrent_state_creation_survives_race(monkeypatch):
    """Test that get_or_create_state handles concurrent insert without rolling back outer transaction."""
    async with memory_session(monkeypatch, TABLES) as db:
        user = User(id=1, telegram_id=10, first_name='Alice', language='ru', status='active', balance_kopeks=0)
        reminder = _reminder()
        db.add_all([user, reminder])
        await db.commit()

        # Simulate concurrent writer: directly insert a state row before get_or_create_state sees it.
        await db.execute(
            UserReminderState.__table__.insert().values(reminder_id=reminder.id, user_id=user.id, sends_count=5)
        )
        await db.commit()

        # Monkeypatch _find_state: first call returns None (racing reader misses row), later calls use real function.
        original_find_state = crud._find_state
        call_count = 0

        async def mocked_find_state(db, reminder_id, user_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # First call: simulate race condition
            return await original_find_state(db, reminder_id, user_id)

        monkeypatch.setattr(crud, '_find_state', mocked_find_state)

        # Make an unrelated pending change to ensure outer transaction is not rolled back.
        user.first_name = 'Bob'

        # Call get_or_create_state (will hit IntegrityError, then retry with real _find_state).
        state = await crud.get_or_create_state(db, reminder.id, user.id)

        # Verify it returned the pre-existing row.
        assert state.sends_count == 5

        # Commit and verify outer transaction was not rolled back.
        await db.commit()

        # Verify the unrelated change persisted.
        user_row = await db.get(User, user.id)
        assert user_row.first_name == 'Bob'

        # Verify exactly one state row exists for this pair.
        states = (await db.execute(UserReminderState.__table__.select())).fetchall()
        assert len(states) == 1
        assert states[0].sends_count == 5
