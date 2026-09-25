"""Inline gift feedback tests.

Covers the chain that keeps the inline message button in sync:
- handle_chosen_inline_result stores the real inline_message_id and the
  intended-recipient sentinel separately (no sentinel overwrite).
- _check_recipient gates by intended_recipient.
- _update_inline_button edits by inline_message_id and skips safely.
"""

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import ChosenInlineResult, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Base, InlineGiftSubscription, User
from app.handlers.admin import inline_gift as admin_mod
from app.handlers.inline_gift import _check_recipient, _update_inline_button
from tests.fixtures.sqlite_memory import ensure_real_aiosqlite

_TABLES = [InlineGiftSubscription.__table__, User.__table__]

_INLINE_MSG_ID = 'BQAAI-fAKEtestinline1234'


def _make_chosen(result_id: str, query: str, inline_message_id: str | None) -> ChosenInlineResult:
    return ChosenInlineResult(
        result_id=result_id,
        from_user=TgUser(id=999, is_bot=False, first_name='Admin'),
        query=query,
        inline_message_id=inline_message_id,
    )


@contextlib.asynccontextmanager
async def db_maker_with_seed(monkeypatch):
    """In-memory sessionmaker patched into the admin inline_gift module."""
    ensure_real_aiosqlite(monkeypatch)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=list(_TABLES)))
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(admin_mod, 'AsyncSessionLocal', maker)
    monkeypatch.setattr(admin_mod, '_is_admin', lambda *args: True)
    try:
        yield maker
    finally:
        await engine.dispose()


async def _run_chosen(db_maker, chosen: ChosenInlineResult) -> None:
    await admin_mod.handle_chosen_inline_result(chosen)


async def _get_gift(db, gift_code: str):
    result = await db.execute(select(InlineGiftSubscription).where(InlineGiftSubscription.gift_code == gift_code))
    return result.scalars().first()


class TestChosenInlineResultStorage:
    @pytest.mark.asyncio
    async def test_resolved_recipient_keeps_real_inline_message_id(self, monkeypatch):
        async with db_maker_with_seed(monkeypatch) as maker:
            async with maker() as db:
                db.add(User(telegram_id=555, username='bill', balance_kopeks=0))
                await db.commit()
            await admin_mod.handle_chosen_inline_result(
                _make_chosen('code_resolved', '@bill 30', _INLINE_MSG_ID)
            )
            async with maker() as db:
                gift = await _get_gift(db, 'code_resolved')
        assert gift is not None
        assert gift.inline_message_id == _INLINE_MSG_ID
        assert gift.intended_recipient is None
        assert gift.recipient_telegram_id == 555

    @pytest.mark.asyncio
    async def test_unresolved_username_keeps_real_inline_message_id(self, monkeypatch):
        async with db_maker_with_seed(monkeypatch) as maker:
            await admin_mod.handle_chosen_inline_result(
                _make_chosen('code_unresolved_user', '@ghost 30', _INLINE_MSG_ID)
            )
            async with maker() as db:
                gift = await _get_gift(db, 'code_unresolved_user')
        assert gift is not None
        assert gift.inline_message_id == _INLINE_MSG_ID
        assert gift.intended_recipient == 'u:ghost'
        assert gift.recipient_telegram_id == 0

    @pytest.mark.asyncio
    async def test_unresolved_target_id_keeps_real_inline_message_id(self, monkeypatch):
        async with db_maker_with_seed(monkeypatch) as maker:
            await admin_mod.handle_chosen_inline_result(
                _make_chosen('code_unresolved_tid', '777 30', _INLINE_MSG_ID)
            )
            async with maker() as db:
                gift = await _get_gift(db, 'code_unresolved_tid')
        assert gift is not None
        assert gift.inline_message_id == _INLINE_MSG_ID
        assert gift.intended_recipient == 'tid:777'
        assert gift.recipient_telegram_id == 777

    @pytest.mark.asyncio
    async def test_missing_inline_message_id_is_stored_as_none(self, monkeypatch):
        async with db_maker_with_seed(monkeypatch) as maker:
            await admin_mod.handle_chosen_inline_result(
                _make_chosen('code_no_feedback', '@bill 30', None)
            )
            async with maker() as db:
                gift = await _get_gift(db, 'code_no_feedback')
        assert gift is not None
        assert gift.inline_message_id is None
        assert gift.intended_recipient is None

    @pytest.mark.asyncio
    async def test_multi_activation_keeps_real_inline_message_id(self, monkeypatch):
        async with db_maker_with_seed(monkeypatch) as maker:
            await admin_mod.handle_chosen_inline_result(
                _make_chosen('code_multi', '-r 5 30', _INLINE_MSG_ID)
            )
            async with maker() as db:
                gift = await _get_gift(db, 'code_multi')
        assert gift is not None
        assert gift.recipient_telegram_id == 0
        assert gift.max_activations == 5
        assert gift.inline_message_id == _INLINE_MSG_ID
        assert gift.intended_recipient is None

    @pytest.mark.asyncio
    async def test_hint_result_ids_are_ignored(self, monkeypatch):
        async with db_maker_with_seed(monkeypatch) as maker:
            await admin_mod.handle_chosen_inline_result(_make_chosen('hint_sub', '', _INLINE_MSG_ID))
            async with maker() as db:
                gift = await _get_gift(db, 'hint_sub')
        assert gift is None


