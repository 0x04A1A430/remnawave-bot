"""Tests for the RemnaWave node-webhook flood-control fix.

Covers commits 3756ad66 + 0461279e:
- TelegramRetryAfter retry loop in AdminNotificationService._send_message
- bot-token redaction helper
- node-event coalescing buffer + overflow accounting
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramRetryAfter

from app.services.admin_notification_service import (
    AdminNotificationService,
    NotificationCategory,
    _redact_telegram_secrets,
)
from app.services.remnawave_webhook_service import RemnaWaveWebhookService


# ---------------------------------------------------------------------------
# _redact_telegram_secrets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('raw', 'expected_contains'),
    [
        (
            'connect to https://api.telegram.org/bot8123456789:AAH-Sample_TokenString_With-Chars-aiogram01/sendMessage',
            'bot[REDACTED]/sendMessage',
        ),
        (
            'bare token leak 123456789:AAHabcdefABCDEF0123456789zZxY1234',
            'bot[REDACTED]',
        ),
        (
            'token trailing dash 123456789:AAHabcdefABCDEF0123456789zZxY1234-',
            'bot[REDACTED]',
        ),
        (
            'token trailing underscore 123456789:AAHabcdefABCDEF0123456789zZxY1234_',
            'bot[REDACTED]',
        ),
        ('no token here at all', 'no token here at all'),
    ],
)
def test_redact_telegram_secrets(raw: str, expected_contains: str) -> None:
    redacted = _redact_telegram_secrets(raw)
    assert expected_contains in redacted
    # Sanity: no token-shape leaks survived
    assert '123456789:AAH' not in redacted
    assert '8123456789:AAH' not in redacted


def test_redact_telegram_secrets_handles_multiple_tokens() -> None:
    text = 'first 123456789:AAHabcdefABCDEF0123456789zZxY1234 second bot987654321:XYZabcdefABCDEF0123456789zZxY9876 end'
    redacted = _redact_telegram_secrets(text)
    assert redacted.count('bot[REDACTED]') == 2
    assert 'AAH' not in redacted
    assert 'XYZ' not in redacted


# ---------------------------------------------------------------------------
# AdminNotificationService._send_message — flood-control retry
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_service(monkeypatch: pytest.MonkeyPatch) -> AdminNotificationService:
    bot = MagicMock()
    service = AdminNotificationService(bot)
    # Enable the service and pin chat_id so the message-send path is reached.
    service.chat_id = -100123456
    service.enabled = True
    return service


@pytest.mark.asyncio
async def test_send_message_retries_on_flood_control(
    admin_service: AdminNotificationService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RetryAfter on attempt 1, success on attempt 2 → exactly one sleep, returns True."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr('app.services.admin_notification_service.asyncio.sleep', fake_sleep)

    flood_error = TelegramRetryAfter(method=SimpleNamespace(), message='flood', retry_after=3)
    admin_service.bot.send_message = AsyncMock(side_effect=[flood_error, None])

    result = await admin_service._send_message('hello', category=NotificationCategory.INFRASTRUCTURE)

    assert result is True
    assert admin_service.bot.send_message.await_count == 2
    assert sleeps == [3]


@pytest.mark.asyncio
async def test_send_message_gives_up_after_three_flood_errors(
    admin_service: AdminNotificationService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three consecutive RetryAfter → two sleeps, third attempt returns False without sleeping."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr('app.services.admin_notification_service.asyncio.sleep', fake_sleep)

    flood = TelegramRetryAfter(method=SimpleNamespace(), message='flood', retry_after=2)
    admin_service.bot.send_message = AsyncMock(side_effect=[flood, flood, flood])

    result = await admin_service._send_message('hi', category=NotificationCategory.INFRASTRUCTURE)

    assert result is False
    assert admin_service.bot.send_message.await_count == 3
    assert sleeps == [2, 2]  # third attempt does NOT sleep before returning False


@pytest.mark.asyncio
async def test_send_message_caps_retry_after_at_30s(
    admin_service: AdminNotificationService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """retry_after=120 from Telegram must be clamped to 30s to avoid blocking the flush task."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr('app.services.admin_notification_service.asyncio.sleep', fake_sleep)

    flood = TelegramRetryAfter(method=SimpleNamespace(), message='flood', retry_after=120)
    admin_service.bot.send_message = AsyncMock(side_effect=[flood, None])

    result = await admin_service._send_message('hi', category=NotificationCategory.INFRASTRUCTURE)

    assert result is True
    assert sleeps == [30]


# ---------------------------------------------------------------------------
# RemnaWaveWebhookService node-event coalescing
# ---------------------------------------------------------------------------


