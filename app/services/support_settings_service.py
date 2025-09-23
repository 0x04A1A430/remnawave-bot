import json
import logging
from pathlib import Path
from typing import Dict

from app.config import settings


logger = logging.getLogger(__name__)


class SupportSettingsService:
    """Runtime editable support settings with JSON persistence."""

    _storage_path: Path = Path("data/support_settings.json")
    _data: Dict = {}
    _loaded: bool = False

    @classmethod
    def _ensure_dir(cls) -> None:
        try:
            cls._storage_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to ensure settings dir: {e}")

    @classmethod
    def _load(cls) -> None:
        if cls._loaded:
            return
        cls._ensure_dir()
        try:
            if cls._storage_path.exists():
                cls._data = json.loads(cls._storage_path.read_text(encoding="utf-8"))
            else:
                cls._data = {}
        except Exception as e:
            logger.error(f"Failed to load support settings: {e}")
            cls._data = {}
        cls._loaded = True

    @classmethod
    def _save(cls) -> bool:
        cls._ensure_dir()
        try:
            cls._storage_path.write_text(json.dumps(cls._data, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            logger.error(f"Failed to save support settings: {e}")
            return False

    # Mode
    @classmethod
    def get_system_mode(cls) -> str:
        cls._load()
        mode = (cls._data.get("system_mode") or settings.get_support_system_mode()).strip().lower()
        return mode if mode in {"tickets", "contact", "both"} else "both"

    @classmethod
    def set_system_mode(cls, mode: str) -> bool:
        mode_clean = (mode or "").strip().lower()
        if mode_clean not in {"tickets", "contact", "both"}:
            return False
        cls._load()
        cls._data["system_mode"] = mode_clean
        return cls._save()

    # Main menu visibility
    @classmethod
    def is_support_menu_enabled(cls) -> bool:
        cls._load()
        if "menu_enabled" in cls._data:
            return bool(cls._data["menu_enabled"])
        return bool(settings.SUPPORT_MENU_ENABLED)

    @classmethod
    def set_support_menu_enabled(cls, enabled: bool) -> bool:
        cls._load()
        cls._data["menu_enabled"] = bool(enabled)
        return cls._save()

    # Contact vs tickets helpers
    @classmethod
    def is_tickets_enabled(cls) -> bool:
        return cls.get_system_mode() in {"tickets", "both"}

    @classmethod
    def is_contact_enabled(cls) -> bool:
        return cls.get_system_mode() in {"contact", "both"}

    # Descriptions (per language)
    @classmethod
    def get_support_info_text(cls, language: str) -> str:
        cls._load()
        lang = (language or settings.DEFAULT_LANGUAGE).split("-")[0].lower()
        overrides = cls._data.get("support_info_texts") or {}
        text = overrides.get(lang)
        if text and isinstance(text, str) and text.strip():
            return text
        # Fallback to dynamic localization default
        from app.localization.texts import get_texts
        return get_texts(lang).SUPPORT_INFO

    @classmethod
    def set_support_info_text(cls, language: str, text: str) -> bool:
        cls._load()
        lang = (language or settings.DEFAULT_LANGUAGE).split("-")[0].lower()
        texts_map = cls._data.get("support_info_texts") or {}
        texts_map[lang] = text or ""
        cls._data["support_info_texts"] = texts_map
        return cls._save()