class TestCheckRecipient:
    def _gift(self, *, recipient_telegram_id: int = 0, intended_recipient: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            recipient_telegram_id=recipient_telegram_id,
            intended_recipient=intended_recipient,
            inline_message_id='BQAAI-fAKERealMessage',
            gift_code='code_x',
        )

    def test_intended_username_gates_by_username(self):
        gift = self._gift(intended_recipient='u:alice')
        assert _check_recipient(gift, 123, 'alice')
        assert not _check_recipient(gift, 123, 'bob')

    def test_intended_tid_gates_by_id(self):
        gift = self._gift(intended_recipient='tid:777')
        assert _check_recipient(gift, 777, 'anyone')
        assert not _check_recipient(gift, 555, 'anyone')

    def test_open_gift_accepts_any_recipient(self):
        gift = self._gift()
        assert _check_recipient(gift, 123, 'alice')
        assert _check_recipient(gift, 777, 'bob')

    def test_resolved_recipient_gates_by_id(self):
        gift = self._gift(recipient_telegram_id=42)
        assert _check_recipient(gift, 42, 'anyone')
        assert not _check_recipient(gift, 43, 'anyone')


class TestUpdateInlineButton:
    def test_missing_inline_message_id_skips_edit(self):
        bot = AsyncMock()
        _update_inline_button(bot, None, 'Активировано', 0, 1, gift_code='c1')
        bot.edit_message_reply_markup.assert_not_called()

    def test_legacy_sentinel_skips_edit(self):
        bot = AsyncMock()
        _update_inline_button(bot, 'u:alice', 'Активировано', 0, 1, gift_code='c1')
        bot.edit_message_reply_markup.assert_not_called()

    def test_fully_used_sets_noop_callback_button(self):
        bot = AsyncMock()
        _update_inline_button(bot, _INLINE_MSG_ID, 'Активировано', 0, 2, gift_code='c2')
        bot.edit_message_reply_markup.assert_awaited_once()
        kwargs = bot.edit_message_reply_markup.await_args.kwargs
        assert kwargs['inline_message_id'] == _INLINE_MSG_ID
        button = kwargs['reply_markup'].inline_keyboard[0][0]
        assert button.callback_data == 'igift_noop'

    def test_remaining_shows_deep_link_button(self):
        bot = AsyncMock()
        _update_inline_button(bot, _INLINE_MSG_ID, 'Активировать (осталось: 1)', 1, 2, gift_code='c3')
        bot.edit_message_reply_markup.assert_awaited_once()
        kwargs = bot.edit_message_reply_markup.await_args.kwargs
        button = kwargs['reply_markup'].inline_keyboard[0][0]
        assert button.url is not None
        assert button.url.endswith('start=c3')

    def test_edit_failure_is_swallowed(self):
        bot = AsyncMock()
        bot.edit_message_reply_markup.side_effect = RuntimeError('boom')
        _update_inline_button(bot, _INLINE_MSG_ID, 'Активировано', 0, 1, gift_code='c4')
        bot.edit_message_reply_markup.assert_awaited_once()
