import json
import logging
from pathlib import Path
from typing import Any, Dict


logger = logging.getLogger(__name__)


class AutoNotificationSettingsService:
    """Runtime storage for auto notification settings.

    Settings are stored in a JSON file inside the data directory so they can be
    tweaked from the admin panel without restarting the bot. Only overrides are
    persisted – sensible defaults are provided in code.
    """

    _storage_path: Path = Path("data/auto_notification_settings.json")
    _defaults: Dict[str, Any] = {
        "trial_no_connection_1h_enabled": True,
        "trial_no_connection_24h_enabled": True,
        "expired_day1_enabled": True,
        "expired_day23_enabled": True,
        "expired_day23_discount_percent": 15,
        "expired_day23_valid_hours": 24,
        "expired_day23_window_start": 2,
        "expired_day23_window_end": 3,
        "expired_dayN_enabled": True,
        "expired_dayN_discount_percent": 25,
        "expired_dayN_valid_hours": 24,
        "expired_dayN_threshold": 7,
    }
    _data: Dict[str, Any] = {}
    _loaded: bool = False

    @classmethod
    def _ensure_loaded(cls) -> None:
        if cls._loaded:
            return

        try:
            cls._storage_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Не удалось создать директорию настроек уведомлений: %s", e)

        if cls._storage_path.exists():
            try:
                raw = cls._storage_path.read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else {}
                if isinstance(data, dict):
                    cls._data = data
                else:
                    cls._data = {}
            except Exception as e:
                logger.error("Ошибка загрузки настроек уведомлений: %s", e)
                cls._data = {}
        else:
            cls._data = {}

        cls._loaded = True

    @classmethod
    def _get_merged(cls) -> Dict[str, Any]:
        cls._ensure_loaded()
        merged = dict(cls._defaults)
        merged.update(cls._data)
        return merged

    @classmethod
    def _save(cls) -> bool:
        try:
            cls._storage_path.parent.mkdir(parents=True, exist_ok=True)
            data_to_save = cls._get_merged()
            cls._storage_path.write_text(
                json.dumps(data_to_save, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except Exception as e:
            logger.error("Ошибка сохранения настроек уведомлений: %s", e)
            return False

    @classmethod
    def get_settings(cls) -> Dict[str, Any]:
        """Returns a copy of current settings with defaults applied."""

        return cls._get_merged().copy()

    @classmethod
    def _set_value(cls, key: str, value: Any) -> bool:
        cls._ensure_loaded()
        cls._data[key] = value
        return cls._save()

    # Trial reminders
    @classmethod
    def is_trial_1h_enabled(cls) -> bool:
        return bool(cls.get_settings().get("trial_no_connection_1h_enabled", True))

    @classmethod
    def set_trial_1h_enabled(cls, enabled: bool) -> bool:
        return cls._set_value("trial_no_connection_1h_enabled", bool(enabled))

    @classmethod
    def is_trial_24h_enabled(cls) -> bool:
        return bool(cls.get_settings().get("trial_no_connection_24h_enabled", True))

    @classmethod
    def set_trial_24h_enabled(cls, enabled: bool) -> bool:
        return cls._set_value("trial_no_connection_24h_enabled", bool(enabled))

    # Expired subscription follow-ups
    @classmethod
    def is_expired_day1_enabled(cls) -> bool:
        return bool(cls.get_settings().get("expired_day1_enabled", True))

    @classmethod
    def set_expired_day1_enabled(cls, enabled: bool) -> bool:
        return cls._set_value("expired_day1_enabled", bool(enabled))

    @classmethod
    def is_expired_day23_enabled(cls) -> bool:
        return bool(cls.get_settings().get("expired_day23_enabled", True))

    @classmethod
    def set_expired_day23_enabled(cls, enabled: bool) -> bool:
        return cls._set_value("expired_day23_enabled", bool(enabled))

    @classmethod
    def get_expired_day23_discount(cls) -> int:
        try:
            return int(cls.get_settings().get("expired_day23_discount_percent", 15))
        except Exception:
            return 15

    @classmethod
    def set_expired_day23_discount(cls, percent: int) -> bool:
        return cls._set_value("expired_day23_discount_percent", int(percent))

    @classmethod
    def get_expired_day23_valid_hours(cls) -> int:
        try:
            return int(cls.get_settings().get("expired_day23_valid_hours", 24))
        except Exception:
            return 24

    @classmethod
    def get_expired_day23_window(cls) -> tuple[int, int]:
        settings = cls.get_settings()
        try:
            start = int(settings.get("expired_day23_window_start", 2))
        except Exception:
            start = 2
        try:
            end = int(settings.get("expired_day23_window_end", 3))
        except Exception:
            end = 3
        return start, end

    @classmethod
    def is_expired_dayN_enabled(cls) -> bool:
        return bool(cls.get_settings().get("expired_dayN_enabled", True))

    @classmethod
    def set_expired_dayN_enabled(cls, enabled: bool) -> bool:
        return cls._set_value("expired_dayN_enabled", bool(enabled))

    @classmethod
    def get_expired_dayN_discount(cls) -> int:
        try:
            return int(cls.get_settings().get("expired_dayN_discount_percent", 25))
        except Exception:
            return 25

    @classmethod
    def set_expired_dayN_discount(cls, percent: int) -> bool:
        return cls._set_value("expired_dayN_discount_percent", int(percent))

    @classmethod
    def get_expired_dayN_valid_hours(cls) -> int:
        try:
            return int(cls.get_settings().get("expired_dayN_valid_hours", 24))
        except Exception:
            return 24

    @classmethod
    def get_expired_dayN_threshold(cls) -> int:
        try:
            return int(cls.get_settings().get("expired_dayN_threshold", 7))
        except Exception:
            return 7

    @classmethod
    def set_expired_dayN_threshold(cls, days: int) -> bool:
        return cls._set_value("expired_dayN_threshold", int(days))
