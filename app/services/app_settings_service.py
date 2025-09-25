import json
import logging
from typing import Any, Dict, List

from app.config import settings
from app.database.crud.app_settings import AppSettingsCRUD
from app.database.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


class AppSettingsService:
    RESET_COMMANDS = {"--reset", "reset", "/reset", "default", "сброс", "по умолчанию"}

    @classmethod
    async def load_overrides(cls) -> None:
        try:
            async with AsyncSessionLocal() as session:
                records = await AppSettingsCRUD.list_all(session)
                existing_keys = {record.setting_key for record in records}
                defaults = settings.get_initial_editable_values()

                added = False
                for key, default_value in defaults.items():
                    if not settings.is_editable_field(key) or key in existing_keys:
                        continue
                    serialized = cls._serialize_for_storage(key, default_value)
                    await AppSettingsCRUD.upsert(session, key, serialized)
                    added = True

                if added:
                    await session.commit()
                    records = await AppSettingsCRUD.list_all(session)
                else:
                    await session.commit()
        except Exception as error:
            logger.error("Не удалось синхронизировать таблицу app_settings: %s", error)
            records = []

        overrides: Dict[str, Any] = {}
        for record in records:
            key = record.setting_key
            if not settings.is_editable_field(key):
                continue
            try:
                value = cls._deserialize_value(key, record.value)
            except Exception as error:
                logger.warning("Не удалось разобрать значение настройки %s: %s", key, error)
                continue
            overrides[key] = value

        if overrides:
            settings.apply_overrides(overrides)
            logger.info("Применены %d пользовательских настроек", len(overrides))

    @classmethod
    def get_categories(cls) -> List[Dict[str, Any]]:
        categories: Dict[str, Dict[str, Any]] = {}
        for field in cls._collect_fields():
            category_key = field["category_key"]
            category = categories.setdefault(
                category_key,
                {
                    "key": category_key,
                    "label": field["category_label"],
                    "fields": [],
                },
            )
            category["fields"].append(field)

        for category in categories.values():
            category["fields"].sort(key=lambda item: item["label"].lower())

        def sort_key(item: Dict[str, Any]) -> tuple[int, str]:
            return (1 if item["label"] == "Прочее" else 0, item["label"].lower())

        return sorted(categories.values(), key=sort_key)

    @classmethod
    def get_category_fields(cls, category_key: str) -> List[Dict[str, Any]]:
        category_key = category_key.upper()
        fields = [
            field
            for field in cls._collect_fields()
            if field["category_key"] == category_key
        ]
        fields.sort(key=lambda item: item["label"].lower())
        return fields

    @classmethod
    def get_field_info(cls, key: str) -> Dict[str, Any]:
        if not settings.is_editable_field(key):
            raise ValueError("Недоступная настройка")
        field = settings.model_fields.get(key)
        if not field:
            raise ValueError("Настройка не найдена")
        return {
            "key": key,
            "label": settings.get_field_label(key),
            "type": settings.get_field_type_name(key),
            "current_value": getattr(settings, key),
            "display_value": settings.format_value_for_display(key),
            "default_value": settings.get_initial_value(key),
            "category_key": settings.get_field_category_key(key),
            "category_label": settings.get_field_category_label(key),
            "is_overridden": settings.get_initial_value(key) != getattr(settings, key),
        }

    @classmethod
    async def update_setting(cls, key: str, raw_value: str) -> Dict[str, Any]:
        if not settings.is_editable_field(key):
            raise ValueError("Эту настройку нельзя изменять через админку")

        if raw_value is None:
            raise ValueError("Значение не может быть пустым")

        stripped = raw_value.strip()
        use_default = stripped.lower() in cls.RESET_COMMANDS

        if use_default:
            parsed_value = settings.get_initial_value(key)
        else:
            parsed_value = settings.parse_raw_value(key, stripped)

        serialized = cls._serialize_for_storage(key, parsed_value)

        async with AsyncSessionLocal() as session:
            await AppSettingsCRUD.upsert(session, key, serialized)
            await session.commit()

        settings.apply_override(key, parsed_value)

        return {
            "key": key,
            "value": parsed_value,
            "display": settings.format_value_for_display(key, parsed_value),
            "is_default": settings.get_initial_value(key) == parsed_value,
        }

    @classmethod
    def _collect_fields(cls) -> List[Dict[str, Any]]:
        fields: List[Dict[str, Any]] = []
        for key in settings.model_fields.keys():
            if not settings.is_editable_field(key):
                continue
            fields.append(
                {
                    "key": key,
                    "label": settings.get_field_label(key),
                    "type": settings.get_field_type_name(key),
                    "value": getattr(settings, key),
                    "display_value": settings.format_value_for_display(key),
                    "category_key": settings.get_field_category_key(key),
                    "category_label": settings.get_field_category_label(key),
                    "is_overridden": settings.get_initial_value(key) != getattr(settings, key),
                }
            )
        return fields

    @classmethod
    def _serialize_for_storage(cls, key: str, value: Any) -> str:
        prepared = settings.serialize_value_for_storage(key, value)
        return json.dumps(prepared, ensure_ascii=False)

    @classmethod
    def _deserialize_value(cls, key: str, raw_value: str | None) -> Any:
        if raw_value is None or raw_value == "":
            stored = None
        else:
            stored = json.loads(raw_value)
        return settings.cast_value(key, stored)
