"""CRUD for `user_device_aliases` — local nicknames for HWID devices.

Aliases live ONLY in our DB. They are never pushed to RemnaWave panel.
Scope is per-(user, hwid) so the same physical device shares the alias
across all of a user's subscriptions in multi-tariff mode.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import UserDeviceAlias


logger = structlog.get_logger(__name__)


# Hard cap matches the model column length. Enforced again at the API/handler
# boundary so we fail with a friendly message before hitting the DB.
ALIAS_MAX_LENGTH: int = 64


def normalize_alias(value: str | None) -> str:
    """Strip + collapse whitespace + cap length. Returns '' for empty/None input."""
    if value is None:
        return ''
    # Collapse all whitespace runs to a single space — pasted line breaks etc.
    collapsed = ' '.join(value.split())
    return collapsed[:ALIAS_MAX_LENGTH]


async def get_aliases_for_user(db: AsyncSession, user_id: int) -> dict[str, str]:
    """Return all device aliases for a user as a {hwid: alias} dict."""
    result = await db.execute(
        select(UserDeviceAlias.hwid, UserDeviceAlias.alias).where(UserDeviceAlias.user_id == user_id)
    )
    return {row.hwid: row.alias for row in result.all()}


async def get_alias(db: AsyncSession, user_id: int, hwid: str) -> str | None:
    """Return a single alias or None when not set."""
    result = await db.execute(
        select(UserDeviceAlias.alias).where(
            UserDeviceAlias.user_id == user_id,
            UserDeviceAlias.hwid == hwid,
        )
    )
    return result.scalar_one_or_none()


async def upsert_alias(db: AsyncSession, user_id: int, hwid: str, alias: str) -> str:
    """Set/update alias. Empty string is treated as 'delete' to keep the row
    set minimal — callers that want to clear an alias just pass ''.

    Returns the normalized alias actually stored (or '' when cleared).
    """
    normalized = normalize_alias(alias)
    if not normalized:
        await delete_alias(db, user_id, hwid)
        return ''

    if not hwid:
        raise ValueError('hwid is required')

    stmt = (
        pg_insert(UserDeviceAlias)
        .values(user_id=user_id, hwid=hwid, alias=normalized)
        .on_conflict_do_update(
            index_elements=['user_id', 'hwid'],
            set_={'alias': normalized},
        )
    )
    await db.execute(stmt)
    await db.commit()
    logger.info(
        'Device alias upserted', user_id=user_id, hwid_prefix=hwid[:8], alias_length=len(normalized)
    )
    return normalized


async def delete_alias(db: AsyncSession, user_id: int, hwid: str) -> bool:
    """Remove the alias for a (user, hwid) pair. Returns True if something was deleted."""
    result = await db.execute(
        select(UserDeviceAlias).where(
            UserDeviceAlias.user_id == user_id,
            UserDeviceAlias.hwid == hwid,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


def attach_aliases_to_devices(devices: list[dict], aliases: dict[str, str]) -> list[dict]:
    """Mutate-and-return: enrich each device dict with `local_name` field.

    `local_name` is `None` when the user hasn't set an alias — clients
    should fall back to a sensible default (platform / deviceModel).

    Designed to be called right after the RemnaWave panel response so the
    rest of the bot/cabinet code sees a uniform field.
    """
    for device in devices:
        hwid = device.get('hwid') or ''
        device['local_name'] = aliases.get(hwid) or None
    return devices
