"""Remnawave 2.8.0 merged the 4 per-interval expiration webhooks into a single
``user.expiration`` event carrying ``meta.expiration`` (signed hours). The bot
must handle the new event (or expiration notifications silently stop on 2.8.0),
while still accepting the old events from 2.7.x panels.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.services import remnawave_webhook_service
from app.services.remnawave_webhook_service import RemnaWaveWebhookService


def _service() -> RemnaWaveWebhookService:
    svc = RemnaWaveWebhookService(MagicMock())
    svc._notify_user = AsyncMock()
    svc._get_renew_keyboard = MagicMock(return_value=None)
    return svc


def _user() -> MagicMock:
    u = MagicMock()
    u.id = 1
    return u


def _sub() -> MagicMock:
    s = MagicMock()
    s.id = 42
    return s


def _sent_key(svc: RemnaWaveWebhookService) -> str:
    # _notify_user(user, text_key, *, ...)
    return svc._notify_user.await_args.args[1]


def _receiver_data(expiration) -> dict:
    """Build the handler ``data`` exactly as the webhook receiver does.

    The receiver injects the envelope ``meta`` under ``data['_meta']`` (see
    app/webserver/remnawave_webhook.py: "Inject meta into data so handlers can
    access it via data.get('_meta')"). Handlers read ``_meta`` — NOT ``meta``.
    Tests MUST go through this contract, otherwise they silently pass against
    the wrong key and mask a production break (every notification lost).
    """
    payload = {'event': 'user.expiration', 'meta': {'expiration': expiration}}
    data: dict = {}
    meta = payload.get('meta')
    if isinstance(meta, dict):
        data['_meta'] = meta
    return data


async def test_new_and_old_events_both_registered():
    svc = _service()
    # 2.8.0 event handled...
    assert svc._user_handlers.get('user.expiration') is not None
    # ...and 2.7.x events kept for backward compatibility.
    for old in (
        'user.expires_in_72_hours',
        'user.expires_in_48_hours',
        'user.expires_in_24_hours',
        'user.expired_24_hours_ago',
    ):
        assert old in svc._user_handlers


async def test_canonical_hours_map_to_legacy_messages():
    cases = {
        -72: 'WEBHOOK_SUB_EXPIRES_72H',
        -48: 'WEBHOOK_SUB_EXPIRES_48H',
        -24: 'WEBHOOK_SUB_EXPIRES_24H',
        24: 'WEBHOOK_SUB_EXPIRED_24H_AGO',
    }
    for hours, expected in cases.items():
        svc = _service()
        await svc._handle_user_expiration(None, _user(), _sub(), _receiver_data(hours))
        svc._notify_user.assert_awaited_once()
        assert _sent_key(svc) == expected


async def test_reads_receiver_meta_key_not_raw_meta():
    """Regression: the handler reads data['_meta'] (receiver contract). A payload
    carrying the RAW envelope key 'meta' (i.e. receiver injection skipped) must
    NOT produce a notification — this is exactly the bug that silently dropped
    every user.expiration notification on 2.8.0."""
    svc = _service()
    await svc._handle_user_expiration(None, _user(), _sub(), {'meta': {'expiration': -24}})
    svc._notify_user.assert_not_awaited()


async def test_non_canonical_negative_picks_nearest_before_message():
    svc = _service()
    # -30 is closest to -24 → "expires in <24h" message.
    await svc._handle_user_expiration(None, _user(), _sub(), _receiver_data(-30))
    assert _sent_key(svc) == 'WEBHOOK_SUB_EXPIRES_24H'


async def test_non_canonical_positive_uses_expired_message():
    svc = _service()
    await svc._handle_user_expiration(None, _user(), _sub(), _receiver_data(48))
    assert _sent_key(svc) == 'WEBHOOK_SUB_EXPIRED_24H_AGO'


async def test_missing_or_invalid_meta_sends_nothing():
    for data in ({'_meta': {}}, {'_meta': {'expiration': 'oops'}}, {}, {'_meta': None}):
        svc = _service()
        await svc._handle_user_expiration(None, _user(), _sub(), data)
        svc._notify_user.assert_not_awaited()


async def test_no_subscription_sends_nothing():
    svc = _service()
    await svc._handle_user_expiration(None, _user(), None, _receiver_data(-24))
    svc._notify_user.assert_not_awaited()


async def test_webhook_expiry_respects_user_days_threshold(monkeypatch):
    svc = RemnaWaveWebhookService(MagicMock())
    send_notification = AsyncMock(return_value=True)
    monkeypatch.setattr(remnawave_webhook_service.notification_delivery_service, 'send_notification', send_notification)
    monkeypatch.setattr(settings, 'WEBHOOK_NOTIFY_USER_ENABLED', True)
    monkeypatch.setattr(settings, 'WEBHOOK_NOTIFY_SUB_EXPIRING', True)
    monkeypatch.setattr(settings, 'ENABLE_NOTIFICATIONS', True)
    user = SimpleNamespace(
        id=1,
        language='ru',
        notification_settings={
            'subscription_expiry_enabled': True,
            'subscription_expiry_days': 1,
        },
    )

    await svc._notify_user(user, 'WEBHOOK_SUB_EXPIRES_72H')
    send_notification.assert_not_awaited()

    await svc._notify_user(user, 'WEBHOOK_SUB_EXPIRES_24H')
    send_notification.assert_awaited_once()


async def test_new_2_8_0_api_token_admin_events_registered():
    """2.8.0 added service.api_token_created/deleted — surfaced as admin notifications."""
    svc = _service()
    assert 'service.api_token_created' in svc._admin_handlers
    assert 'service.api_token_deleted' in svc._admin_handlers


async def test_user_modified_syncs_used_traffic_from_nested_user_traffic():
    """usedTrafficBytes lives nested in userTraffic (ExtendedUsersSchema); the
    user.modified handler must read it there, else used-traffic never syncs."""
    svc = _service()
    sub = MagicMock()
    sub.status = 'active'
    sub.traffic_used_gb = 0.0
    await svc._handle_user_modified(AsyncMock(), _user(), sub, {'userTraffic': {'usedTrafficBytes': 5 * 1024**3}})
    assert sub.traffic_used_gb == 5.0


async def test_user_modified_used_traffic_falls_back_to_flat_key():
    """Old panels send a flat usedTrafficBytes — keep the fallback working."""
    svc = _service()
    sub = MagicMock()
    sub.status = 'active'
    sub.traffic_used_gb = 0.0
    await svc._handle_user_modified(AsyncMock(), _user(), sub, {'usedTrafficBytes': 2 * 1024**3})
    assert sub.traffic_used_gb == 2.0


def _grace_overlay(expire_at: datetime) -> GracePanelOverlay:
    return GracePanelOverlay(
        status='ACTIVE',
        expire_at=expire_at,
        traffic_limit_bytes=11 * 1024**3,
        squad_uuids=('11111111-1111-1111-1111-111111111111',),
    )


async def _run_modified_with_overlay(
    monkeypatch,
    svc: RemnaWaveWebhookService,
    sub,
    data,
    overlay: GracePanelOverlay | None,
) -> None:
    """Run the handler with a stubbed open-grace-overlay lookup."""
    import app.services.remnawave_webhook_service as rw

    async def fake_overlay(db, subscription_id):
        assert subscription_id == sub.id
        return overlay

    monkeypatch.setattr(rw, 'get_open_grace_overlay', fake_overlay)
    await svc._handle_user_modified(AsyncMock(), _user(), sub, data)


async def test_user_modified_grace_echo_does_not_overwrite_billing_fields(monkeypatch):
    """A user.modified echo of the active grace overlay must NOT clobber the
    canonical billing status/end_date/traffic limit. Regression for the case
    where the grace overlay PATCH echoed status=ACTIVE + expireAt=grace_until and
    rewrote billing, causing reconciliation to complete the session as paid and
    revert the user to the old squad."""
    svc = _service()
    now = datetime.now(UTC)
    grace_until = now + timedelta(hours=21)
    sub = MagicMock()
    sub.id = 42
    sub.status = 'limited'
    sub.traffic_limit_gb = 5
    sub.traffic_used_gb = 4.5
    sub.end_date = now + timedelta(days=3650)
    sub.subscription_url = 'https://x'
    sub.subscription_crypto_link = 'crypto'
    sub.updated_at = now

    echo = {
        'status': 'ACTIVE',
        'expireAt': grace_until.isoformat(),
        'trafficLimitBytes': 11 * 1024**3,
        'activeInternalSquads': [{'uuid': '11111111-1111-1111-1111-111111111111'}],
        'usedTrafficBytes': 5 * 1024**3,
    }
    await _run_modified_with_overlay(monkeypatch, svc, sub, echo, _grace_overlay(grace_until))

    assert sub.status == 'limited'  # not reactivated by the overlay echo
    assert sub.traffic_limit_gb == 5  # not overwritten with the grace overlay limit
    assert abs((sub.end_date - (now + timedelta(days=3650))).total_seconds()) < 2  # untouched
    assert sub.traffic_used_gb == 5.0  # usage still synchronized


async def test_user_modified_real_renewal_still_syncs_without_grace_echo(monkeypatch):
    """A genuine panel renewal (different expireAt/limit) must still sync even
    while an unrelated grace overlay is open."""
    svc = _service()
    now = datetime.now(UTC)
    sub = MagicMock()
    sub.id = 42
    sub.status = 'limited'
    sub.traffic_limit_gb = 5
    sub.traffic_used_gb = 4.5
    sub.end_date = now + timedelta(days=1)
    sub.subscription_url = 'https://x'
    sub.subscription_crypto_link = 'crypto'
    sub.updated_at = now

    real_expiry = now + timedelta(days=30)
    renewal = {
        'status': 'ACTIVE',
        'expireAt': real_expiry.isoformat(),
        'trafficLimitBytes': 20 * 1024**3,
        'activeInternalSquads': [{'uuid': '22222222-2222-2222-2222-222222222222'}],
    }
    await _run_modified_with_overlay(monkeypatch, svc, sub, renewal, _grace_overlay(now + timedelta(hours=1)))

    assert sub.status == 'active'  # genuine renewal is honored
    assert abs((sub.end_date - real_expiry).total_seconds()) < 2
    assert sub.traffic_limit_gb == 20


async def test_user_modified_without_open_grace_syncs_normally(monkeypatch):
    """Without an open grace overlay the handler keeps its original behaviour."""
    svc = _service()
    now = datetime.now(UTC)
    sub = MagicMock()
    sub.id = 42
    sub.status = 'active'
    sub.traffic_limit_gb = 5
    sub.traffic_used_gb = 0.0
    sub.end_date = now + timedelta(days=10)
    sub.subscription_url = 'https://x'
    sub.subscription_crypto_link = 'crypto'
    sub.updated_at = now

    data = {'status': 'ACTIVE', 'expireAt': (now + timedelta(days=15)).isoformat()}
    await _run_modified_with_overlay(monkeypatch, svc, sub, data, None)

    assert sub.status == 'active'
    assert abs((sub.end_date - (now + timedelta(days=15))).total_seconds()) < 2


async def _run_expiration_with_grace(
    monkeypatch,
    svc: RemnaWaveWebhookService,
    sub,
    grace_active: bool,
) -> None:
    """Run an expiring-webhook handler with a stubbed grace-access lookup."""
    import app.services.remnawave_webhook_service as rw

    async def fake_overlay(db, subscription_id):
        assert subscription_id == sub.id
        return _grace_overlay(datetime.now(UTC) + timedelta(hours=1)) if grace_active else None

    monkeypatch.setattr(rw, 'get_open_grace_overlay', fake_overlay)


@pytest.mark.parametrize('handler_name', ['_handle_expires_in_72h', '_handle_expires_in_48h', '_handle_expires_in_24h'])
async def test_expiring_webhooks_suppressed_during_grace(monkeypatch, handler_name):
    """Panel 'subscription expiring' webhooks must be skipped while grace is open."""
    svc = _service()
    sub = _sub()
    await _run_expiration_with_grace(monkeypatch, svc, sub, grace_active=True)
    await getattr(svc, handler_name)(AsyncMock(), _user(), sub, {})
    svc._notify_user.assert_not_awaited()


@pytest.mark.parametrize('handler_name', ['_handle_expires_in_72h', '_handle_expires_in_48h', '_handle_expires_in_24h'])
async def test_expiring_webhooks_sent_without_grace(monkeypatch, handler_name):
    """Without grace the expiring webhooks still notify as before."""
    svc = _service()
    sub = _sub()
    await _run_expiration_with_grace(monkeypatch, svc, sub, grace_active=False)
    await getattr(svc, handler_name)(AsyncMock(), _user(), sub, {})
    svc._notify_user.assert_awaited_once()


async def test_user_expiration_suppressed_during_grace(monkeypatch):
    """The unified user.expiration event must be suppressed during grace."""
    svc = _service()
    sub = _sub()
    await _run_expiration_with_grace(monkeypatch, svc, sub, grace_active=True)
    await svc._handle_user_expiration(AsyncMock(), _user(), sub, _receiver_data(-24))
    svc._notify_user.assert_not_awaited()


async def test_user_expiration_sent_without_grace(monkeypatch):
    """Without grace the user.expiration event still notifies."""
    svc = _service()
    sub = _sub()
    await _run_expiration_with_grace(monkeypatch, svc, sub, grace_active=False)
    await svc._handle_user_expiration(AsyncMock(), _user(), sub, _receiver_data(-24))
    svc._notify_user.assert_awaited_once()
