"""Security: email-link merge must require ownership of the existing account.

Account-takeover regression guard. The attack: an authenticated user (their own
Telegram account) calls POST /cabinet/auth/email/register with a VICTIM's email.
Pre-fix, the conflict branch handed back a merge token with no proof the caller
controlled the victim's account, and the unauthenticated POST /cabinet/auth/merge
then absorbed the victim's subscription/balance/email into the attacker.

The fix requires the EXISTING account's password before a merge token is minted —
mirroring how the OAuth/Telegram link flows only merge after proving control of
the other identity. These tests pin that: no victim password -> no token.
"""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from app.cabinet.auth.password_utils import hash_password
from app.cabinet.routes.auth import register_email
from app.cabinet.schemas.auth import EmailRegisterRequest
from app.database.models import UserStatus


VICTIM_PASSWORD = 'victim-secret-pw'


def _db_returning(existing: object) -> AsyncMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock(return_value=None)
    return db


def _attacker() -> SimpleNamespace:
    # No verified email of their own, so the early guard lets them through.
    return SimpleNamespace(id=1, email=None, email_verified=False, language='en')


def _victim(*, password_hash: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=2,
        email='victim@example.com',
        email_verified=True,
        status=UserStatus.ACTIVE.value,
        password_hash=password_hash if password_hash is not None else hash_password(VICTIM_PASSWORD),
    )


def _io_patches(merge_token_mock: AsyncMock) -> list:
    return [
        patch('app.cabinet.routes.auth.get_client_ip', return_value='1.2.3.4'),
        patch(
            'app.cabinet.routes.auth.RateLimitCache.is_ip_rate_limited',
            AsyncMock(return_value=False),
        ),
        patch('app.cabinet.routes.auth.disposable_email_service.is_disposable', return_value=False),
        patch('app.cabinet.routes.auth.create_merge_token', merge_token_mock),
    ]


async def _call(req: EmailRegisterRequest, db: AsyncMock, merge_token_mock: AsyncMock):
    with ExitStack() as stack:
        for p in _io_patches(merge_token_mock):
            stack.enter_context(p)
        return await register_email(request=req, raw_request=MagicMock(), user=_attacker(), db=db)


@pytest.mark.asyncio
async def test_merge_denied_without_victim_password() -> None:
    """ATO: knowing only the victim's email must NOT yield a merge token."""
    db = _db_returning(_victim())
    merge_token_mock = AsyncMock(return_value='SHOULD-NOT-BE-ISSUED')
    req = EmailRegisterRequest(email='victim@example.com', password='attacker-guess-pw')

    with pytest.raises(HTTPException) as exc:
        await _call(req, db, merge_token_mock)

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    merge_token_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_denied_when_existing_account_has_no_password() -> None:
    """Fail closed: an OAuth-only account (no password) can't be merged via email."""
    db = _db_returning(_victim(password_hash=None))
    merge_token_mock = AsyncMock(return_value='SHOULD-NOT-BE-ISSUED')
    req = EmailRegisterRequest(email='victim@example.com', password='any-password-here')

    with pytest.raises(HTTPException) as exc:
        await _call(req, db, merge_token_mock)

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    merge_token_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_offered_with_correct_existing_password() -> None:
    """Legit: the real owner (knows the existing account's password) still merges."""
    db = _db_returning(_victim())
    merge_token_mock = AsyncMock(return_value='merge-tok-123')
    req = EmailRegisterRequest(email='victim@example.com', password=VICTIM_PASSWORD)

    result = await _call(req, db, merge_token_mock)

    assert result['merge_required'] is True
    assert result['merge_token'] == 'merge-tok-123'
    merge_token_mock.assert_awaited_once()
