"""Temporary merge token management for account linking.

Stores short-lived tokens in Redis so the user can confirm merging
two cabinet accounts (primary absorbs secondary) via a separate
confirmation endpoint.
"""

import secrets
from datetime import UTC, datetime
from typing import Any

import structlog

from app.utils.cache import cache, cache_key


logger = structlog.get_logger(__name__)

MERGE_TOKEN_TTL_SECONDS = 1800  # 30 minutes
MERGE_TOKEN_PREFIX = 'account_merge'


async def create_merge_token(
    primary_user_id: int,
    secondary_user_id: int,
    provider: str,
    provider_id: str,
) -> str:
    """Generate a merge token and store its payload in Redis.

    The token is a one-time confirmation handle: whoever presents it
    within ``MERGE_TOKEN_TTL_SECONDS`` can execute the account merge.

    Returns the raw token string (URL-safe base64, 32 bytes of entropy).
    Raises ``RuntimeError`` if Redis write fails.
    """
    token = secrets.token_urlsafe(32)
    value: dict[str, Any] = {
        'primary_user_id': primary_user_id,
        'secondary_user_id': secondary_user_id,
        'provider': provider,
        'provider_id': provider_id,
        'created_at': datetime.now(UTC).isoformat(),
    }
    key = cache_key(MERGE_TOKEN_PREFIX, token)
    stored = await cache.set(key, value, expire=MERGE_TOKEN_TTL_SECONDS)
    if not stored:
        logger.error(
            'Failed to store merge token in Redis',
            primary_user_id=primary_user_id,
            secondary_user_id=secondary_user_id,
            provider=provider,
        )
        raise RuntimeError('Failed to store merge token')

    logger.info(
        'Merge token created',
        primary_user_id=primary_user_id,
        secondary_user_id=secondary_user_id,
        provider=provider,
        provider_id=provider_id,
    )
    return token


async def get_merge_token_data(token: str) -> dict[str, Any] | None:
    """Read merge token payload *without* consuming it.

    Intended for preview / confirmation screens where the user sees
    what will happen before they press "Confirm".

    Returns ``None`` when the token is expired, missing, or malformed.
    """
    key = cache_key(MERGE_TOKEN_PREFIX, token)
    data: Any = await cache.get(key)
    if data is None or not isinstance(data, dict):
        return None
    return data


async def consume_merge_token(token: str) -> dict[str, Any] | None:
    """Atomically read and delete a merge token (GETDEL).

    This prevents double-merge race conditions: only the first caller
    that reaches Redis will get the payload; every subsequent attempt
    receives ``None``.

    Returns the stored dict or ``None`` if already consumed / expired.
    """
    key = cache_key(MERGE_TOKEN_PREFIX, token)
    data: Any = await cache.getdel(key)
    if data is None or not isinstance(data, dict):
        return None

    logger.info(
        'Merge token consumed',
        primary_user_id=data.get('primary_user_id'),
        secondary_user_id=data.get('secondary_user_id'),
        provider=data.get('provider'),
    )
    return data
