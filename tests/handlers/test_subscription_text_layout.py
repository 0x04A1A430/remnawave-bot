"""Final subscription screens must not be padded with empty rows.

The cards are assembled from localized blocks; empty optional blocks used to
leave two or more blank lines (or a trailing one) in the rendered message.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.handlers.subscription import my_subscriptions
from app.handlers.subscription.my_subscriptions import show_my_subscriptions, show_subscription_detail


def _assert_compact(text: str) -> None:
    assert '\n\n\n' not in text
    assert not text.startswith('\n')
    assert not text.endswith('\n')


def _subscription(sub_id: int, name: str):
    return SimpleNamespace(
        id=sub_id,
        is_pending_trial=False,
        actual_status='active',
        tariff=SimpleNamespace(name=name),
        traffic_limit_gb=10,
        traffic_used_gb=1.0,
        device_limit=2,
        end_date=datetime.now(UTC) + timedelta(days=12),
        status_display='🟢 Активна',
    )


@pytest.mark.anyio('asyncio')
async def test_subscriptions_list_keeps_at_most_one_blank_line(monkeypatch: pytest.MonkeyPatch) -> None:
    subscriptions = [_subscription(1, 'Первый'), _subscription(2, 'Второй')]
    monkeypatch.setattr(Settings, 'is_multi_tariff_enabled', lambda _self: True)
    monkeypatch.setattr(
        my_subscriptions,
        'get_all_subscriptions_by_user_id',
        AsyncMock(return_value=subscriptions),
    )
    monkeypatch.setattr(
        my_subscriptions,
        '_build_subscriptions_keyboard',
        lambda _subscriptions, _language: SimpleNamespace(inline_keyboard=[]),
    )

    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    db_user = SimpleNamespace(id=7, language='ru')

    await show_my_subscriptions(callback, db_user, SimpleNamespace())

    text = callback.message.edit_text.await_args.args[0]
    _assert_compact(text)
    # Title + one block per subscription: exactly one empty row between blocks.
    assert text.count('\n\n') == len(subscriptions)
    assert 'Первый' in text and 'Второй' in text


@pytest.mark.anyio('asyncio')
async def test_subscription_detail_card_has_no_trailing_empty_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    subscription = _subscription(42, 'Тестовый')
    monkeypatch.setattr(
        my_subscriptions,
        'get_subscription_by_id_for_user',
        AsyncMock(return_value=subscription),
    )
    monkeypatch.setattr(my_subscriptions, 'get_display_subscription_link', lambda _sub: None)
    monkeypatch.setattr(
        my_subscriptions,
        '_build_subscription_detail_keyboard',
        lambda *_args, **_kwargs: SimpleNamespace(inline_keyboard=[]),
    )

    from app.database.crud import campaign

    monkeypatch.setattr(campaign, 'is_campaign_bonus_tariff_subscription', AsyncMock(return_value=False))

    callback = SimpleNamespace(
        data='sm:42',
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(update_data=AsyncMock())
    db_user = SimpleNamespace(id=7, language='ru')

    await show_subscription_detail(callback, db_user, SimpleNamespace(), state)

    text = callback.message.edit_text.await_args.args[0]
    _assert_compact(text)
    assert 'Тестовый' in text
