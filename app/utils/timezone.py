"""Timezone utilities for consistent local time handling."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import structlog

from app.config import settings


logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_local_timezone() -> ZoneInfo:
    """Return the configured local timezone.

    Falls back to UTC if the configured timezone cannot be loaded. The
    fallback is logged once and cached for subsequent calls.
    """

    tz_name = settings.TIMEZONE

    try:
        return ZoneInfo(tz_name)
    except Exception as exc:  # pragma: no cover - defensive branch
        logger.warning("⚠️ Не удалось загрузить временную зону '': . Используем UTC.", tz_name=tz_name, exc=exc)
        return ZoneInfo('UTC')


def panel_datetime_to_naive_utc(dt: datetime) -> datetime:
    """Convert a panel datetime to naive UTC.

    Panel API returns local time with a misleading UTC offset (+00:00 / Z).
    This strips the offset, interprets the raw value as panel-local time,
    then converts to naive UTC for database storage.
    """
    naive = dt.replace(tzinfo=None)
    localized = naive.replace(tzinfo=get_local_timezone())
    return localized.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)


def to_local_datetime(dt: datetime | None) -> datetime | None:
    """Convert a datetime value to the configured local timezone."""

    if dt is None:
        return None

    aware_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware_dt.astimezone(get_local_timezone())


def format_local_datetime(
    dt: datetime | None,
    fmt: str = '%Y-%m-%d %H:%M:%S %Z',
    na_placeholder: str = 'N/A',
) -> str:
    """Format a datetime value in the configured local timezone."""

    localized = to_local_datetime(dt)
    if localized is None:
        return na_placeholder
    return localized.strftime(fmt)
