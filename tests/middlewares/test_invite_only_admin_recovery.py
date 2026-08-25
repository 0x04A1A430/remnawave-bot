from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_middleware_has_no_admin_exemption_from_the_block_check():
    """A BLOCKED account stays blocked in the bot even when it is in ADMIN_IDS.

    The invite-only admin recovery path exists so an operator cannot be locked out
    of a missing or soft-deleted account. A block is a deliberate administrative
    action, so exempting ADMIN_IDS here would make such accounts unblockable.
    """
    from app.middlewares import auth

    source = Path(auth.__file__).read_text(encoding='utf-8')

    assert 'if db_user.status == UserStatus.BLOCKED.value:' in source
    assert not hasattr(auth, '_is_blocked_non_admin')


@pytest.mark.asyncio
async def test_refresh_remnawave_description_uses_numeric_panel_id(monkeypatch):
    from app.middlewares import auth

    api = SimpleNamespace(update_user=AsyncMock())

    class _ApiContext:
        async def __aenter__(self):
            return api

        async def __aexit__(self, exc_type, exc, tb):
            return False

    service = SimpleNamespace(get_api_client=lambda: _ApiContext())
    monkeypatch.setattr(auth, 'RemnaWaveService', lambda: service)

    await auth._refresh_remnawave_description(4242, 'updated description', 99)

    api.update_user.assert_awaited_once_with(user_id=4242, description='updated description')
