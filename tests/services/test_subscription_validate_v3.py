"""Regression: RemnaWave v3.0+ (REMNAWAVE_USE_USER_ID=true) has no `uuid` field —
users are identified by numeric id. validate_and_clean_subscription must not treat
a subscription with a valid short_uuid but no remnawave_uuid as garbage (that
would wipe short_uuid/subscription_url every validation).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services.subscription_service import SubscriptionService


def _fake_db():
    class FakeDB:
        committed = False

        async def commit(self):
            self.committed = True

        async def rollback(self):
            self.committed = False

    return FakeDB()


def _sub(remnawave_id=None, remnawave_uuid=None, short_uuid='duPmeL84Skv_bydv'):
    return SimpleNamespace(
        id=1,
        remnawave_id=remnawave_id,
        remnawave_uuid=remnawave_uuid,
        remnawave_short_uuid=short_uuid,
        subscription_url='https://sub.example/s',
        subscription_crypto_link='',
    )


def _user(telegram_id=7667943460, remnawave_id=None, remnawave_uuid=None):
    return SimpleNamespace(
        id=2,
        telegram_id=telegram_id,
        remnawave_uuid=remnawave_uuid,
        remnawave_id=remnawave_id,
    )


def _patch(monkeypatch, *, use_user_id: bool, multi_tariff: bool, panel_user=None, tg_panel_users=None):
    from app.config import settings
    from app.config import Settings

    monkeypatch.setattr(settings, 'REMNAWAVE_USE_USER_ID', use_user_id)
    monkeypatch.setattr(Settings, 'is_multi_tariff_enabled', lambda self: multi_tariff)

    svc = SubscriptionService.__new__(SubscriptionService)

    api = SimpleNamespace()

    async def _amock(*args, **kwargs):
        return panel_user

    async def _tgmock(*args, **kwargs):
        return list(tg_panel_users) if tg_panel_users else []

    api.get_user_by_uuid = _amock
    api.get_user_by_telegram_id = _tgmock

    @asynccontextmanager
    async def _client():
        yield api

    svc.get_api_client = _client
    return svc, api


def test_v3_short_uuid_without_uuid_is_not_cleaned(monkeypatch):
    """v3: short_uuid present, no remnawave_uuid, no panel id -> NOT garbage."""
    svc, api = _patch(monkeypatch, use_user_id=True, multi_tariff=False, panel_user=None)
    sub = _sub(remnawave_id=None, remnawave_uuid=None)
    user = _user()
    db = _fake_db()

    import asyncio

    result = asyncio.run(svc.validate_and_clean_subscription(db, sub, user))

    assert result is True
    assert sub.remnawave_short_uuid == 'duPmeL84Skv_bydv'  # preserved
    assert sub.subscription_url == 'https://sub.example/s'  # preserved
    assert db.committed is False


def test_v3_panel_id_validation_calls_panel(monkeypatch):
    """v3: with a numeric remnawave_id the panel is queried by id and kept."""
    panel_user = SimpleNamespace(telegram_id=7667943460)
    svc, api = _patch(
        monkeypatch,
        use_user_id=True,
        multi_tariff=False,
        panel_user=panel_user,
    )
    sub = _sub(remnawave_id=314, remnawave_uuid=None)
    user = _user(telegram_id=7667943460, remnawave_id=314)
    db = _fake_db()

    import asyncio

    result = asyncio.run(svc.validate_and_clean_subscription(db, sub, user))

    assert result is True
    assert sub.remnawave_short_uuid == 'duPmeL84Skv_bydv'
    assert db.committed is False


def test_v3_panel_id_telegram_mismatch_cleans(monkeypatch):
    """v3: panel user belongs to another telegram id -> subscription cleaned."""
    panel_user = SimpleNamespace(telegram_id=999)
    svc, api = _patch(
        monkeypatch,
        use_user_id=True,
        multi_tariff=False,
        panel_user=panel_user,
    )
    sub = _sub(remnawave_id=314, remnawave_uuid=None)
    user = _user(telegram_id=7667943460, remnawave_id=314)
    db = _fake_db()

    import asyncio

    result = asyncio.run(svc.validate_and_clean_subscription(db, sub, user))

    assert result is True
    assert sub.remnawave_short_uuid is None  # cleaned
    assert db.committed is True


def test_v2_short_uuid_without_uuid_still_cleaned(monkeypatch):
    """v2 (legacy): short_uuid without remnawave_uuid is still garbage."""
    svc, api = _patch(monkeypatch, use_user_id=False, multi_tariff=False, panel_user=None)
    sub = _sub(remnawave_id=None, remnawave_uuid=None)
    user = _user()
    db = _fake_db()

    import asyncio

    result = asyncio.run(svc.validate_and_clean_subscription(db, sub, user))

    assert result is True
    assert sub.remnawave_short_uuid is None  # legacy cleanup preserved
    assert db.committed is True


def test_v3_no_id_falls_back_to_telegram_id(monkeypatch):
    """v3: no remnawave_id in DB -> match panel user by telegram_id and backfill."""
    panel_user = SimpleNamespace(telegram_id=7667943460, id=314)
    svc, api = _patch(
        monkeypatch,
        use_user_id=True,
        multi_tariff=False,
        panel_user=None,
        tg_panel_users=[panel_user],
    )
    sub = _sub(remnawave_id=None, remnawave_uuid=None)
    user = _user(telegram_id=7667943460, remnawave_id=None)
    db = _fake_db()

    import asyncio

    result = asyncio.run(svc.validate_and_clean_subscription(db, sub, user))

    assert result is True
    assert user.remnawave_id == 314  # backfilled from panel
    assert sub.remnawave_short_uuid == 'duPmeL84Skv_bydv'  # preserved
    assert db.committed is False


def test_v3_no_id_no_panel_match_keeps_short_uuid(monkeypatch):
    """v3: no remnawave_id, telegram lookup empty -> NOT garbage, short_uuid kept."""
    svc, api = _patch(
        monkeypatch,
        use_user_id=True,
        multi_tariff=False,
        panel_user=None,
        tg_panel_users=[],
    )
    sub = _sub(remnawave_id=None, remnawave_uuid=None)
    user = _user(telegram_id=7667943460, remnawave_id=None)
    db = _fake_db()

    import asyncio

    result = asyncio.run(svc.validate_and_clean_subscription(db, sub, user))

    assert result is True
    assert sub.remnawave_short_uuid == 'duPmeL84Skv_bydv'  # preserved
    assert db.committed is False
