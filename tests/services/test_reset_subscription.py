"""Feature (#2): admin "reset subscription" — fully zero out a subscription
("as if the user never spammed it") WITHOUT deleting the user from the bot DB,
so support tickets survive. The RemnaWave panel user is DISABLED (not deleted).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import app.services.subscription_service as ss
from app.cabinet.routes import admin_users
from app.cabinet.schemas.users import ResetSubscriptionRequest
from app.config import Settings
from app.database.crud.subscription import reset_subscription
from app.database.models import SubscriptionStatus


def _set_multi_tariff(monkeypatch, enabled: bool):
    # is_multi_tariff_enabled is a method on the pydantic Settings class.
    monkeypatch.setattr(Settings, 'is_multi_tariff_enabled', lambda self: enabled)


def _sub(**kw) -> SimpleNamespace:
    base = dict(
        id=7,
        user_id=1,
        status=SubscriptionStatus.ACTIVE.value,
        end_date=datetime.now(UTC) + timedelta(days=1000),  # spammed far into the future
        connected_squads=['sq1', 'sq2'],
        traffic_used_gb=42.5,
        autopay_enabled=True,
        remnawave_uuid='SUB_UUID',
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_reset_subscription_zeroes_fields():
    sub = _sub()
    before = datetime.now(UTC)

    await reset_subscription(AsyncMock(), sub, commit=False)

    assert sub.status == SubscriptionStatus.DISABLED.value
    # spammed days gone: end_date snapped to ~now
    assert before <= sub.end_date <= datetime.now(UTC)
    assert sub.connected_squads == []
    assert sub.traffic_used_gb == 0.0
    assert sub.autopay_enabled is False


async def test_reset_with_panel_disables_subscription_uuid(monkeypatch):
    disabled: list[str] = []

    async def fake_disable(self, uuid):
        disabled.append(uuid)
        return True

    monkeypatch.setattr(ss.SubscriptionService, 'disable_remnawave_user', fake_disable)
    _set_multi_tariff(monkeypatch, True)

    sub = _sub(remnawave_uuid='SUB_UUID')
    user = SimpleNamespace(id=1, remnawave_uuid='USER_UUID')

    result = await ss.reset_subscription_with_panel(AsyncMock(), user, sub)

    assert disabled == ['SUB_UUID']  # per-subscription uuid (never the user-level one)
    assert result['panel_disabled'] is True
    assert sub.status == SubscriptionStatus.DISABLED.value  # DB reset applied too


async def test_reset_with_panel_multitariff_no_sub_uuid_skips_panel(monkeypatch):
    """Multi-tariff + no per-sub uuid → must NOT fall back to user.remnawave_uuid
    (that legacy uuid could belong to a different active sub). Panel is skipped."""
    disabled: list[str] = []

    async def fake_disable(self, uuid):
        disabled.append(uuid)
        return True

    monkeypatch.setattr(ss.SubscriptionService, 'disable_remnawave_user', fake_disable)
    _set_multi_tariff(monkeypatch, True)

    sub = _sub(remnawave_uuid=None)
    user = SimpleNamespace(id=1, remnawave_uuid='USER_UUID')

    result = await ss.reset_subscription_with_panel(AsyncMock(), user, sub)

    assert disabled == []  # no fallback in multi-tariff
    assert result['panel_disabled'] is False
    assert sub.status == SubscriptionStatus.DISABLED.value  # bot-side reset still applied


async def test_reset_with_panel_singletariff_falls_back_to_user_uuid(monkeypatch):
    disabled: list[str] = []

    async def fake_disable(self, uuid):
        disabled.append(uuid)
        return True

    monkeypatch.setattr(ss.SubscriptionService, 'disable_remnawave_user', fake_disable)
    _set_multi_tariff(monkeypatch, False)

    sub = _sub(remnawave_uuid=None)
    user = SimpleNamespace(id=1, remnawave_uuid='USER_UUID')

    await ss.reset_subscription_with_panel(AsyncMock(), user, sub)

    assert disabled == ['USER_UUID']  # legacy single-tariff fallback is correct here


async def test_reset_with_panel_no_uuid_skips_panel(monkeypatch):
    called: list[str] = []

    async def fake_disable(self, uuid):
        called.append(uuid)
        return True

    monkeypatch.setattr(ss.SubscriptionService, 'disable_remnawave_user', fake_disable)

    sub = _sub(remnawave_uuid=None)
    user = SimpleNamespace(id=1, remnawave_uuid=None)

    result = await ss.reset_subscription_with_panel(AsyncMock(), user, sub)

    assert called == []  # nothing to disable
    assert result['panel_disabled'] is False
    assert sub.status == SubscriptionStatus.DISABLED.value  # bot-side reset still applied


async def test_reset_with_panel_survives_panel_error(monkeypatch):
    """A panel disable failure must not block the bot-side reset (best effort)."""

    async def boom(self, uuid):
        raise RuntimeError('panel down')

    monkeypatch.setattr(ss.SubscriptionService, 'disable_remnawave_user', boom)

    sub = _sub(remnawave_uuid='SUB_UUID')
    user = SimpleNamespace(id=1, remnawave_uuid=None)

    result = await ss.reset_subscription_with_panel(AsyncMock(), user, sub)

    assert result['panel_disabled'] is False
    assert sub.status == SubscriptionStatus.DISABLED.value


async def test_user_modified_does_not_resurrect_disabled_end_date():
    """A user.modified webhook carrying a stale FUTURE expireAt must NOT restore the
    end_date of a subscription the bot deliberately DISABLED (e.g. after reset) —
    otherwise the spammed days would silently come back."""
    import app.services.remnawave_webhook_service as rw

    svc = rw.RemnaWaveWebhookService(MagicMock())
    svc._stamp_webhook_update = MagicMock()

    now = datetime.now(UTC)
    reset_end = now
    sub = SimpleNamespace(
        id=7,
        status=SubscriptionStatus.DISABLED.value,
        end_date=reset_end,
        traffic_limit_gb=0,
        traffic_used_gb=0.0,
        connected_squads=[],
        subscription_url='https://x',
        subscription_crypto_link='crypto',
        updated_at=now,
    )
    user = SimpleNamespace(id=1, telegram_id=123)
    data = {'expireAt': (now + timedelta(days=900)).isoformat(), 'status': 'DISABLED'}

    await svc._handle_user_modified(AsyncMock(), user, sub, data)

    assert sub.end_date == reset_end  # NOT pushed back to +900 days
    assert sub.status == SubscriptionStatus.DISABLED.value


async def test_user_modified_still_syncs_end_date_for_active():
    """Regression guard: ACTIVE subs still get end_date synced from the panel."""
    import app.services.remnawave_webhook_service as rw

    svc = rw.RemnaWaveWebhookService(MagicMock())
    svc._stamp_webhook_update = MagicMock()

    now = datetime.now(UTC)
    sub = SimpleNamespace(
        id=7,
        status=SubscriptionStatus.ACTIVE.value,
        end_date=now + timedelta(days=10),
        traffic_limit_gb=0,
        traffic_used_gb=0.0,
        connected_squads=[],
        subscription_url='https://x',
        subscription_crypto_link='crypto',
        updated_at=now,
    )
    user = SimpleNamespace(id=1, telegram_id=123)
    new_expiry = now + timedelta(days=30)
    data = {'expireAt': new_expiry.isoformat(), 'status': 'ACTIVE'}

    await svc._handle_user_modified(AsyncMock(), user, sub, data)

    assert abs((sub.end_date - new_expiry).total_seconds()) < 2  # synced from panel


async def test_user_level_reset_deletes_all_three_current_subscriptions(monkeypatch):
    """The user reset remains one user-scoped operation, not a selected-sub reset."""
    subscriptions = [_sub(id=sub_id, remnawave_uuid=f'SUB-{sub_id}') for sub_id in (7, 8, 9)]
    user = SimpleNamespace(id=1, subscriptions=subscriptions, updated_at=None, remnawave_uuid=None)
    db = AsyncMock()
    grace_checks: list[tuple[int, ...]] = []
    cancelled: list[int] = []

    monkeypatch.setattr(admin_users, 'get_user_by_id', AsyncMock(return_value=user))

    async def ensure_no_open_grace(_db, subscription_ids):
        grace_checks.append(subscription_ids)

    async def cancel_recurrent(_db, subscription_id):
        cancelled.append(subscription_id)

    monkeypatch.setattr(
        'app.services.grace_access_runtime.ensure_no_open_grace_for_subscriptions', ensure_no_open_grace
    )
    monkeypatch.setattr('app.services.payment.platega.cancel_platega_recurring_for_subscription_safe', cancel_recurrent)

    result = await admin_users.reset_user_subscription(
        1,
        ResetSubscriptionRequest(deactivate_in_panel=False),
        admin=SimpleNamespace(id=99),
        db=db,
    )

    assert result.subscription_deleted is True
    assert grace_checks == [(7, 8, 9), (7, 8, 9)]
    assert cancelled == [7, 8, 9]
    assert db.execute.await_count == 4  # three server cleanups plus one user-scoped subscription deletion
    delete_statement = db.execute.await_args_list[-1].args[0]
    compiled_delete = delete_statement.compile()
    assert 'DELETE FROM subscriptions' in str(delete_statement)
    assert 'subscriptions.user_id' in str(delete_statement)
    assert 1 in compiled_delete.params.values()
    assert db.commit.await_count == 1
