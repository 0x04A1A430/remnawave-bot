"""End-to-end regression tests for admin inline gifts without chosen_inline_result.

Telegram delivers ``chosen_inline_result`` only when inline feedback is enabled
in @BotFather, so the admin inline-gift row used to be silently missing on a
freshly configured bot. These tests pin the fallback chain:

    inline query answer → Redis draft → /start bs_<code> → materialised row → preview

They also guard the unified ``bs_`` prefix and the normalised bot username.
"""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import ChosenInlineResult, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import (
    Base,
    InlineGiftSubscription,
    PromoGroup,
    Subscription,
    User,
    UserPromoGroup,
)
from app.handlers import inline_gift as user_mod
from app.handlers.admin import inline_gift as admin_mod
from app.services import inline_gift_draft_service as draft_service
from tests.fixtures.sqlite_memory import ensure_real_aiosqlite


_TABLES = [
    InlineGiftSubscription.__table__,
    Subscription.__table__,
    User.__table__,
    PromoGroup.__table__,
    UserPromoGroup.__table__,
]


class _FakeCache:
    """Minimal Redis stand-in that JSON round-trips like CacheService."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        raw = self.store.get(key)
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, value, expire=None) -> bool:
        self.store[key] = json.dumps(value, default=str)
        return True

    async def delete(self, key: str) -> bool:
        return self.store.pop(key, None) is not None


@contextlib.asynccontextmanager
async def _db(monkeypatch):
    ensure_real_aiosqlite(monkeypatch)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=list(_TABLES)))
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(admin_mod, 'AsyncSessionLocal', maker)
    monkeypatch.setattr(user_mod, 'AsyncSessionLocal', maker)
    try:
        yield maker
    finally:
        await engine.dispose()


def _fake_message(telegram_id: int, username: str | None):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=telegram_id, username=username),
        answer=AsyncMock(),
    )


def _fake_inline_query(query_text: str, telegram_id: int = 999):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=telegram_id, username='admin'),
        query=query_text,
        answer=AsyncMock(),
    )


async def _get_gift(maker, gift_code: str):
    async with maker() as db:
        result = await db.execute(select(InlineGiftSubscription).where(InlineGiftSubscription.gift_code == gift_code))
        return result.scalars().first()


class TestInlineGiftDraftFallback:
    @pytest.mark.asyncio
    async def test_start_materialises_gift_from_draft_when_row_is_missing(self, monkeypatch):
        cache = _FakeCache()
        monkeypatch.setattr(draft_service, 'cache', cache)

        async with _db(monkeypatch) as maker:
            async with maker() as db:
                db.add(User(telegram_id=22222, username='recipient', balance_kopeks=0))
                await db.commit()

            gift_code = 'draft_only_code_1234567890abcdefghijklmnop'
            assert await draft_service.save_gift_draft(
                gift_code,
                recipient_telegram_id=22222,
                gift_type='subscription',
                days=30,
                traffic_limit_gb=None,
                device_limit=None,
                max_activations=1,
            )

            # The fallback must also make the bare-code probe in start.py see the gift.
            assert await user_mod.inline_gift_exists(gift_code) is True
            assert await _get_gift(maker, gift_code) is None

            message = _fake_message(22222, 'recipient')
            handled = await user_mod.handle_gift_deeplink(message, gift_code)

            assert handled is True
            assert message.answer.await_count == 1
            preview_markup = message.answer.await_args.kwargs['reply_markup']
            assert preview_markup.inline_keyboard[0][0].callback_data == f'igift_activate:{gift_code}'

            gift = await _get_gift(maker, gift_code)
            assert gift is not None
            assert gift.days == 30
            assert gift.recipient_telegram_id == 22222
            assert gift.max_activations == 1
            assert gift.inline_message_id is None
            # Draft is consumed once the row exists.
            assert await draft_service.load_gift_draft(gift_code) is None

    @pytest.mark.asyncio
    async def test_missing_code_still_falls_through(self, monkeypatch):
        cache = _FakeCache()
        monkeypatch.setattr(draft_service, 'cache', cache)

        async with _db(monkeypatch):
            message = _fake_message(22222, 'recipient')
            handled = await user_mod.handle_gift_deeplink(message, 'no_such_gift_code_1234567890abcdef')

        assert handled is False
        message.answer.assert_not_awaited()


class TestAdminInlineQueryCreatesDraft:
    @pytest.mark.asyncio
    async def test_query_answer_saves_draft_and_uses_bs_prefix(self, monkeypatch):
        cache = _FakeCache()
        monkeypatch.setattr(draft_service, 'cache', cache)
        monkeypatch.setattr(admin_mod, '_is_admin', lambda *args: True)
        monkeypatch.setattr(type(admin_mod.settings), 'get_bot_username', lambda self: 'test_bot')

        async with _db(monkeypatch) as maker:
            async with maker() as db:
                db.add(User(telegram_id=555, username='bill', balance_kopeks=0))
                await db.commit()

            query = _fake_inline_query('@bill 30')
            await admin_mod.handle_admin_inline_query(query)

            query.answer.assert_awaited_once()
            results = query.answer.await_args.args[0]
            button = results[0].reply_markup.inline_keyboard[0][0]
            assert button.url.startswith('https://t.me/test_bot?start=bs_')

            gift_code = button.url.split('bs_', 1)[1]
            # Draft exists, no DB row yet — exactly the broken-bot situation.
            assert await draft_service.load_gift_draft(gift_code) is not None
            assert await _get_gift(maker, gift_code) is None

            # Recipient opens /start bs_<code> and should get the preview.
            message = _fake_message(555, 'bill')
            handled = await user_mod.handle_gift_deeplink(message, gift_code)

            assert handled is True
            gift = await _get_gift(maker, gift_code)
            assert gift is not None
            assert gift.days == 30
            assert gift.recipient_telegram_id == 555
            assert gift.gift_type == 'subscription'

    @pytest.mark.asyncio
    async def test_multi_query_uses_bs_prefix_and_saves_draft(self, monkeypatch):
        cache = _FakeCache()
        monkeypatch.setattr(draft_service, 'cache', cache)
        monkeypatch.setattr(admin_mod, '_is_admin', lambda *args: True)
        monkeypatch.setattr(type(admin_mod.settings), 'get_bot_username', lambda self: 'test_bot')

        async with _db(monkeypatch):
            query = _fake_inline_query('-r 3 30')
            await admin_mod.handle_admin_inline_query(query)

            button = query.answer.await_args.args[0][0].reply_markup.inline_keyboard[0][0]
            assert button.url.startswith('https://t.me/test_bot?start=bs_')
            gift_code = button.url.split('bs_', 1)[1]
            draft = await draft_service.load_gift_draft(gift_code)
            assert draft is not None
            assert draft['max_activations'] == 3
            assert draft['recipient_telegram_id'] == 0


class TestChosenInlineResultDropsDraft:
    @pytest.mark.asyncio
    async def test_chosen_result_persists_row_and_drops_draft(self, monkeypatch):
        cache = _FakeCache()
        monkeypatch.setattr(draft_service, 'cache', cache)
        monkeypatch.setattr(admin_mod, '_is_admin', lambda *args: True)

        async with _db(monkeypatch) as maker:
            async with maker() as db:
                db.add(User(telegram_id=555, username='bill', balance_kopeks=0))
                await db.commit()

            gift_code = 'chosen_code_1234567890abcdefghijklmnop'
            await draft_service.save_gift_draft(
                gift_code,
                recipient_telegram_id=555,
                gift_type='subscription',
                days=30,
                max_activations=1,
            )
            chosen = ChosenInlineResult(
                result_id=gift_code,
                from_user=TgUser(id=999, is_bot=False, first_name='Admin'),
                query='@bill 30',
                inline_message_id='inline-msg-id',
            )

            await admin_mod.handle_chosen_inline_result(chosen)

            gift = await _get_gift(maker, gift_code)
            assert gift is not None
            assert gift.inline_message_id == 'inline-msg-id'
            assert await draft_service.load_gift_draft(gift_code) is None
