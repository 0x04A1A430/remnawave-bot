"""Timezone utilities for consistent local time handling."""

from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo

from app.config import settings

logger = logging.getLogger(__name__)


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
        logger.warning(
            "⚠️ Не удалось загрузить временную зону '%s': %s. Используем UTC.",
            tz_name,
            exc,
        )
        return ZoneInfo("UTC")


def to_local_datetime(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert a datetime value to the configured local timezone."""

    if dt is None:
        return None

    aware_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=dt_timezone.utc)
    return aware_dt.astimezone(get_local_timezone())


def format_local_datetime(
    dt: Optional[datetime],
    fmt: str = "%Y-%m-%d %H:%M:%S %Z",
    na_placeholder: str = "N/A",
) -> str:
    """Format a datetime value in the configured local timezone."""

    localized = to_local_datetime(dt)
    if localized is None:
        return na_placeholder
    return localized.strftime(fmt)


class TimezoneAwareFormatter(logging.Formatter):
    """Logging formatter that renders timestamps in the configured timezone."""

    def __init__(self, *args, timezone_name: Optional[str] = None, **kwargs):
        super().__init__(*args, **kwargs)
        if timezone_name:
            try:
                self._timezone = ZoneInfo(timezone_name)
            except Exception as exc:  # pragma: no cover - defensive branch
                logger.warning(
                    "⚠️ Не удалось загрузить временную зону '%s': %s. Используем UTC.",
                    timezone_name,
                    exc,
                )
                self._timezone = ZoneInfo("UTC")
        else:
            self._timezone = get_local_timezone()

    def formatTime(self, record, datefmt=None):  # noqa: N802 - inherited method name
        dt = datetime.fromtimestamp(record.created, tz=self._timezone)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
