"""Tests for `app.services.referral_service.attach_referrer_if_missing`.

Background — the bug this helper fixes
--------------------------------------
A new user clicks ``t.me/bot?start=ref_XYZ``, then immediately taps the
Telegram menu's "Open Cabinet" WebApp button. The cabinet's auth route
fires before the bot's /start handler finishes, so:

  1. cabinet creates the user row with ``referred_by_id=None``
     (pending_referral Redis key is not yet populated)
  2. /start handler runs LATER, sees ``db_user`` already exists, and
     used to skip the ``save_pending_referral`` call entirely
  3. Result: referrer is permanently dropped

The helper closes that race by exposing a single retroactive-attach
entry point used by every login path (bot /start, cabinet
initData / widget / OIDC). It must be:

  * **Idempotent** — calling it twice for the same user must not
    create duplicate ``referral_earning`` rows (the event fires only
    on the call that actually performs the attachment).
  * **Self-referral-safe** — checks ID, telegram_id, and email.
  * **Resilient** — a Redis or DB hiccup must not crash the caller.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.referral_service import attach_referrer_if_missing


def _user(
    *,
    user_id: int = 100,
    telegram_id: int | None = 555,
    referred_by_id: int | None = None,
    email: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        telegram_id=telegram_id,
        referred_by_id=referred_by_id,
        email=email,
    )


def _referrer(
    *,
    user_id: int = 200,
    telegram_id: int | None = 888,
    email: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        telegram_id=telegram_id,
        email=email,
    )


@pytest.fixture
def db() -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock(return_value=None)
    session.refresh = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)
    return session


# ---------------------------------------------------------------------------
# Idempotency — never double-attach, never double-fire the registration event.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_op_when_user_already_has_referrer(db: AsyncMock) -> None:
    user = _user(referred_by_id=999)

    with (
        patch('app.services.referral_service.get_pending_referral', AsyncMock(return_value=None)) as _gpr,
        patch('app.services.referral_service.process_referral_registration', AsyncMock()) as fire,
    ):
        result = await attach_referrer_if_missing(db, user, source='unit_test')

    assert result is None
    assert user.referred_by_id == 999, 'must not overwrite an existing referrer'
    db.commit.assert_not_called()
    fire.assert_not_called(), 'registration event must NOT fire when no attachment happens'


@pytest.mark.asyncio
async def test_no_op_when_no_pending_and_no_code(db: AsyncMock) -> None:
    user = _user()

    with (
        patch('app.services.referral_service.get_pending_referral', AsyncMock(return_value=None)),
        patch('app.services.referral_service.process_referral_registration', AsyncMock()) as fire,
    ):
        result = await attach_referrer_if_missing(db, user, source='unit_test')

    assert result is None
    assert user.referred_by_id is None
    fire.assert_not_called()


# ---------------------------------------------------------------------------
# Happy paths — explicit code, Redis fallback, both at once.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attaches_referrer_from_explicit_code(db: AsyncMock) -> None:
    user = _user()
    referrer = _referrer(user_id=200, telegram_id=888)

    with (
        patch('app.database.crud.user.get_user_by_referral_code', AsyncMock(return_value=referrer)),
        patch('app.services.referral_service.get_pending_referral', AsyncMock(return_value=None)) as gpr,
        patch('app.services.referral_service.clear_pending_referral', AsyncMock()),
        patch('app.services.referral_service.process_referral_registration', AsyncMock()) as fire,
    ):
        result = await attach_referrer_if_missing(db, user, referral_code='ABCD-EFGH', source='unit_test')

    assert result == 200
    assert user.referred_by_id == 200
    db.commit.assert_awaited_once()
    fire.assert_awaited_once(), 'registration event must fire exactly once on attach'
    gpr.assert_not_called(), 'Redis fallback should be skipped when explicit code resolves'


@pytest.mark.asyncio
async def test_attaches_referrer_from_redis_pending_when_no_code(db: AsyncMock) -> None:
    """REGRESSION: this is the exact race the user reported.

    Miniapp opened before /start finished → user row created with no
    referrer → /start later wrote pending_referral to Redis → on the
    NEXT cabinet request, the eager-attach helper picks it up.
    """
    user = _user()
    referrer = _referrer(user_id=200)

    with (
        patch(
            'app.services.referral_service.get_pending_referral',
            AsyncMock(return_value={'referrer_id': 200, 'referral_code': 'ABCD'}),
        ),
        patch('app.services.referral_service.get_user_by_id', AsyncMock(return_value=referrer)),
        patch('app.services.referral_service.clear_pending_referral', AsyncMock()) as clear,
        patch('app.services.referral_service.process_referral_registration', AsyncMock()) as fire,
    ):
        result = await attach_referrer_if_missing(db, user, source='unit_test')

    assert result == 200
    assert user.referred_by_id == 200
    db.commit.assert_awaited_once()
    fire.assert_awaited_once()
    clear.assert_awaited_once_with(555), 'pending_referral must be cleared after attach'


@pytest.mark.asyncio
async def test_explicit_code_takes_precedence_over_redis(db: AsyncMock) -> None:
    """Explicit URL/state-provided code wins over a stale Redis entry."""
    user = _user()
    code_referrer = _referrer(user_id=300, telegram_id=900)

    with (
        patch('app.database.crud.user.get_user_by_referral_code', AsyncMock(return_value=code_referrer)),
        patch(
            'app.services.referral_service.get_pending_referral',
            AsyncMock(return_value={'referrer_id': 999, 'referral_code': 'stale'}),
        ) as gpr,
        patch('app.services.referral_service.get_user_by_id', AsyncMock()) as gubi,
        patch('app.services.referral_service.clear_pending_referral', AsyncMock()),
        patch('app.services.referral_service.process_referral_registration', AsyncMock()),
    ):
        result = await attach_referrer_if_missing(db, user, referral_code='EXPLICIT-CODE', source='unit_test')

    assert result == 300, 'explicit code must take precedence over Redis pending'
    gpr.assert_not_called()
    gubi.assert_not_called()


# ---------------------------------------------------------------------------
# Self-referral guards — ID, telegram_id, email all must be checked.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_self_referral_by_id(db: AsyncMock) -> None:
    user = _user(user_id=100)
    self_referrer = _referrer(user_id=100, telegram_id=555)

    with (
        patch('app.database.crud.user.get_user_by_referral_code', AsyncMock(return_value=self_referrer)),
        patch('app.services.referral_service.process_referral_registration', AsyncMock()) as fire,
    ):
        result = await attach_referrer_if_missing(db, user, referral_code='X', source='unit_test')

    assert result is None
    assert user.referred_by_id is None
    fire.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_self_referral_by_telegram_id(db: AsyncMock) -> None:
    """Different DB user IDs but same Telegram account → still self-referral."""
    user = _user(user_id=100, telegram_id=555)
    self_referrer = _referrer(user_id=200, telegram_id=555)

    with (
        patch('app.database.crud.user.get_user_by_referral_code', AsyncMock(return_value=self_referrer)),
        patch('app.services.referral_service.process_referral_registration', AsyncMock()) as fire,
    ):
        result = await attach_referrer_if_missing(db, user, referral_code='X', source='unit_test')

    assert result is None
    assert user.referred_by_id is None
    fire.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_self_referral_by_email(db: AsyncMock) -> None:
    user = _user(email='Alice@Example.com')
    self_referrer = _referrer(user_id=200, email='alice@example.com')

    with (
        patch('app.database.crud.user.get_user_by_referral_code', AsyncMock(return_value=self_referrer)),
        patch('app.services.referral_service.process_referral_registration', AsyncMock()) as fire,
    ):
        result = await attach_referrer_if_missing(db, user, referral_code='X', source='unit_test')

    assert result is None
    assert user.referred_by_id is None
    fire.assert_not_called()


# ---------------------------------------------------------------------------
# Resilience — Redis / DB failures must not crash the caller.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_and_returns_none(db: AsyncMock) -> None:
    """If the DB commit fails, the helper rolls back and reports None.

    The caller continues normally; the user is not stuck in a
    half-attached state.
    """
    user = _user()
    referrer = _referrer(user_id=200)
    db.commit = AsyncMock(side_effect=RuntimeError('connection lost'))

    with (
        patch('app.database.crud.user.get_user_by_referral_code', AsyncMock(return_value=referrer)),
        patch('app.services.referral_service.process_referral_registration', AsyncMock()) as fire,
    ):
        result = await attach_referrer_if_missing(db, user, referral_code='X', source='unit_test')

    assert result is None
    db.rollback.assert_awaited()
    fire.assert_not_called(), 'event must not fire when the commit failed'


@pytest.mark.asyncio
async def test_registration_event_failure_still_keeps_attachment(db: AsyncMock) -> None:
    """If process_referral_registration raises, the referrer attachment survives.

    The attach is the load-bearing part; losing the notification/event
    is a softer failure than losing the referrer link itself.
    """
    user = _user()
    referrer = _referrer(user_id=200)

    with (
        patch('app.database.crud.user.get_user_by_referral_code', AsyncMock(return_value=referrer)),
        patch('app.services.referral_service.clear_pending_referral', AsyncMock()),
        patch(
            'app.services.referral_service.process_referral_registration',
            AsyncMock(side_effect=RuntimeError('notification service down')),
        ),
    ):
        result = await attach_referrer_if_missing(db, user, referral_code='X', source='unit_test')

    assert result == 200, 'attach must be reported as successful even if event firing failed'
    assert user.referred_by_id == 200


@pytest.mark.asyncio
async def test_user_without_telegram_id_skips_redis_fallback(db: AsyncMock) -> None:
    """Email-only user (no telegram_id) must not query Redis."""
    user = _user(telegram_id=None)

    with (
        patch('app.services.referral_service.get_pending_referral', AsyncMock()) as gpr,
        patch('app.services.referral_service.process_referral_registration', AsyncMock()),
    ):
        result = await attach_referrer_if_missing(db, user, source='unit_test')

    assert result is None
    gpr.assert_not_called(), 'Redis pending key is telegram_id-scoped; no point in querying without one'


@pytest.mark.asyncio
async def test_invalid_pending_referrer_id_type_is_handled(db: AsyncMock) -> None:
    """Malformed Redis payload (referrer_id is a string that can't int())
    must not crash — fall through to None."""
    user = _user()

    with (
        patch(
            'app.services.referral_service.get_pending_referral',
            AsyncMock(return_value={'referrer_id': 'not-an-int', 'referral_code': 'X'}),
        ),
        patch('app.services.referral_service.get_user_by_id', AsyncMock()) as gubi,
        patch('app.services.referral_service.process_referral_registration', AsyncMock()),
    ):
        result = await attach_referrer_if_missing(db, user, source='unit_test')

    assert result is None
    gubi.assert_not_called()