@pytest.fixture
def webhook_service() -> RemnaWaveWebhookService:
    bot = MagicMock()
    service = RemnaWaveWebhookService(bot)
    # Enable admin notifications so the flush path actually sends.
    service._admin_service.chat_id = -100123456
    service._admin_service.enabled = True
    service._admin_service.bot.send_message = AsyncMock(return_value=None)
    return service


@pytest.mark.asyncio
async def test_node_event_coalescing_keeps_one_flush_task(
    webhook_service: RemnaWaveWebhookService,
) -> None:
    """7 concurrent enqueues land in one buffer with one scheduled flush task."""
    for i in range(7):
        await webhook_service._enqueue_node_event(
            'node.connection_lost', {'name': f'node-{i}', 'address': f'10.0.0.{i}'}
        )

    bucket = webhook_service._node_event_buffer['node.connection_lost']
    assert len(bucket) == 7
    assert webhook_service._node_event_flush_task is not None
    assert not webhook_service._node_event_flush_task.done()

    webhook_service._node_event_flush_task.cancel()
    try:
        await webhook_service._node_event_flush_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_node_event_buffer_overflow_counts_dropped_events(
    webhook_service: RemnaWaveWebhookService,
) -> None:
    """Past BUFFER_MAX, events are dropped but counted in overflow."""
    cap = webhook_service._NODE_EVENT_BUFFER_MAX
    for i in range(cap + 3):
        await webhook_service._enqueue_node_event(
            'node.connection_lost', {'name': f'node-{i}', 'address': f'10.0.0.{i}'}
        )

    assert len(webhook_service._node_event_buffer['node.connection_lost']) == cap
    assert webhook_service._node_event_overflow['node.connection_lost'] == 3

    if webhook_service._node_event_flush_task:
        webhook_service._node_event_flush_task.cancel()
        try:
            await webhook_service._node_event_flush_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_coalesced_summary_truncates_and_reports_overflow(
    webhook_service: RemnaWaveWebhookService,
) -> None:
    """50 unique nodes + 7 overflow → 40 lines + 'truncated' line + 'отброшено' line."""
    max_lines = webhook_service._NODE_EVENT_SUMMARY_MAX_LINES
    payloads = [{'name': f'node-{i}', 'address': f'10.0.0.{i}'} for i in range(50)]

    sent_text: dict[str, str] = {}

    async def capture_send(text: str) -> bool:
        sent_text['value'] = text
        return True

    webhook_service._admin_service.send_webhook_notification = AsyncMock(side_effect=capture_send)

    await webhook_service._send_coalesced_node_notification(
        'node.connection_lost', payloads, overflow_count=7
    )

    text = sent_text['value']
    bullet_lines = [line for line in text.split('\n') if line.startswith('•')]
    # 40 node lines + 1 "ещё N нод(ы) (truncated)" + 1 "событий отброшено (buffer overflow)"
    assert len(bullet_lines) == max_lines + 2
    assert '(truncated)' in text
    assert 'buffer overflow' in text
    # Header reports total = unique + overflow = 50 + 7 = 57
    assert '× 57' in text


@pytest.mark.asyncio
async def test_coalesced_summary_single_event_omits_count_suffix(
    webhook_service: RemnaWaveWebhookService,
) -> None:
    """One event → header without '× N' suffix."""
    sent_text: dict[str, str] = {}

    async def capture_send(text: str) -> bool:
        sent_text['value'] = text
        return True

    webhook_service._admin_service.send_webhook_notification = AsyncMock(side_effect=capture_send)

    await webhook_service._send_coalesced_node_notification(
        'node.connection_restored', [{'name': 'lone-node', 'address': '10.0.0.1'}]
    )

    assert '×' not in sent_text['value']
    assert 'lone-node' in sent_text['value']


@pytest.mark.asyncio
async def test_coalesced_summary_dedupes_by_name_and_address(
    webhook_service: RemnaWaveWebhookService,
) -> None:
    """Same (name, address) repeated 5 times → 1 line, header shows × 5 total."""
    payloads = [{'name': 'spammy', 'address': '10.0.0.1'} for _ in range(5)]

    sent_text: dict[str, str] = {}

    async def capture_send(text: str) -> bool:
        sent_text['value'] = text
        return True

    webhook_service._admin_service.send_webhook_notification = AsyncMock(side_effect=capture_send)

    await webhook_service._send_coalesced_node_notification('node.connection_lost', payloads)

    bullet_lines = [line for line in sent_text['value'].split('\n') if line.startswith('•')]
    assert len(bullet_lines) == 1
    assert 'spammy' in sent_text['value']
