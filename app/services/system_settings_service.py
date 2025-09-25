import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Type, Union, get_args, get_origin

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, settings
from app.database.crud.system_setting import (
    delete_system_setting,
    upsert_system_setting,
)
from app.database.database import AsyncSessionLocal
from app.database.models import SystemSetting


logger = logging.getLogger(__name__)


def _title_from_key(key: str) -> str:
    parts = key.split("_")
    if not parts:
        return key
    return " ".join(part.capitalize() for part in parts)


def _truncate(value: str, max_len: int = 60) -> str:
    value = value.strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


@dataclass(slots=True)
class SettingDefinition:
    key: str
    category_key: str
    category_label: str
    python_type: Type[Any]
    type_label: str
    is_optional: bool
    choices: Optional[List[Tuple[Any, str]]] = None

    @property
    def display_name(self) -> str:
        return _title_from_key(self.key)

    @property
    def has_choices(self) -> bool:
        return bool(self.choices)


class BotConfigurationService:
    EXCLUDED_KEYS: set[str] = {"BOT_TOKEN", "ADMIN_IDS"}

    CATEGORY_TITLES: Dict[str, str] = {
        "DATABASE": "База данных",
        "POSTGRES": "PostgreSQL",
        "SQLITE": "SQLite",
        "REDIS": "Redis",
        "REMNAWAVE": "Remnawave",
        "SUPPORT": "Поддержка",
        "ADMIN": "Администрирование",
        "CHANNEL": "Каналы",
        "TRIAL": "Триал",
        "DEFAULT": "Значения по умолчанию",
        "PRICE": "Цены",
        "TRAFFIC": "Трафик",
        "REFERRAL": "Реферальная программа",
        "AUTOPAY": "Автопродление",
        "MONITORING": "Мониторинг",
        "SERVER": "Статус серверов",
        "MAINTENANCE": "Техработы",
        "PAYMENT": "Оплаты",
        "YOOKASSA": "YooKassa",
        "CRYPTOBOT": "CryptoBot",
        "MULENPAY": "MulenPay",
        "PAL24": "PayPalych",
        "CONNECT": "Кнопка подключения",
        "HAPP": "Happ",
        "VERSION": "Версии",
        "BACKUP": "Бекапы",
        "WEBHOOK": "Вебхуки",
        "LOG": "Логи",
        "DEBUG": "Отладка",
        "TRIBUTE": "Tribute",
        "TELEGRAM": "Telegram Stars",
    }

    CHOICES: Dict[str, List[Tuple[Any, str]]] = {
        "SUPPORT_SYSTEM_MODE": [
            ("tickets", "Только тикеты"),
            ("contact", "Только контакт"),
            ("both", "Тикеты и контакт"),
        ],
        "REMNAWAVE_AUTH_TYPE": [
            ("api_key", "API Key"),
            ("basic_auth", "Basic Auth"),
        ],
        "REMNAWAVE_USER_DELETE_MODE": [
            ("delete", "Удалять пользователя"),
            ("disable", "Деактивировать пользователя"),
        ],
        "DATABASE_MODE": [
            ("auto", "Определять автоматически"),
            ("postgresql", "PostgreSQL"),
            ("sqlite", "SQLite"),
        ],
        "TRAFFIC_SELECTION_MODE": [
            ("selectable", "Пользователь выбирает пакет"),
            ("fixed", "Фиксированный лимит"),
        ],
        "DEFAULT_TRAFFIC_RESET_STRATEGY": [
            ("NO_RESET", "Без сброса"),
            ("DAY", "Ежедневно"),
            ("WEEK", "Еженедельно"),
            ("MONTH", "Ежемесячно"),
        ],
        "CONNECT_BUTTON_MODE": [
            ("guide", "Открывать гайд"),
            ("miniapp_subscription", "Мини-приложение с подпиской"),
            ("miniapp_custom", "Мини-приложение с кастомной ссылкой"),
            ("link", "Прямая ссылка"),
            ("happ_cryptolink", "Happ CryptoLink"),
        ],
        "SERVER_STATUS_MODE": [
            ("disabled", "Отключено"),
            ("external_link", "Внешняя ссылка"),
            ("external_link_miniapp", "Мини-приложение со ссылкой"),
            ("xray", "Интеграция XrayChecker"),
        ],
    }

    _definitions: Dict[str, SettingDefinition] = {}
    _original_values: Dict[str, Any] = settings.model_dump()
    _overrides_raw: Dict[str, Optional[str]] = {}
    _callback_tokens: Dict[str, str] = {}
    _token_lookup: Dict[str, str] = {}

    @classmethod
    def initialize_definitions(cls) -> None:
        if cls._definitions:
            return

        for key, field in Settings.model_fields.items():
            if key in cls.EXCLUDED_KEYS:
                continue

            annotation = field.annotation
            python_type, is_optional = cls._normalize_type(annotation)
            type_label = cls._type_to_label(python_type, is_optional)

            category_key = cls._resolve_category_key(key)
            category_label = cls.CATEGORY_TITLES.get(
                category_key,
                category_key.capitalize() if category_key else "Прочее",
            )

            choices = cls.CHOICES.get(key)

            cls._definitions[key] = SettingDefinition(
                key=key,
                category_key=category_key or "other",
                category_label=category_label,
                python_type=python_type,
                type_label=type_label,
                is_optional=is_optional,
                choices=choices,
            )

    @classmethod
    def _resolve_category_key(cls, key: str) -> str:
        if "_" not in key:
            return key.upper()
        prefix = key.split("_", 1)[0]
        return prefix.upper()

    @classmethod
    def _normalize_type(cls, annotation: Any) -> Tuple[Type[Any], bool]:
        if annotation is None:
            return str, True

        origin = get_origin(annotation)
        if origin is Union:
            args = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(args) == 1:
                nested_type, nested_optional = cls._normalize_type(args[0])
                return nested_type, True
            return str, True

        if annotation in {int, float, bool, str}:
            return annotation, False

        if annotation in {Optional[int], Optional[float], Optional[bool], Optional[str]}:
            nested = get_args(annotation)[0]
            return nested, True

        # Paths, lists, dicts и прочее будем хранить как строки
        return str, False

    @classmethod
    def _type_to_label(cls, python_type: Type[Any], is_optional: bool) -> str:
        base = {
            bool: "bool",
            int: "int",
            float: "float",
            str: "str",
        }.get(python_type, "str")
        return f"optional[{base}]" if is_optional else base

    @classmethod
    def get_categories(cls) -> List[Tuple[str, str, int]]:
        cls.initialize_definitions()
        categories: Dict[str, List[SettingDefinition]] = {}

        for definition in cls._definitions.values():
            categories.setdefault(definition.category_key, []).append(definition)

        result: List[Tuple[str, str, int]] = []
        for category_key, items in categories.items():
            label = items[0].category_label
            result.append((category_key, label, len(items)))

        result.sort(key=lambda item: item[1])
        return result

    @classmethod
    def get_settings_for_category(cls, category_key: str) -> List[SettingDefinition]:
        cls.initialize_definitions()
        filtered = [
            definition
            for definition in cls._definitions.values()
            if definition.category_key == category_key
        ]
        filtered.sort(key=lambda definition: definition.key)
        return filtered

    @classmethod
    def get_definition(cls, key: str) -> SettingDefinition:
        cls.initialize_definitions()
        return cls._definitions[key]

    @classmethod
    def has_override(cls, key: str) -> bool:
        return key in cls._overrides_raw

    @classmethod
    def get_current_value(cls, key: str) -> Any:
        return getattr(settings, key)

    @classmethod
    def get_original_value(cls, key: str) -> Any:
        return cls._original_values.get(key)

    @classmethod
    def format_value(cls, value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "✅ Да" if value else "❌ Нет"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, (list, dict, tuple, set)):
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        return str(value)

    @classmethod
    def format_value_for_list(cls, key: str) -> str:
        value = cls.get_current_value(key)
        formatted = cls._format_value_with_choices(key, value)
        if formatted == "—":
            return formatted
        return _truncate(formatted)

    @classmethod
    async def initialize(cls) -> None:
        cls.initialize_definitions()

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(SystemSetting))
            rows = result.scalars().all()

        overrides: Dict[str, Optional[str]] = {}
        for row in rows:
            if row.key in cls._definitions:
                overrides[row.key] = row.value

        for key, raw_value in overrides.items():
            try:
                parsed_value = cls.deserialize_value(key, raw_value)
            except Exception as error:
                logger.error("Не удалось применить настройку %s: %s", key, error)
                continue

            cls._overrides_raw[key] = raw_value
            cls._apply_to_settings(key, parsed_value)

    @classmethod
    async def reload(cls) -> None:
        cls._overrides_raw.clear()
        await cls.initialize()

    @classmethod
    def get_callback_token(cls, key: str) -> str:
        cls.initialize_definitions()
        if key in cls._callback_tokens:
            return cls._callback_tokens[key]

        token = format(len(cls._callback_tokens) + 1, "x")
        while token in cls._token_lookup:
            token = format(len(cls._callback_tokens) + len(cls._token_lookup) + 1, "x")

        cls._callback_tokens[key] = token
        cls._token_lookup[token] = key
        return token

    @classmethod
    def resolve_key_from_token(cls, token: str) -> Optional[str]:
        if not token:
            return None
        return cls._token_lookup.get(token)

    @classmethod
    def get_choices(cls, key: str) -> List[Tuple[Any, str]]:
        definition = cls.get_definition(key)
        return definition.choices or []

    @classmethod
    def format_choice_label(cls, key: str, value: Any) -> Optional[str]:
        definition = cls.get_definition(key)
        if not definition.choices:
            return None

        for stored_value, label in definition.choices:
            if cls._values_equal(stored_value, value):
                return f"{label} ({stored_value})"
        return None

    @classmethod
    def cast_choice_value(cls, key: str, raw_value: Any) -> Any:
        definition = cls.get_definition(key)
        python_type = definition.python_type

        if python_type is bool:
            return bool(raw_value)
        if python_type is int:
            return int(raw_value)
        if python_type is float:
            return float(raw_value)
        return str(raw_value)

    @classmethod
    def deserialize_value(cls, key: str, raw_value: Optional[str]) -> Any:
        if raw_value is None:
            return None

        definition = cls.get_definition(key)
        python_type = definition.python_type

        if python_type is bool:
            value_lower = raw_value.strip().lower()
            if value_lower in {"1", "true", "on", "yes", "да"}:
                return True
            if value_lower in {"0", "false", "off", "no", "нет"}:
                return False
            raise ValueError(f"Неверное булево значение: {raw_value}")

        if python_type is int:
            return int(raw_value)

        if python_type is float:
            return float(raw_value)

        return raw_value

    @classmethod
    def serialize_value(cls, key: str, value: Any) -> Optional[str]:
        if value is None:
            return None

        definition = cls.get_definition(key)
        python_type = definition.python_type

        if python_type is bool:
            return "true" if value else "false"
        if python_type in {int, float}:
            return str(value)
        return str(value)

    @classmethod
    def parse_user_value(cls, key: str, user_input: str) -> Any:
        definition = cls.get_definition(key)
        text = (user_input or "").strip()

        if text.lower() in {"отмена", "cancel"}:
            raise ValueError("Ввод отменен пользователем")

        if definition.is_optional and text.lower() in {"none", "null", "пусто", ""}:
            return None

        python_type = definition.python_type

        if python_type is bool:
            lowered = text.lower()
            if lowered in {"1", "true", "on", "yes", "да", "вкл", "enable", "enabled"}:
                return True
            if lowered in {"0", "false", "off", "no", "нет", "выкл", "disable", "disabled"}:
                return False
            raise ValueError("Введите 'true' или 'false' (или 'да'/'нет')")

        if python_type is int:
            return int(text)

        if python_type is float:
            return float(text.replace(",", "."))

        return text

    @classmethod
    async def set_value(cls, db: AsyncSession, key: str, value: Any) -> None:
        raw_value = cls.serialize_value(key, value)
        await upsert_system_setting(db, key, raw_value)
        cls._overrides_raw[key] = raw_value
        cls._apply_to_settings(key, value)

    @classmethod
    async def reset_value(cls, db: AsyncSession, key: str) -> None:
        await delete_system_setting(db, key)
        cls._overrides_raw.pop(key, None)
        original = cls.get_original_value(key)
        cls._apply_to_settings(key, original)

    @classmethod
    def _apply_to_settings(cls, key: str, value: Any) -> None:
        try:
            setattr(settings, key, value)
        except Exception as error:
            logger.error("Не удалось применить значение %s=%s: %s", key, value, error)

    @classmethod
    def get_setting_summary(cls, key: str) -> Dict[str, Any]:
        definition = cls.get_definition(key)
        current = cls.get_current_value(key)
        original = cls.get_original_value(key)
        has_override = cls.has_override(key)

        return {
            "key": key,
            "name": definition.display_name,
            "current": cls._format_value_with_choices(key, current),
            "original": cls._format_value_with_choices(key, original),
            "type": definition.type_label,
            "category_key": definition.category_key,
            "category_label": definition.category_label,
            "has_override": has_override,
        }

    @classmethod
    def _format_value_with_choices(cls, key: str, value: Any) -> str:
        formatted = cls.format_value(value)
        definition = cls.get_definition(key)
        if not definition.choices:
            return formatted

        choice_label = cls.format_choice_label(key, value)
        return choice_label or formatted

    @staticmethod
    def _values_equal(a: Any, b: Any) -> bool:
        if isinstance(a, str) and isinstance(b, str):
            return a.lower() == b.lower()
        return a == b


bot_configuration_service = BotConfigurationService

