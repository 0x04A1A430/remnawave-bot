"""Runtime storage for user notification preferences."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


class NotificationSettingsService:
    """Manage runtime-configurable notification settings.

    Values are stored in ``data/notification_settings.json`` and can be
    modified from the admin panel without restarting the bot.
    """

    _storage_path: Path = Path("data/notification_settings.json")
    _data: Dict[str, Any] = {}
    _loaded: bool = False

    _defaults: Dict[str, Any] = {
        "trial_inactive_1h_enabled": True,
        "trial_inactive_24h_enabled": True,
        "expired_day1_enabled": True,
        "expired_day23_enabled": True,
        "expired_day23_discount_percent": 20,
        "expired_day23_valid_hours": 24,
        "expired_dayn_enabled": True,
        "expired_dayn_discount_percent": 30,
        "expired_dayn_valid_hours": 24,
        "expired_dayn_threshold_days": 5,
    }

    @classmethod
    def _ensure_storage_dir(cls) -> None:
        try:
            cls._storage_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Failed to create notification settings directory: %s", exc)

    @classmethod
    def _load(cls) -> None:
        if cls._loaded:
            return

        cls._ensure_storage_dir()
        if cls._storage_path.exists():
            try:
                cls._data = json.loads(cls._storage_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.error("Failed to load notification settings: %s", exc)
                cls._data = {}
        else:
            cls._data = {}

        cls._loaded = True

    @classmethod
    def _save(cls) -> bool:
        cls._ensure_storage_dir()
        try:
            cls._storage_path.write_text(
                json.dumps(cls._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Failed to save notification settings: %s", exc)
            return False

    # Helper accessors -----------------------------------------------------
    @classmethod
    def _get_bool(cls, key: str) -> bool:
        cls._load()
        if key in cls._data:
            return bool(cls._data[key])
        return bool(cls._defaults.get(key, False))

    @classmethod
    def _set_bool(cls, key: str, value: bool) -> bool:
        cls._load()
        cls._data[key] = bool(value)
        return cls._save()

    @classmethod
    def _get_int(cls, key: str) -> int:
        cls._load()
        if key in cls._data:
            try:
                return int(cls._data[key])
            except (TypeError, ValueError):
                pass
        return int(cls._defaults.get(key, 0))

    @classmethod
    def _set_int(cls, key: str, value: int) -> bool:
        cls._load()
        cls._data[key] = int(value)
        return cls._save()

    @classmethod
    def get_all(cls) -> Dict[str, Any]:
        cls._load()
        data = {**cls._defaults, **cls._data}
        # cast ints to ensure consistent types
        int_keys = [
            "expired_day23_discount_percent",
            "expired_day23_valid_hours",
            "expired_dayn_discount_percent",
            "expired_dayn_valid_hours",
            "expired_dayn_threshold_days",
        ]
        for key in int_keys:
            try:
                data[key] = int(data[key])
            except (TypeError, ValueError):
                data[key] = int(cls._defaults[key])
        bool_keys = [
            "trial_inactive_1h_enabled",
            "trial_inactive_24h_enabled",
            "expired_day1_enabled",
            "expired_day23_enabled",
            "expired_dayn_enabled",
        ]
        for key in bool_keys:
            data[key] = bool(data.get(key, cls._defaults[key]))
        return data

    # Trial inactivity -----------------------------------------------------
    @classmethod
    def is_trial_inactive_1h_enabled(cls) -> bool:
        return cls._get_bool("trial_inactive_1h_enabled")

    @classmethod
    def set_trial_inactive_1h_enabled(cls, enabled: bool) -> bool:
        return cls._set_bool("trial_inactive_1h_enabled", enabled)

    @classmethod
    def is_trial_inactive_24h_enabled(cls) -> bool:
        return cls._get_bool("trial_inactive_24h_enabled")

    @classmethod
    def set_trial_inactive_24h_enabled(cls, enabled: bool) -> bool:
        return cls._set_bool("trial_inactive_24h_enabled", enabled)

    # Expired subscription follow-ups -------------------------------------
    @classmethod
    def is_expired_day1_enabled(cls) -> bool:
        return cls._get_bool("expired_day1_enabled")

    @classmethod
    def set_expired_day1_enabled(cls, enabled: bool) -> bool:
        return cls._set_bool("expired_day1_enabled", enabled)

    @classmethod
    def is_expired_day23_enabled(cls) -> bool:
        return cls._get_bool("expired_day23_enabled")

    @classmethod
    def set_expired_day23_enabled(cls, enabled: bool) -> bool:
        return cls._set_bool("expired_day23_enabled", enabled)

    @classmethod
    def get_expired_day23_discount_percent(cls) -> int:
        return max(0, min(100, cls._get_int("expired_day23_discount_percent")))

    @classmethod
    def set_expired_day23_discount_percent(cls, percent: int) -> bool:
        percent = max(0, min(100, int(percent)))
        return cls._set_int("expired_day23_discount_percent", percent)

    @classmethod
    def get_expired_day23_valid_hours(cls) -> int:
        return max(1, cls._get_int("expired_day23_valid_hours"))

    @classmethod
    def set_expired_day23_valid_hours(cls, hours: int) -> bool:
        hours = max(1, int(hours))
        return cls._set_int("expired_day23_valid_hours", hours)

    @classmethod
    def is_expired_dayn_enabled(cls) -> bool:
        return cls._get_bool("expired_dayn_enabled")

    @classmethod
    def set_expired_dayn_enabled(cls, enabled: bool) -> bool:
        return cls._set_bool("expired_dayn_enabled", enabled)

    @classmethod
    def get_expired_dayn_discount_percent(cls) -> int:
        return max(0, min(100, cls._get_int("expired_dayn_discount_percent")))

    @classmethod
    def set_expired_dayn_discount_percent(cls, percent: int) -> bool:
        percent = max(0, min(100, int(percent)))
        return cls._set_int("expired_dayn_discount_percent", percent)

    @classmethod
    def get_expired_dayn_valid_hours(cls) -> int:
        return max(1, cls._get_int("expired_dayn_valid_hours"))

    @classmethod
    def set_expired_dayn_valid_hours(cls, hours: int) -> bool:
        hours = max(1, int(hours))
        return cls._set_int("expired_dayn_valid_hours", hours)

    @classmethod
    def get_expired_dayn_threshold_days(cls) -> int:
        return max(4, cls._get_int("expired_dayn_threshold_days"))

    @classmethod
    def set_expired_dayn_threshold_days(cls, days: int) -> bool:
        days = max(4, int(days))
        return cls._set_int("expired_dayn_threshold_days", days)
