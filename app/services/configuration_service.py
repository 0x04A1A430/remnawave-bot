import asyncio
import logging
import math
from typing import Any, Dict, List, Optional, Tuple, Annotated, Union, get_args, get_origin

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import (
    Settings,
    refresh_period_prices,
    refresh_traffic_prices,
    settings,
)
from app.database.database import AsyncSessionLocal
from app.database.models import BotConfig


logger = logging.getLogger(__name__)


class ConfigurationValidationError(Exception):
    def __init__(self, code: str, key: str):
        super().__init__(code)
        self.code = code
        self.key = key


class ConfigurationService:
    EXCLUDED_KEYS = {"BOT_TOKEN", "ADMIN_IDS"}

    PERIOD_PRICE_KEYS = {
        "PRICE_14_DAYS",
        "PRICE_30_DAYS",
        "PRICE_60_DAYS",
        "PRICE_90_DAYS",
        "PRICE_180_DAYS",
        "PRICE_360_DAYS",
    }

    TRAFFIC_PRICE_KEYS = {
        "TRAFFIC_PACKAGES_CONFIG",
        "PRICE_TRAFFIC_5GB",
        "PRICE_TRAFFIC_10GB",
        "PRICE_TRAFFIC_25GB",
        "PRICE_TRAFFIC_50GB",
        "PRICE_TRAFFIC_100GB",
        "PRICE_TRAFFIC_250GB",
        "PRICE_TRAFFIC_500GB",
        "PRICE_TRAFFIC_1000GB",
        "PRICE_TRAFFIC_UNLIMITED",
    }

    DATABASE_KEYS = {
        "DATABASE_URL",
        "DATABASE_MODE",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "SQLITE_PATH",
    }

    BOOL_TRUE_VALUES = {"1", "true", "yes", "on", "enable", "enabled", "y", "да", "истина"}
    BOOL_FALSE_VALUES = {"0", "false", "no", "off", "disable", "disabled", "n", "нет", "ложь"}

    def __init__(self) -> None:
        self._cache: Dict[str, Optional[str]] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    async def ensure_loaded(self) -> None:
        if self._loaded:
            return
        await self.load_and_apply()

    async def load_and_apply(self) -> None:
        async with self._lock:
            try:
                await self._load_from_db()
                self._loaded = True
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error("Не удалось загрузить конфигурацию бота: %s", exc)
                self._loaded = False
                raise

    async def _load_from_db(self) -> None:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(BotConfig))
            records = result.scalars().all()

        self._cache = {record.key: record.value for record in records}
        await self._ensure_missing_keys()

        overrides: Dict[str, Any] = {}
        for key, stored in self._cache.items():
            if not self.is_configurable(key):
                continue
            overrides[key] = self._deserialize_value(key, stored)

        self._apply_overrides(overrides)

    async def _ensure_missing_keys(self) -> None:
        missing = [key for key in self._ordered_keys() if key not in self._cache]
        if not missing:
            return

        async with AsyncSessionLocal() as session:
            try:
                for key in missing:
                    value = getattr(settings, key, None)
                    serialized = self._serialize_for_storage(key, value)
                    session.add(BotConfig(key=key, value=serialized))
                    self._cache[key] = serialized
                await session.commit()
            except Exception as exc:  # pragma: no cover - defensive logging
                await session.rollback()
                logger.error("Не удалось сохранить значения по умолчанию bot_config: %s", exc)
                raise

    def _ordered_keys(self) -> List[str]:
        return [key for key in Settings.model_fields.keys() if self.is_configurable(key)]

    def is_configurable(self, key: str) -> bool:
        return key not in self.EXCLUDED_KEYS and key in Settings.model_fields

    async def get_paginated_items(self, page: int, per_page: int) -> Dict[str, Any]:
        await self.ensure_loaded()

        keys = self._ordered_keys()
        total = len(keys)
        total_pages = max(1, math.ceil(total / per_page)) if total else 1
        page = max(1, min(page, total_pages))

        start = (page - 1) * per_page
        end = start + per_page

        items: List[Dict[str, Any]] = []
        for key in keys[start:end]:
            value = getattr(settings, key, None)
            display = self.format_value_for_display(key, value)
            items.append(
                {
                    "key": key,
                    "value": value,
                    "display": display,
                    "short_display": self._shorten_display(display),
                    "type": self.get_type_name(key),
                    "optional": self._is_optional(key),
                }
            )

        return {
            "items": items,
            "total": total,
            "total_pages": total_pages,
            "page": page,
        }

    async def get_item(self, key: str) -> Dict[str, Any]:
        await self.ensure_loaded()

        if not self.is_configurable(key):
            raise ConfigurationValidationError("unknown_setting", key)

        value = getattr(settings, key, None)
        return {
            "key": key,
            "value": value,
            "display": self.format_value_for_display(key, value),
            "type": self.get_type_name(key),
            "optional": self._is_optional(key),
        }

    async def update_setting(self, key: str, raw_value: str) -> Tuple[Any, str]:
        await self.ensure_loaded()

        if not self.is_configurable(key):
            raise ConfigurationValidationError("unknown_setting", key)

        python_value = self._parse_user_input(key, raw_value)
        serialized = self._serialize_for_storage(key, python_value)

        async with AsyncSessionLocal() as session:
            try:
                record = await session.get(BotConfig, key)
                if record is None:
                    record = BotConfig(key=key, value=serialized)
                    session.add(record)
                else:
                    record.value = serialized
                await session.commit()
            except SQLAlchemyError as exc:
                await session.rollback()
                logger.error("Ошибка обновления bot_config %s: %s", key, exc)
                raise ConfigurationValidationError("db_error", key) from exc

        self._cache[key] = serialized
        self._apply_overrides({key: python_value})

        display = self.format_value_for_display(key, python_value)
        return python_value, display

    def format_value_for_display(self, key: str, value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "✅ True" if value else "❌ False"
        return str(value)

    def get_type_name(self, key: str) -> str:
        base_type, optional = self._resolve_type(key)
        type_name = getattr(base_type, "__name__", str(base_type))
        if optional:
            return f"Optional[{type_name}]"
        return type_name

    def _shorten_display(self, text: str, limit: int = 24) -> str:
        clean = " ".join(text.split())
        if len(clean) > limit:
            return clean[: limit - 1] + "…"
        return clean

    def _parse_user_input(self, key: str, raw_value: Optional[str]) -> Any:
        base_type, optional = self._resolve_type(key)
        text = (raw_value or "").strip()

        if optional and text.lower() in {"", "null", "none"}:
            return None

        if base_type is bool:
            lowered = text.lower()
            if lowered in self.BOOL_TRUE_VALUES:
                return True
            if lowered in self.BOOL_FALSE_VALUES:
                return False
            raise ConfigurationValidationError("invalid_bool", key)

        if base_type is int:
            try:
                return int(text)
            except ValueError as exc:
                raise ConfigurationValidationError("invalid_int", key) from exc

        if base_type is float:
            try:
                normalized = text.replace(",", ".")
                return float(normalized)
            except ValueError as exc:
                raise ConfigurationValidationError("invalid_float", key) from exc

        if not text and not optional and base_type is not str:
            raise ConfigurationValidationError("invalid_value", key)

        return raw_value or ""

    def _serialize_for_storage(self, key: str, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _deserialize_value(self, key: str, stored: Optional[str]) -> Any:
        if stored is None:
            return None if self._is_optional(key) else getattr(settings, key, None)

        base_type, _ = self._resolve_type(key)
        text = str(stored).strip()

        if base_type is bool:
            lowered = text.lower()
            if lowered in self.BOOL_TRUE_VALUES:
                return True
            if lowered in self.BOOL_FALSE_VALUES:
                return False
            logger.warning("Неверное булево значение для %s: %s", key, stored)
            return getattr(settings, key, None)

        if base_type is int:
            try:
                return int(text)
            except ValueError:
                logger.warning("Неверное числовое значение для %s: %s", key, stored)
                return getattr(settings, key, None)

        if base_type is float:
            try:
                return float(text)
            except ValueError:
                logger.warning("Неверное значение с плавающей точкой для %s: %s", key, stored)
                return getattr(settings, key, None)

        return stored

    def _apply_overrides(self, overrides: Dict[str, Any]) -> None:
        if not overrides:
            return

        changed_keys = []
        for key, value in overrides.items():
            if not self.is_configurable(key):
                continue
            try:
                setattr(settings, key, value)
                changed_keys.append(key)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error("Не удалось применить параметр %s: %s", key, exc)

        if not changed_keys:
            return

        if any(key in self.PERIOD_PRICE_KEYS for key in changed_keys):
            refresh_period_prices()

        if any(key in self.TRAFFIC_PRICE_KEYS for key in changed_keys):
            refresh_traffic_prices()

        if any(key in self.DATABASE_KEYS for key in changed_keys):
            try:
                settings.DATABASE_URL = settings.get_database_url()
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Не удалось обновить DATABASE_URL после изменения настроек: %s", exc)

    def _resolve_type(self, key: str) -> Tuple[type, bool]:
        field = Settings.model_fields.get(key)
        optional = False

        if field is None:
            return str, optional

        annotation = field.annotation

        annotation, optional = self._unwrap_annotation(annotation)

        if isinstance(annotation, type):
            if issubclass(annotation, bool):
                return bool, optional
            if issubclass(annotation, int):
                return int, optional
            if issubclass(annotation, float):
                return float, optional
            if issubclass(annotation, str):
                return str, optional

        default_value = getattr(settings, key, None)
        if isinstance(default_value, bool):
            return bool, optional
        if isinstance(default_value, int):
            return int, optional
        if isinstance(default_value, float):
            return float, optional
        if isinstance(default_value, str):
            return str, optional

        return str, optional

    def _unwrap_annotation(self, annotation: Any) -> Tuple[Any, bool]:
        origin = get_origin(annotation)

        if origin is Annotated:
            args = get_args(annotation)
            if args:
                return self._unwrap_annotation(args[0])

        if origin is Union:
            args = get_args(annotation)
            non_none = [arg for arg in args if arg is not type(None)]
            optional = len(non_none) != len(args)
            if not non_none:
                return str, True
            if len(non_none) == 1:
                inner, inner_optional = self._unwrap_annotation(non_none[0])
                return inner, optional or inner_optional
            return str, optional

        if origin in {list, tuple, dict, set}:
            return str, False

        return annotation, False

    def _is_optional(self, key: str) -> bool:
        _, optional = self._resolve_type(key)
        return optional


configuration_service = ConfigurationService()

