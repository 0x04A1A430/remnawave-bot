"""Пользовательские экраны не оставляют двойных пустых строк.

Тикеты и реферальные разделы собираются из блоков: шапка, сообщения, сводка,
футер. Блоки приходят из локалей и БД с собственными переводами строки, поэтому
склейка легко давала две-три пустые строки и хвост под последней строкой.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.handlers import referral, tickets
from app.utils.text_compact import compact_blank_lines


def test_compact_blank_lines_collapses_runs_and_trims_edges() -> None:
    assert compact_blank_lines('\n\nЗаголовок\n\n\n\nТекст\n\n') == 'Заголовок\nТекст'
    assert compact_blank_lines('А\n \n\nБ') == 'А\nБ'
    assert compact_blank_lines('   ') == ''


def _ticket_with_blank_runs() -> SimpleNamespace:
    created = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    return SimpleNamespace(
        id=17,
        user_id=1,
        title='Не подключается VPN',
        status='open',
        status_emoji='🟢',
        is_closed=False,
        created_at=created,
        messages=[
            SimpleNamespace(
                created_at=created,
                message_text='Первая строка\n\n\nВторая строка',
                is_user_message=True,
                has_media=False,
                media_type=None,
            )
        ],
    )


@pytest.mark.asyncio
async def test_ticket_view_has_no_double_blank_lines() -> None:
    callback = SimpleNamespace(data='view_ticket_17', message=SimpleNamespace(), answer=AsyncMock())
    db_user = SimpleNamespace(id=1, language='ru')

    with (
        patch.object(tickets.TicketCRUD, 'get_ticket_by_id', AsyncMock(return_value=_ticket_with_blank_runs())),
        patch.object(tickets, 'safe_edit_or_resend', AsyncMock()) as send_mock,
    ):
        await tickets.view_ticket(callback, db_user, AsyncMock())

    shown = send_mock.await_args.args[1]
    assert '\n\n\n' not in shown
    assert shown == shown.strip('\n')
    assert 'Первая строка\nВторая строка' in shown


@pytest.mark.asyncio
async def test_referral_analytics_has_no_double_blank_lines() -> None:
    captured: dict[str, str] = {}

    async def fake_edit(callback, text, keyboard):
        captured['text'] = text

    analytics = {
        'earnings_by_period': {'today': 0, 'week': 0, 'month': 0, 'quarter': 0},
        'top_referrals': [
            {
                'referral_name': 'Friend',
                'total_earned_kopeks': 10000,
                'total_earned_days': 0,
                'earnings_count': 1,
            }
        ],
    }
    callback = SimpleNamespace(answer=AsyncMock())
    db_user = SimpleNamespace(id=1, language='ru', referral_code='X')

    with (
        patch.object(referral, 'edit_or_answer_photo', fake_edit),
        patch.object(referral, 'get_referral_analytics', AsyncMock(return_value=analytics)),
    ):
        await referral.show_referral_analytics(callback, db_user, AsyncMock())

    text = captured['text']
    assert '\n\n\n' not in text
    assert text == text.strip('\n')
