import math
import html
import io
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

from aiogram import Dispatcher, F, types
from aiogram.types import BufferedInputFile
from aiogram.filters import BaseFilter, StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.config import settings
from app.services.remnawave_service import RemnaWaveService
from app.services.payment_service import PaymentService
from app.services.tribute_service import TributeService
from app.services.system_settings_service import (
    SettingDefinition,
    bot_configuration_service,
)
from app.states import BotConfigStates
from app.utils.decorators import admin_required, error_handler
from app.utils.currency_converter import currency_converter
from app.external.telegram_stars import TelegramStarsService


CATEGORY_PAGE_SIZE = 10
SETTINGS_PAGE_SIZE = 8


@dataclass(slots=True)
class MenuCategoryDefinition:
    key: str
    title: str
    description: str
    category_keys: Tuple[str, ...]

    @property
    def icon(self) -> str:
        return self.title.split(" ", 1)[0]

    @property
    def plain_title(self) -> str:
        return self.title.split(" ", 1)[1] if " " in self.title else self.title


class SettingKind(Enum):
    TOGGLE = "toggle"
    TEXT = "text"
    NUMBER = "number"
    FLOAT = "float"
    PRICE = "price"
    LIST = "list"
    CHOICE = "choice"
    TIME = "time"
    URL = "url"
    SECRET = "secret"


@dataclass(slots=True)
class SettingMetadata:
    description: str = ""
    format_hint: str = ""
    example: str = ""
    warning: str = ""
    dependencies: str = ""
    recommended: Optional[str] = None
    sensitive: bool = False
    unit: Optional[str] = None
    doc_link: Optional[str] = None
    highlight: Optional[str] = None


MENU_CATEGORIES: Tuple[MenuCategoryDefinition, ...] = (
    MenuCategoryDefinition(
        key="core",
        title="🤖 Основные",
        description="Базовые параметры и ключевые переключатели",
        category_keys=("CHANNEL", "SKIP", "CONNECT_BUTTON"),
    ),
    MenuCategoryDefinition(
        key="support",
        title="💬 Поддержка",
        description="Тикеты, контакт и SLA",
        category_keys=("SUPPORT",),
    ),
    MenuCategoryDefinition(
        key="payments",
        title="💳 Платежные системы",
        description="YooKassa, CryptoBot, MulenPay и другие провайдеры",
        category_keys=(
            "PAYMENT",
            "YOOKASSA",
            "CRYPTOBOT",
            "MULENPAY",
            "PAL24",
            "TRIBUTE",
            "TELEGRAM",
        ),
    ),
    MenuCategoryDefinition(
        key="subscriptions",
        title="📅 Подписки и цены",
        description="Тарифы, периоды, трафик и автопродления",
        category_keys=(
            "PAID_SUBSCRIPTION",
            "PERIODS",
            "SUBSCRIPTION_PRICES",
            "TRAFFIC",
            "TRAFFIC_PACKAGES",
            "DISCOUNTS",
            "AUTOPAY",
        ),
    ),
    MenuCategoryDefinition(
        key="trial",
        title="🎁 Пробный период",
        description="Настройки бесплатного доступа",
        category_keys=("TRIAL",),
    ),
    MenuCategoryDefinition(
        key="referral",
        title="👥 Реферальная программа",
        description="Бонусы, комиссии и уведомления",
        category_keys=("REFERRAL",),
    ),
    MenuCategoryDefinition(
        key="notifications",
        title="🔔 Уведомления",
        description="Админ-уведомления, отчеты и SLA",
        category_keys=("ADMIN_NOTIFICATIONS", "ADMIN_REPORTS", "NOTIFICATIONS"),
    ),
    MenuCategoryDefinition(
        key="interface",
        title="🎨 Интерфейс и брендинг",
        description="Логотип, тексты, языки и miniapp",
        category_keys=(
            "INTERFACE_BRANDING",
            "INTERFACE_SUBSCRIPTION",
            "HAPP",
            "MINIAPP",
            "LOCALIZATION",
        ),
    ),
    MenuCategoryDefinition(
        key="database",
        title="💾 База данных",
        description="Настройки PostgreSQL и SQLite",
        category_keys=("DATABASE", "POSTGRES", "SQLITE"),
    ),
    MenuCategoryDefinition(
        key="remnawave",
        title="🌐 RemnaWave API",
        description="Интеграция с панелью VPN",
        category_keys=("REMNAWAVE",),
    ),
    MenuCategoryDefinition(
        key="server_status",
        title="📊 Статус серверов",
        description="Мониторинг, метрики и XRay",
        category_keys=("SERVER", "MONITORING"),
    ),
    MenuCategoryDefinition(
        key="maintenance",
        title="🔧 Обслуживание",
        description="Режим ТО, бэкапы и версии",
        category_keys=("MAINTENANCE", "BACKUP", "VERSION"),
    ),
    MenuCategoryDefinition(
        key="advanced",
        title="⚡ Расширенные",
        description="Web API, глубокие ссылки и Redis",
        category_keys=("WEB_API", "WEBHOOK", "LOG", "DEBUG", "ADDITIONAL", "REDIS"),
    ),
)


CATEGORY_FALLBACK_KEY = "other"
CATEGORY_FALLBACK_TITLE = "📦 Прочие настройки"


ASSIGNED_CATEGORY_KEYS: set[str] = {
    key
    for menu_category in MENU_CATEGORIES
    for key in menu_category.category_keys
}


SENSITIVE_KEYWORDS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "API_KEY",
    "SECRET_KEY",
    "WEBHOOK_SECRET",
    "SIGNATURE",
    "PRIVATE",
)

LIST_KEYWORDS = (
    "LIST",
    "IDS",
    "PERIODS",
    "ASSETS",
    "LANGUAGES",
    "PACKAGES",
    "WARNING_DAYS",
    "UUIDS",
)

URL_KEYWORDS = (
    "URL",
    "LINK",
    "WEBHOOK",
    "HOST",
    "ENDPOINT",
    "BASE_URL",
)

TIME_KEYWORDS = (
    "_TIME",
    "_HOUR",
    "_HOURS",
    "_MINUTE",
    "_MINUTES",
)


SETTING_METADATA_OVERRIDES: Dict[str, SettingMetadata] = {
    "MAINTENANCE_MODE": SettingMetadata(
        description="Включает режим обслуживания и блокирует пользовательские действия.",
        format_hint="Переключатель: <code>вкл</code>/<code>выкл</code>.",
        example="выкл",
        warning="При включении пользователи увидят сообщение об обслуживании.",
        dependencies="MAINTENANCE_MESSAGE — текст уведомления для пользователей.",
        recommended="false",
        highlight="Критический параметр",
    ),
    "MAINTENANCE_MESSAGE": SettingMetadata(
        description="Сообщение, которое увидят пользователи во время технических работ.",
        format_hint="Текст до 512 символов, поддерживается Markdown.",
        example="🔧 Ведутся технические работы...",
        warning="Избегайте раскрытия внутренних данных, сообщение видят все.",
    ),
    "SUPPORT_USERNAME": SettingMetadata(
        description="Имя пользователя или ссылка для связи с поддержкой.",
        format_hint="Telegram username в формате @username или https://t.me/...",
        example="@bedolaga_support",
        dependencies="SUPPORT_SYSTEM_MODE — режим обработки запросов.",
    ),
    "SUPPORT_TICKET_SLA_MINUTES": SettingMetadata(
        description="Срок (в минутах) для ответа модератора на тикет.",
        format_hint="Целое число от 1 до 1440.",
        example="15",
        unit="минут",
        warning="При превышении времени бот отправит напоминание администраторам.",
    ),
    "TRIAL_DURATION_DAYS": SettingMetadata(
        description="Длительность бесплатного периода подписки.",
        format_hint="Целое число дней.",
        example="3",
        unit="дней",
        recommended="3",
    ),
    "TRIAL_TRAFFIC_LIMIT_GB": SettingMetadata(
        description="Лимит трафика в гигабайтах для триала.",
        format_hint="Целое число",
        example="10",
        unit="ГБ",
    ),
    "TRIAL_DEVICE_LIMIT": SettingMetadata(
        description="Количество устройств, доступных в триале.",
        format_hint="Целое число",
        example="2",
        unit="устройств",
    ),
    "YOOKASSA_ENABLED": SettingMetadata(
        description="Включает прием платежей через YooKassa.",
        format_hint="Переключатель",
        warning="Убедитесь, что Shop ID и секреты заполнены, иначе платежи не будут работать.",
        dependencies="YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_RETURN_URL",
    ),
    "YOOKASSA_SHOP_ID": SettingMetadata(
        description="Идентификатор магазина в YooKassa.",
        format_hint="Строка, как указано в личном кабинете YooKassa.",
        example="123456",
        sensitive=True,
    ),
    "YOOKASSA_SECRET_KEY": SettingMetadata(
        description="Секретный ключ для подписи запросов YooKassa.",
        format_hint="Строка 32-64 символа.",
        example="sk_test_***",
        warning="Храните ключ в секрете, не делитесь им с третьими лицами.",
        sensitive=True,
    ),
    "BASE_SUBSCRIPTION_PRICE": SettingMetadata(
        description="Базовая стоимость тарифа по умолчанию в копейках.",
        format_hint="Введите значение в рублях — бот сконвертирует в копейки.",
        example="990 ₽",
        unit="₽",
    ),
    "AVAILABLE_SUBSCRIPTION_PERIODS": SettingMetadata(
        description="Список доступных периодов подписки в днях.",
        format_hint="Числа через запятую (например, 30,90,180).",
        example="14,30,90",
        dependencies="PRICE_XX_DAYS — стоимость для каждого периода.",
    ),
    "REMNAWAVE_API_URL": SettingMetadata(
        description="Базовый URL RemnaWave API.",
        format_hint="Полный URL, например https://panel.example.com/api.",
        example="https://remnawave.example/api",
        dependencies="REMNAWAVE_API_KEY или REMNAWAVE_USERNAME/REMNAWAVE_PASSWORD",
    ),
    "REMNAWAVE_API_KEY": SettingMetadata(
        description="API-ключ для авторизации в RemnaWave.",
        format_hint="Строка, выданная панелью RemnaWave.",
        example="rw_************************",
        sensitive=True,
    ),
    "REMNAWAVE_SECRET_KEY": SettingMetadata(
        description="Секретный ключ для подписи запросов RemnaWave.",
        sensitive=True,
        warning="Неверный ключ приведет к ошибке синхронизации пользователей.",
    ),
    "ENABLE_NOTIFICATIONS": SettingMetadata(
        description="Глобальный переключатель пользовательских уведомлений.",
        format_hint="Переключатель",
        warning="При выключении пользователи не будут получать напоминания и предупреждения.",
        dependencies="NOTIFICATION_RETRY_ATTEMPTS, NOTIFICATION_CACHE_HOURS",
    ),
    "ADMIN_REPORTS_SEND_TIME": SettingMetadata(
        description="Время отправки ежедневных отчетов администраторам.",
        format_hint="Формат ЧЧ:ММ в часовом поясе бота.",
        example="09:30",
        dependencies="ADMIN_REPORTS_ENABLED должно быть включено.",
    ),
    "WEB_API_ENABLED": SettingMetadata(
        description="Включает административное Web API.",
        format_hint="Переключатель",
        warning="Убедитесь, что настроены токены и ограничения доступа.",
        dependencies="WEB_API_DEFAULT_TOKEN, WEB_API_ALLOWED_ORIGINS",
    ),
    "WEB_API_DEFAULT_TOKEN": SettingMetadata(
        description="Бутстрап токен для доступа к Web API.",
        format_hint="Строка из безопасных символов.",
        example="rw_api_***",
        sensitive=True,
        warning="После смены токена требуется обновить интеграции.",
    ),
    "PAYMENT_SERVICE_NAME": SettingMetadata(
        description="Название сервиса в платежных назначениях.",
        format_hint="Краткий текст 2-32 символа.",
        example="Bedolaga VPN",
    ),
    "TELEGRAM_STARS_ENABLED": SettingMetadata(
        description="Включает оплату через Telegram Stars.",
        format_hint="Переключатель",
        warning="Доступно только при правильной настройке связанных приложений Telegram.",
    ),
    "CRYPTOBOT_ENABLED": SettingMetadata(
        description="Активирует прием платежей через CryptoBot.",
        warning="Проверьте токен и секрет вебхука перед включением.",
    ),
    "PAL24_ENABLED": SettingMetadata(
        description="Включает PayPalych (PAL24).",
        warning="Не забудьте задать токен и параметры вебхука.",
    ),
    "MULENPAY_ENABLED": SettingMetadata(
        description="Активирует MulenPay.",
        warning="Необходимы API KEY и SECRET KEY из панели MulenPay.",
    ),
    "TRIBUTE_ENABLED": SettingMetadata(
        description="Разрешает платежи через Tribute.",
        warning="Убедитесь в корректном API ключе и настроенном вебхуке.",
    ),
}


CATEGORY_SECTION_DESCRIPTIONS: Dict[str, str] = {
    "CHANNEL": "Настройки обязательной подписки и ссылок на канал.",
    "SKIP": "Переключатели упрощенного сценария и пропуска шагов.",
    "CONNECT_BUTTON": "Действие кнопки «Подключиться» и связанные ссылки.",
    "SUPPORT": "Контакты поддержки, тикеты и SLA.",
    "PAYMENT": "Общие шаблоны описаний и назначения платежей.",
    "YOOKASSA": "Интеграция и параметры провайдера YooKassa.",
    "CRYPTOBOT": "Параметры оплаты через CryptoBot.",
    "MULENPAY": "Настройки провайдера MulenPay.",
    "PAL24": "Интеграция PayPalych (PAL24).",
    "TRIBUTE": "Сбор пожертвований через Tribute.",
    "TELEGRAM": "Оплата через Telegram Stars.",
    "PAID_SUBSCRIPTION": "Базовые параметры платных подписок.",
    "PERIODS": "Доступные периоды продления подписки.",
    "SUBSCRIPTION_PRICES": "Цены на подписки по периодам.",
    "TRAFFIC": "Лимиты трафика и стратегии сброса.",
    "TRAFFIC_PACKAGES": "Дополнительные пакеты трафика и их стоимость.",
    "DISCOUNTS": "Промогруппы и скидки.",
    "AUTOPAY": "Настройки автоматического продления подписки.",
    "TRIAL": "Параметры пробного периода.",
    "REFERRAL": "Параметры реферальной программы и уведомлений.",
    "ADMIN_NOTIFICATIONS": "Уведомления администраторам о событиях.",
    "ADMIN_REPORTS": "Автоматические отчеты для администраторов.",
    "NOTIFICATIONS": "Пользовательские уведомления и ретраи.",
    "INTERFACE_BRANDING": "Логотип и визуальные элементы интерфейса.",
    "INTERFACE_SUBSCRIPTION": "Параметры отображения ссылок на подписку.",
    "HAPP": "Ссылки на приложения Happ и CryptoLink.",
    "MINIAPP": "Данные и описание мини-приложения.",
    "LOCALIZATION": "Доступные языки и язык по умолчанию.",
    "DATABASE": "Выбор движка базы данных.",
    "POSTGRES": "Параметры подключения к PostgreSQL.",
    "SQLITE": "Путь к базе SQLite.",
    "REMNAWAVE": "Доступ к RemnaWave API.",
    "SERVER": "Режимы отображения статуса серверов.",
    "MONITORING": "Мониторинг и хранение логов.",
    "MAINTENANCE": "Режим обслуживания и сообщения пользователям.",
    "BACKUP": "Автоматические бэкапы и их отправка.",
    "VERSION": "Проверка обновлений бота.",
    "WEB_API": "Параметры административного Web API.",
    "WEBHOOK": "Настройки входящего вебхука бота.",
    "LOG": "Логирование и уровни логов.",
    "DEBUG": "Режим отладки и дополнительные флаги.",
    "ADDITIONAL": "Файл конфигурации и глубокие ссылки.",
    "REDIS": "Подключение к Redis.",
}


SETTINGS_HISTORY: deque[Dict[str, Any]] = deque(maxlen=10)

PREDEFINED_PRESETS: Dict[str, Dict[str, Any]] = {
    "recommended": {
        "MAINTENANCE_MODE": False,
        "ENABLE_NOTIFICATIONS": True,
        "SUPPORT_TICKET_SLA_MINUTES": 5,
        "TRIAL_DURATION_DAYS": 3,
        "WEB_API_ENABLED": False,
    },
    "minimal": {
        "ENABLE_NOTIFICATIONS": False,
        "SUPPORT_MENU_ENABLED": False,
        "TRIAL_DURATION_DAYS": 0,
        "TRIAL_TRAFFIC_LIMIT_GB": 5,
        "MAINTENANCE_MODE": False,
    },
    "security": {
        "ENABLE_NOTIFICATIONS": True,
        "RESET_TRAFFIC_ON_PAYMENT": True,
        "MAINTENANCE_AUTO_ENABLE": True,
        "WEB_API_ENABLED": False,
        "YOOKASSA_SBP_ENABLED": False,
    },
    "testing": {
        "DEBUG": True,
        "ENABLE_NOTIFICATIONS": False,
        "MAINTENANCE_MODE": True,
        "WEB_API_ENABLED": True,
    },
}

PRESET_TITLES: Dict[str, str] = {
    "recommended": "Рекомендуемые настройки",
    "minimal": "Минимальная конфигурация",
    "security": "Максимальная безопасность",
    "testing": "Для тестирования",
}

CUSTOM_PRESETS: Dict[str, Dict[str, Any]] = {}


def _log_setting_change(key: str, old_value: Any, new_value: Any, source: str) -> None:
    SETTINGS_HISTORY.appendleft(
        {
            "timestamp": datetime.utcnow(),
            "key": key,
            "old": old_value,
            "new": new_value,
            "source": source,
        }
    )


async def _store_setting_context(
    state: FSMContext,
    *,
    key: str,
    group_key: str,
    category_page: int,
    settings_page: int,
) -> None:
    await state.update_data(
        setting_key=key,
        setting_group_key=group_key,
        setting_category_page=category_page,
        setting_settings_page=settings_page,
        botcfg_origin="bot_config",
        botcfg_timestamp=time.time(),
    )


class BotConfigInputFilter(BaseFilter):
    def __init__(self, timeout: float = 300.0) -> None:
        self.timeout = timeout

    async def __call__(
        self,
        message: types.Message,
        state: FSMContext,
    ) -> bool:
        if not message.text or message.text.startswith("/"):
            return False

        if message.chat.type != "private":
            return False

        data = await state.get_data()

        if data.get("botcfg_origin") != "bot_config":
            return False

        if not data.get("setting_key"):
            return False

        timestamp = data.get("botcfg_timestamp")
        if timestamp is None:
            return True

        try:
            return (time.time() - float(timestamp)) <= self.timeout
        except (TypeError, ValueError):
            return False


def _chunk(buttons: Iterable[types.InlineKeyboardButton], size: int) -> Iterable[List[types.InlineKeyboardButton]]:
    buttons_list = list(buttons)
    for index in range(0, len(buttons_list), size):
        yield buttons_list[index : index + size]


def _parse_category_payload(payload: str) -> Tuple[str, str, int, int]:
    parts = payload.split(":")
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    category_key = parts[2] if len(parts) > 2 else ""

    def _safe_int(value: str, default: int = 1) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    category_page = _safe_int(parts[3]) if len(parts) > 3 else 1
    settings_page = _safe_int(parts[4]) if len(parts) > 4 else 1
    return group_key, category_key, category_page, settings_page


def _parse_group_payload(payload: str) -> Tuple[str, int]:
    parts = payload.split(":")
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        page = max(1, int(parts[2]))
    except (IndexError, ValueError):
        page = 1
    return group_key, page


def _get_grouped_categories() -> List[Tuple[str, str, List[Tuple[str, str, int]]]]:
    categories = bot_configuration_service.get_categories()
    categories_map = {key: (label, count) for key, label, count in categories}
    used: set[str] = set()
    grouped: List[Tuple[str, str, List[Tuple[str, str, int]]]] = []

    for menu_category in MENU_CATEGORIES:
        items: List[Tuple[str, str, int]] = []
        for category_key in menu_category.category_keys:
            if category_key in categories_map:
                label, count = categories_map[category_key]
                items.append((category_key, label, count))
                used.add(category_key)
        if items:
            grouped.append((menu_category.key, menu_category.title, items))

    remaining = [
        (key, label, count)
        for key, (label, count) in categories_map.items()
        if key not in used
    ]

    if remaining:
        remaining.sort(key=lambda item: item[1])
        grouped.append((CATEGORY_FALLBACK_KEY, CATEGORY_FALLBACK_TITLE, remaining))

    return grouped


def _resolve_menu_category(menu_key: str) -> Optional[MenuCategoryDefinition]:
    for menu_category in MENU_CATEGORIES:
        if menu_category.key == menu_key:
            return menu_category
    return None


def _collect_menu_sections(
    menu_key: str,
) -> List[Tuple[str, str, List[SettingDefinition]]]:
    sections: List[Tuple[str, str, List[SettingDefinition]]] = []

    if menu_key == CATEGORY_FALLBACK_KEY:
        for category_key, label, _ in bot_configuration_service.get_categories():
            if category_key in ASSIGNED_CATEGORY_KEYS:
                continue
            definitions = bot_configuration_service.get_settings_for_category(category_key)
            if definitions:
                sections.append((category_key, label, definitions))
        return sections

    menu_category = _resolve_menu_category(menu_key)
    if not menu_category:
        return sections

    for category_key in menu_category.category_keys:
        definitions = bot_configuration_service.get_settings_for_category(category_key)
        if definitions:
            sections.append((category_key, definitions[0].category_label, definitions))
    return sections


def _collect_menu_definitions(menu_key: str) -> List[SettingDefinition]:
    sections = _collect_menu_sections(menu_key)
    definitions: List[SettingDefinition] = []
    seen: set[str] = set()

    for _, _, items in sections:
        for definition in items:
            if definition.key in seen:
                continue
            definitions.append(definition)
            seen.add(definition.key)

    definitions.sort(key=lambda definition: definition.display_name.lower())
    return definitions


def _find_menu_key_for_category(category_key: str) -> str:
    for menu_category in MENU_CATEGORIES:
        if category_key in menu_category.category_keys:
            return menu_category.key
    return CATEGORY_FALLBACK_KEY


def _iter_all_definitions() -> Iterable[SettingDefinition]:
    seen: set[str] = set()
    for category_key, _, _ in bot_configuration_service.get_categories():
        for definition in bot_configuration_service.get_settings_for_category(category_key):
            if definition.key in seen:
                continue
            seen.add(definition.key)
            yield definition


def _determine_setting_kind(definition: SettingDefinition) -> SettingKind:
    key_upper = definition.key.upper()

    if bot_configuration_service.has_choices(definition.key):
        return SettingKind.CHOICE

    python_type = definition.python_type

    if python_type is bool:
        return SettingKind.TOGGLE

    if python_type is int:
        if key_upper.startswith("PRICE_") or key_upper.endswith("_KOPEKS"):
            return SettingKind.PRICE
        return SettingKind.NUMBER

    if python_type is float:
        return SettingKind.FLOAT

    if python_type is str:
        if any(keyword in key_upper for keyword in TIME_KEYWORDS):
            return SettingKind.TIME
        if any(keyword in key_upper for keyword in URL_KEYWORDS):
            return SettingKind.URL
        if any(keyword in key_upper for keyword in LIST_KEYWORDS):
            return SettingKind.LIST
        if any(keyword in key_upper for keyword in SENSITIVE_KEYWORDS):
            return SettingKind.SECRET
        return SettingKind.TEXT

    return SettingKind.TEXT


def _format_metadata_value(value: Any, kind: Optional[SettingKind]) -> str:
    if value is None:
        return ""
    if kind == SettingKind.PRICE:
        try:
            rubles = float(value) / 100
            return f"{rubles:.0f}"
        except Exception:
            return str(value)
    if kind == SettingKind.TOGGLE:
        return "вкл" if bool(value) else "выкл"
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item) for item in value)
    return str(value)


def _mask_sensitive(value: Any) -> str:
    text = str(value)
    if not text:
        return "—"
    if len(text) <= 4:
        return "•" * len(text)
    return "•" * (len(text) - 4) + text[-4:]


def _format_price(kopeks: int) -> str:
    try:
        rubles = int(kopeks) / 100
        formatted = f"{rubles:,.0f}".replace(",", " ")
        return f"{formatted} ₽"
    except Exception:
        return str(kopeks)


def _to_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


def _format_setting_value(
    definition: SettingDefinition,
    value: Any,
    metadata: SettingMetadata,
    *,
    short: bool = False,
) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "—"

    kind = _determine_setting_kind(definition)

    if metadata.sensitive or kind == SettingKind.SECRET:
        return _mask_sensitive(value)

    if kind == SettingKind.TOGGLE:
        return "ВКЛЮЧЕНО" if bool(value) else "ВЫКЛЮЧЕНО"

    if kind == SettingKind.PRICE:
        try:
            return _format_price(int(value))
        except Exception:
            return str(value)

    if kind == SettingKind.NUMBER:
        return f"{value} {metadata.unit}".strip() if metadata.unit else str(value)

    if kind == SettingKind.FLOAT:
        try:
            return f"{float(value):g}"
        except Exception:
            return str(value)

    if kind == SettingKind.TIME:
        return str(value)

    if kind == SettingKind.URL:
        text = str(value)
        return text if short else html.escape(text)

    if kind == SettingKind.LIST:
        items = _to_list(value)
        if short:
            preview = ", ".join(items[:3])
            if len(items) > 3:
                preview += " …"
            return preview or "—"
        return "\n".join(f"• {html.escape(item)}" for item in items) or "—"

    if kind == SettingKind.CHOICE:
        return html.escape(str(value))

    return html.escape(str(value)) if not short else str(value)


def _setting_status_icon(definition: SettingDefinition, value: Any) -> str:
    kind = _determine_setting_kind(definition)
    if kind == SettingKind.TOGGLE:
        return "✅" if bool(value) else "❌"
    if value is None or (isinstance(value, str) and not value.strip()):
        return "⚠️"
    if bot_configuration_service.has_override(definition.key):
        return "🛠"
    return "📌"


def _get_setting_metadata(
    key: str,
    definition: Optional[SettingDefinition] = None,
) -> SettingMetadata:
    override = SETTING_METADATA_OVERRIDES.get(key)
    if override:
        return replace(override)

    if definition is None:
        try:
            definition = bot_configuration_service.get_definition(key)
        except KeyError:
            definition = None

    kind: Optional[SettingKind] = None
    if definition is not None:
        kind = _determine_setting_kind(definition)

    description = ""
    format_hint = ""
    example = ""
    warning = ""
    dependencies = ""
    unit = None

    if kind == SettingKind.TOGGLE:
        description = "Переключатель функциональности."
        format_hint = "Используйте <code>вкл</code>/<code>выкл</code> или <code>on</code>/<code>off</code>."
    elif kind == SettingKind.PRICE:
        description = "Стоимость в копейках."
        format_hint = "Введите значение в рублях, бот автоматически переведет в копейки."
        unit = "₽"
    elif kind == SettingKind.LIST:
        description = "Список значений через запятую."
        format_hint = "Разделяйте значения запятыми, пробелы допустимы."
    elif kind == SettingKind.TIME:
        description = "Время в формате ЧЧ:ММ."
        format_hint = "Используйте 24-часовой формат, например 09:30."
    elif kind == SettingKind.URL:
        description = "URL или ссылка."
        format_hint = "Введите полный адрес, начинающийся с http:// или https://."
    elif kind == SettingKind.NUMBER:
        description = "Целочисленное значение."
        format_hint = "Введите целое число."
    elif kind == SettingKind.FLOAT:
        description = "Число с плавающей точкой."
        format_hint = "Используйте точку или запятую в качестве разделителя дробной части."
    elif kind == SettingKind.TEXT:
        description = "Текстовое значение."
        format_hint = "Любая строка, максимум 1024 символа."
    elif kind == SettingKind.CHOICE:
        description = "Выбор из доступных вариантов."
        format_hint = "Выберите значение из предложенных кнопок."

    original_value = None
    if definition is not None:
        original_value = bot_configuration_service.get_original_value(key)
        if not example:
            example = _format_metadata_value(original_value, kind)

    metadata = SettingMetadata(
        description=description or (f"Параметр <b>{key}</b>." if not definition else f"Параметр {definition.display_name}.") ,
        format_hint=format_hint,
        example=example,
        warning=warning,
        dependencies=dependencies,
        recommended=_format_metadata_value(original_value, kind) or None,
        sensitive=(kind == SettingKind.SECRET),
        unit=unit,
    )

    return metadata


def _summarize_definitions(definitions: List[SettingDefinition]) -> Dict[str, Any]:
    total = len(definitions)
    overrides = 0
    missing_required = 0
    disabled_flags = 0
    enabled_flags = 0
    issues: List[str] = []

    for definition in definitions:
        value = bot_configuration_service.get_current_value(definition.key)
        kind = _determine_setting_kind(definition)

        if bot_configuration_service.has_override(definition.key):
            overrides += 1

        if kind == SettingKind.TOGGLE:
            if bool(value):
                enabled_flags += 1
            else:
                disabled_flags += 1
                if definition.key.endswith("ENABLED") or "ENABLE" in definition.key:
                    issues.append(f"{definition.display_name}: выключено")
        else:
            if not definition.is_optional:
                if value is None:
                    missing_required += 1
                    issues.append(f"{definition.display_name}: не заполнено")
                elif isinstance(value, str) and not value.strip():
                    missing_required += 1
                    issues.append(f"{definition.display_name}: пустое значение")

    if missing_required:
        status_icon = "🔴"
        status_text = f"{missing_required} критически важных параметров не заполнено"
    elif disabled_flags and not enabled_flags:
        status_icon = "🟡"
        status_text = "Все функции категории выключены"
    elif disabled_flags:
        status_icon = "🟡"
        status_text = "Некоторые функции отключены"
    else:
        status_icon = "🟢"
        status_text = "Всё настроено корректно"

    return {
        "total": total,
        "overrides": overrides,
        "enabled_flags": enabled_flags,
        "disabled_flags": disabled_flags,
        "missing_required": missing_required,
        "issues": issues,
        "status_icon": status_icon,
        "status_text": status_text,
    }


def _summarize_menu_category(menu_key: str) -> Dict[str, Any]:
    definitions = _collect_menu_definitions(menu_key)
    return _summarize_definitions(definitions)


def _build_groups_keyboard() -> types.InlineKeyboardMarkup:
    grouped = _get_grouped_categories()
    rows: list[list[types.InlineKeyboardButton]] = []
    buttons: list[types.InlineKeyboardButton] = []

    for group_key, title, items in grouped:
        summary = _summarize_menu_category(group_key)
        total = summary["total"] or sum(count for _, _, count in items)
        button_text = f"{summary['status_icon']} {title} · {total}"
        buttons.append(
            types.InlineKeyboardButton(
                text=button_text,
                callback_data=f"botcfg_group:{group_key}:1",
            )
        )

    for chunk in _chunk(buttons, 2):
        rows.append(list(chunk))

    rows.append(
        [
            types.InlineKeyboardButton(
                text="🔍 Найти настройку",
                callback_data="botcfg_search",
            ),
            types.InlineKeyboardButton(
                text="📊 История изменений",
                callback_data="botcfg_history",
            ),
        ]
    )
    rows.append(
        [
            types.InlineKeyboardButton(
                text="🎛 Пресеты",
                callback_data="botcfg_presets",
            ),
            types.InlineKeyboardButton(
                text="📤 Экспорт .env",
                callback_data="botcfg_export",
            ),
        ]
    )
    rows.append(
        [
            types.InlineKeyboardButton(
                text="📥 Импорт .env",
                callback_data="botcfg_import",
            ),
            types.InlineKeyboardButton(
                text="❓ Помощь",
                callback_data="botcfg_help",
            ),
        ]
    )
    rows.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="admin_submenu_settings",
            )
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _render_main_menu_text() -> str:
    grouped = _get_grouped_categories()
    total_settings = sum(sum(count for _, _, count in items) for _, _, items in grouped)
    lines = [
        "⚙️ <b>Панель управления ботом</b>",
        "Главная → Разделы",
        "",
        f"Всего параметров: <b>{total_settings}</b>",
        "Выберите категорию, чтобы открыть настройки:",
        "",
    ]

    for group_key, title, _ in grouped:
        summary = _summarize_menu_category(group_key)
        menu_category = _resolve_menu_category(group_key)
        description = menu_category.description if menu_category else "Дополнительные параметры"
        overrides = summary["overrides"]
        lines.append(
            (
                f"{summary['status_icon']} <b>{title}</b> — {description}\n"
                f"   Настроек: {summary['total']} · Переопределено: {overrides}"
            )
        )
    lines.append("")
    lines.append("🔍 Используйте поиск, чтобы быстро найти нужный параметр.")
    lines.append("💡 Пресеты помогут применить готовые конфигурации в пару кликов.")

    return "\n".join(lines)


def _render_group_text(group_key: str, group_title: str) -> str:
    summary = _summarize_menu_category(group_key)
    menu_category = _resolve_menu_category(group_key)
    description = menu_category.description if menu_category else "Дополнительные параметры"

    lines = [
        "⚙️ <b>Панель управления ботом</b>",
        f"Главная → {group_title}",
        "",
        f"{summary['status_icon']} <b>{group_title}</b>",
        description,
        "",
        f"Настроек: {summary['total']} · Переопределено: {summary['overrides']}",
    ]

    if summary["issues"]:
        lines.append("")
        lines.append("⚠️ Требует внимания:")
        for issue in summary["issues"][:5]:
            lines.append(f"• {issue}")

    lines.append("")
    lines.append("Выберите подкатегорию:")

    return "\n".join(lines)


def _render_category_text(
    group_key: str,
    category_key: str,
    category_label: str,
    definitions: List[SettingDefinition],
) -> str:
    menu_category = _resolve_menu_category(group_key)
    breadcrumb = (
        f"Главная → {menu_category.title if menu_category else CATEGORY_FALLBACK_TITLE} → {category_label}"
    )
    summary = _summarize_definitions(definitions)
    description = CATEGORY_SECTION_DESCRIPTIONS.get(
        category_key,
        f"Настройки блока «{category_label}».",
    )

    lines = [
        "⚙️ <b>Панель управления ботом</b>",
        breadcrumb,
        "",
        f"{summary['status_icon']} <b>{category_label}</b>",
        description,
        "",
        f"Настроек: {summary['total']} · Переопределено: {summary['overrides']}",
    ]

    if summary["issues"]:
        lines.append("")
        lines.append("⚠️ Требует внимания:")
        for issue in summary["issues"][:5]:
            lines.append(f"• {issue}")

    lines.append("")
    lines.append("Выберите настройку для просмотра и изменения:")

    return "\n".join(lines)


def _build_categories_keyboard(
    group_key: str,
    group_title: str,
    categories: List[Tuple[str, str, int]],
    page: int = 1,
) -> types.InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(categories) / CATEGORY_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * CATEGORY_PAGE_SIZE
    end = start + CATEGORY_PAGE_SIZE
    sliced = categories[start:end]

    rows: list[list[types.InlineKeyboardButton]] = []
    rows.append(
        [
            types.InlineKeyboardButton(
                text=f"— {group_title} —",
                callback_data="botcfg_group:noop",
            )
        ]
    )

    buttons: List[types.InlineKeyboardButton] = []
    for category_key, label, count in sliced:
        definitions = bot_configuration_service.get_settings_for_category(category_key)
        summary = _summarize_definitions(definitions)
        overrides = summary["overrides"]
        button_text = f"{summary['status_icon']} {label} · {summary['total']}"
        if overrides:
            button_text += f" • ⚙️{overrides}"
        buttons.append(
            types.InlineKeyboardButton(
                text=button_text,
                callback_data=f"botcfg_cat:{group_key}:{category_key}:{page}:1",
            )
        )

    for chunk in _chunk(buttons, 2):
        rows.append(list(chunk))

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"botcfg_group:{group_key}:{page - 1}",
                )
            )
        nav_row.append(
            types.InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data="botcfg_group:noop",
            )
        )
        if page < total_pages:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"botcfg_group:{group_key}:{page + 1}",
                )
            )
        rows.append(nav_row)

    rows.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ К разделам",
                callback_data="admin_bot_config",
            )
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_settings_keyboard(
    category_key: str,
    group_key: str,
    category_page: int,
    language: str,
    page: int = 1,
) -> types.InlineKeyboardMarkup:
    definitions = bot_configuration_service.get_settings_for_category(category_key)
    total_pages = max(1, math.ceil(len(definitions) / SETTINGS_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * SETTINGS_PAGE_SIZE
    end = start + SETTINGS_PAGE_SIZE
    sliced = definitions[start:end]

    rows: list[list[types.InlineKeyboardButton]] = []
    texts = get_texts(language)

    if category_key == "REMNAWAVE":
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🔌 Проверить подключение",
                    callback_data=(
                        f"botcfg_test_remnawave:{group_key}:{category_key}:{category_page}:{page}"
                    ),
                )
            ]
        )

    test_payment_buttons: list[list[types.InlineKeyboardButton]] = []

    def _test_button(text: str, method: str) -> types.InlineKeyboardButton:
        return types.InlineKeyboardButton(
            text=text,
            callback_data=(
                f"botcfg_test_payment:{method}:{group_key}:{category_key}:{category_page}:{page}"
            ),
        )

    if category_key == "YOOKASSA":
        label = texts.t("PAYMENT_CARD_YOOKASSA", "💳 Банковская карта (YooKassa)")
        test_payment_buttons.append([_test_button(f"{label} · тест", "yookassa")])
    elif category_key == "TRIBUTE":
        label = texts.t("PAYMENT_CARD_TRIBUTE", "💳 Банковская карта (Tribute)")
        test_payment_buttons.append([_test_button(f"{label} · тест", "tribute")])
    elif category_key == "MULENPAY":
        label = texts.t("PAYMENT_CARD_MULENPAY", "💳 Банковская карта (Mulen Pay)")
        test_payment_buttons.append([_test_button(f"{label} · тест", "mulenpay")])
    elif category_key == "PAL24":
        label = texts.t("PAYMENT_CARD_PAL24", "💳 Банковская карта (PayPalych)")
        test_payment_buttons.append([_test_button(f"{label} · тест", "pal24")])
    elif category_key == "TELEGRAM":
        label = texts.t("PAYMENT_TELEGRAM_STARS", "⭐ Telegram Stars")
        test_payment_buttons.append([_test_button(f"{label} · тест", "stars")])
    elif category_key == "CRYPTOBOT":
        label = texts.t("PAYMENT_CRYPTOBOT", "🪙 Криптовалюта (CryptoBot)")
        test_payment_buttons.append([_test_button(f"{label} · тест", "cryptobot")])

    if test_payment_buttons:
        rows.extend(test_payment_buttons)

    for definition in sliced:
        value = bot_configuration_service.get_current_value(definition.key)
        metadata = _get_setting_metadata(definition.key, definition)
        value_preview = _format_setting_value(
            definition,
            value,
            metadata,
            short=True,
        )
        status_icon = _setting_status_icon(definition, value)
        button_text = f"{status_icon} {definition.display_name} · {value_preview}"
        if len(button_text) > 64:
            button_text = button_text[:63] + "…"
        callback_token = bot_configuration_service.get_callback_token(definition.key)
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=button_text,
                    callback_data=(
                        f"botcfg_setting:{group_key}:{category_page}:{page}:{callback_token}"
                    ),
                )
            ]
        )

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="⬅️",
                    callback_data=(
                        f"botcfg_cat:{group_key}:{category_key}:{category_page}:{page - 1}"
                    ),
                )
            )
        nav_row.append(
            types.InlineKeyboardButton(
                text=f"{page}/{total_pages}", callback_data="botcfg_cat_page:noop"
            )
        )
        if page < total_pages:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="➡️",
                    callback_data=(
                        f"botcfg_cat:{group_key}:{category_key}:{category_page}:{page + 1}"
                    ),
                )
            )
        rows.append(nav_row)

    rows.append([
        types.InlineKeyboardButton(
            text="⬅️ К категориям",
            callback_data=f"botcfg_group:{group_key}:{category_page}",
        )
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_setting_keyboard(
    key: str,
    group_key: str,
    category_page: int,
    settings_page: int,
) -> types.InlineKeyboardMarkup:
    definition = bot_configuration_service.get_definition(key)
    rows: list[list[types.InlineKeyboardButton]] = []
    callback_token = bot_configuration_service.get_callback_token(key)
    metadata = _get_setting_metadata(key, definition)
    current_value = bot_configuration_service.get_current_value(key)
    kind = _determine_setting_kind(definition)

    choice_options = bot_configuration_service.get_choice_options(key)
    if choice_options:
        current_value = bot_configuration_service.get_current_value(key)
        choice_buttons: list[types.InlineKeyboardButton] = []
        for option in choice_options:
            choice_token = bot_configuration_service.get_choice_token(key, option.value)
            if choice_token is None:
                continue
            button_text = option.label
            if current_value == option.value and not button_text.startswith("✅"):
                button_text = f"✅ {button_text}"
            choice_buttons.append(
                types.InlineKeyboardButton(
                    text=button_text,
                    callback_data=(
                        f"botcfg_choice:{group_key}:{category_page}:{settings_page}:{callback_token}:{choice_token}"
                    ),
                )
            )

        for chunk in _chunk(choice_buttons, 2):
            rows.append(list(chunk))

    if kind == SettingKind.TOGGLE:
        rows.append([
            types.InlineKeyboardButton(
                text="✅ Включить",
                callback_data=(
                    f"botcfg_bool:{group_key}:{category_page}:{settings_page}:{callback_token}:1"
                ),
            ),
            types.InlineKeyboardButton(
                text="❌ Выключить",
                callback_data=(
                    f"botcfg_bool:{group_key}:{category_page}:{settings_page}:{callback_token}:0"
                ),
            ),
        ])
        rows.append([
            types.InlineKeyboardButton(
                text="🔁 Переключить",
                callback_data=(
                    f"botcfg_toggle:{group_key}:{category_page}:{settings_page}:{callback_token}"
                ),
            )
        ])

    rows.append([
        types.InlineKeyboardButton(
            text="✏️ Изменить",
            callback_data=(
                f"botcfg_edit:{group_key}:{category_page}:{settings_page}:{callback_token}"
            ),
        )
    ])

    if metadata.recommended and metadata.recommended.strip():
        rows.append([
            types.InlineKeyboardButton(
                text="💡 Рекомендуемое",
                callback_data=(
                    f"botcfg_apply_rec:{group_key}:{category_page}:{settings_page}:{callback_token}"
                ),
            )
        ])

    if kind == SettingKind.LIST:
        rows.append([
            types.InlineKeyboardButton(
                text="➕ Добавить",
                callback_data=(
                    f"botcfg_list_add:{group_key}:{category_page}:{settings_page}:{callback_token}"
                ),
            ),
            types.InlineKeyboardButton(
                text="➖ Удалить",
                callback_data=(
                    f"botcfg_list_remove:{group_key}:{category_page}:{settings_page}:{callback_token}"
                ),
            ),
        ])

    rows.append([
        types.InlineKeyboardButton(
            text="📋 Копировать из…",
            callback_data=(
                f"botcfg_copy:{group_key}:{category_page}:{settings_page}:{callback_token}"
            ),
        )
    ])

    if bot_configuration_service.has_override(key):
        rows.append([
            types.InlineKeyboardButton(
                text="♻️ Сбросить",
                callback_data=(
                    f"botcfg_reset:{group_key}:{category_page}:{settings_page}:{callback_token}"
                ),
            )
        ])

    rows.append([
        types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=(
                f"botcfg_cat:{group_key}:{definition.category_key}:{category_page}:{settings_page}"
            ),
        )
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _render_setting_text(key: str) -> str:
    summary = bot_configuration_service.get_setting_summary(key)
    definition = bot_configuration_service.get_definition(key)
    metadata = _get_setting_metadata(key, definition)
    current_value = bot_configuration_service.get_current_value(key)
    original_value = bot_configuration_service.get_original_value(key)
    category_key = summary["category_key"]
    group_key = _find_menu_key_for_category(category_key)
    menu_category = _resolve_menu_category(group_key)

    current_display = _format_setting_value(definition, current_value, metadata, short=False)
    original_display = (
        _format_setting_value(definition, original_value, metadata, short=False)
        if original_value not in (None, "")
        else "—"
    )
    status_icon = _setting_status_icon(definition, current_value)

    lines = [
        "⚙️ <b>Панель управления ботом</b>",
        (
            f"Главная → {menu_category.title if menu_category else CATEGORY_FALLBACK_TITLE} → "
            f"{summary['category_label']} → {summary['name']}"
        ),
        "",
        f"{status_icon} <b>{summary['name']}</b>",
        metadata.description or f"Параметр {summary['name']}.",
        "",
        f"🔑 <b>Ключ:</b> <code>{summary['key']}</code>",
        f"🧩 <b>Категория:</b> {summary['category_label']}",
        f"🧷 <b>Тип:</b> {summary['type']}",
        "",
        f"{status_icon} <b>Текущее значение:</b> {current_display}",
        f"📦 <b>По умолчанию:</b> {original_display}",
        f"🛠 <b>Переопределено:</b> {'Да' if summary['has_override'] else 'Нет'}",
    ]

    if metadata.recommended and metadata.recommended.strip():
        try:
            recommended_value = bot_configuration_service.parse_user_value(
                key, metadata.recommended
            )
            recommended_display = _format_setting_value(
                definition, recommended_value, metadata, short=False
            )
        except ValueError:
            recommended_display = metadata.recommended
        lines.append(f"💡 <b>Рекомендуемое:</b> {recommended_display}")

    if metadata.format_hint:
        lines.append("")
        lines.append(f"ℹ️ <b>Формат:</b> {metadata.format_hint}")

    if metadata.unit:
        lines.append(f"📏 <b>Единицы измерения:</b> {metadata.unit}")

    if metadata.dependencies:
        lines.append(f"🔗 <b>Связанные параметры:</b> {metadata.dependencies}")

    if metadata.warning:
        lines.append(f"⚠️ <b>Предупреждение:</b> {metadata.warning}")

    if metadata.doc_link:
        lines.append(f"📘 <a href=\"{metadata.doc_link}\">Документация</a>")

    choices = bot_configuration_service.get_choice_options(key)
    if choices:
        current_raw = bot_configuration_service.get_current_value(key)
        lines.append("")
        lines.append("<b>Доступные значения:</b>")
        for option in choices:
            marker = "✅" if current_raw == option.value else "•"
            value_display = html.escape(str(option.value))
            description = option.description or ""
            if description:
                lines.append(
                    f"{marker} {option.label} — <code>{value_display}</code>\n   {description}"
                )
            else:
                lines.append(f"{marker} {option.label} — <code>{value_display}</code>")

    if metadata.highlight:
        lines.append("")
        lines.append(f"✨ <b>{metadata.highlight}</b>")

    return "\n".join(lines)


@admin_required
@error_handler
async def show_bot_config_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    keyboard = _build_groups_keyboard()
    text = _render_main_menu_text()
    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def show_bot_config_group(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    group_key, page = _parse_group_payload(callback.data)
    grouped = _get_grouped_categories()
    group_lookup = {key: (title, items) for key, title, items in grouped}

    if group_key not in group_lookup:
        await callback.answer("Эта группа больше недоступна", show_alert=True)
        return

    group_title, items = group_lookup[group_key]
    keyboard = _build_categories_keyboard(group_key, group_title, items, page)
    text = _render_group_text(group_key, group_title)
    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def show_bot_config_category(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    group_key, category_key, category_page, settings_page = _parse_category_payload(
        callback.data
    )
    definitions = bot_configuration_service.get_settings_for_category(category_key)

    if not definitions:
        await callback.answer("В этой категории пока нет настроек", show_alert=True)
        return

    category_label = definitions[0].category_label
    keyboard = _build_settings_keyboard(
        category_key,
        group_key,
        category_page,
        db_user.language,
        settings_page,
    )
    text = _render_category_text(group_key, category_key, category_label, definitions)
    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def test_remnawave_connection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split(":", 5)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    category_key = parts[2] if len(parts) > 2 else "REMNAWAVE"

    try:
        category_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        category_page = 1

    try:
        settings_page = max(1, int(parts[4])) if len(parts) > 4 else 1
    except ValueError:
        settings_page = 1

    service = RemnaWaveService()
    result = await service.test_api_connection()

    status = result.get("status")
    message: str

    if status == "connected":
        message = "✅ Подключение успешно"
    elif status == "not_configured":
        message = f"⚠️ {result.get('message', 'RemnaWave API не настроен')}"
    else:
        base_message = result.get("message", "Ошибка подключения")
        status_code = result.get("status_code")
        if status_code:
            message = f"❌ {base_message} (HTTP {status_code})"
        else:
            message = f"❌ {base_message}"

    definitions = bot_configuration_service.get_settings_for_category(category_key)
    if definitions:
        keyboard = _build_settings_keyboard(
            category_key,
            group_key,
            category_page,
            db_user.language,
            settings_page,
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=keyboard)
        except Exception:
            # ignore inability to refresh markup, main result shown in alert
            pass

    await callback.answer(message, show_alert=True)


@admin_required
@error_handler
async def test_payment_provider(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split(":", 6)
    method = parts[1] if len(parts) > 1 else ""
    group_key = parts[2] if len(parts) > 2 else CATEGORY_FALLBACK_KEY
    category_key = parts[3] if len(parts) > 3 else "PAYMENT"

    try:
        category_page = max(1, int(parts[4])) if len(parts) > 4 else 1
    except ValueError:
        category_page = 1

    try:
        settings_page = max(1, int(parts[5])) if len(parts) > 5 else 1
    except ValueError:
        settings_page = 1

    language = db_user.language
    texts = get_texts(language)
    payment_service = PaymentService(callback.bot)

    message_text: str

    async def _refresh_markup() -> None:
        definitions = bot_configuration_service.get_settings_for_category(category_key)
        if definitions:
            keyboard = _build_settings_keyboard(
                category_key,
                group_key,
                category_page,
                language,
                settings_page,
            )
            try:
                await callback.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:
                pass

    if method == "yookassa":
        if not settings.is_yookassa_enabled():
            await callback.answer("❌ YooKassa отключена", show_alert=True)
            return

        amount_kopeks = 10 * 100
        description = settings.get_balance_payment_description(amount_kopeks)
        payment_result = await payment_service.create_yookassa_payment(
            db=db,
            user_id=db_user.id,
            amount_kopeks=amount_kopeks,
            description=f"Тестовый платеж (админ): {description}",
            metadata={
                "user_telegram_id": str(db_user.telegram_id),
                "purpose": "admin_test_payment",
                "provider": "yookassa",
            },
        )

        if not payment_result or not payment_result.get("confirmation_url"):
            await callback.answer("❌ Не удалось создать тестовый платеж YooKassa", show_alert=True)
            await _refresh_markup()
            return

        confirmation_url = payment_result["confirmation_url"]
        message_text = (
            "🧪 <b>Тестовый платеж YooKassa</b>\n\n"
            f"💰 Сумма: {texts.format_price(amount_kopeks)}\n"
            f"🆔 ID: {payment_result['yookassa_payment_id']}"
        )
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="💳 Оплатить картой",
                        url=confirmation_url,
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text="📊 Проверить статус",
                        callback_data=f"check_yookassa_{payment_result['local_payment_id']}",
                    )
                ],
            ]
        )
        await callback.message.answer(message_text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer("✅ Ссылка на платеж YooKassa отправлена", show_alert=True)
        await _refresh_markup()
        return

    if method == "tribute":
        if not settings.TRIBUTE_ENABLED:
            await callback.answer("❌ Tribute отключен", show_alert=True)
            return

        tribute_service = TributeService(callback.bot)
        try:
            payment_url = await tribute_service.create_payment_link(
                user_id=db_user.telegram_id,
                amount_kopeks=10 * 100,
                description="Тестовый платеж Tribute (админ)",
            )
        except Exception:
            payment_url = None

        if not payment_url:
            await callback.answer("❌ Не удалось создать платеж Tribute", show_alert=True)
            await _refresh_markup()
            return

        message_text = (
            "🧪 <b>Тестовый платеж Tribute</b>\n\n"
            f"💰 Сумма: {texts.format_price(10 * 100)}\n"
            "🔗 Нажмите кнопку ниже, чтобы открыть ссылку на оплату."
        )
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="💳 Перейти к оплате",
                        url=payment_url,
                    )
                ]
            ]
        )
        await callback.message.answer(message_text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer("✅ Ссылка на платеж Tribute отправлена", show_alert=True)
        await _refresh_markup()
        return

    if method == "mulenpay":
        if not settings.is_mulenpay_enabled():
            await callback.answer("❌ MulenPay отключен", show_alert=True)
            return

        amount_kopeks = 1 * 100
        payment_result = await payment_service.create_mulenpay_payment(
            db=db,
            user_id=db_user.id,
            amount_kopeks=amount_kopeks,
            description="Тестовый платеж MulenPay (админ)",
            language=language,
        )

        if not payment_result or not payment_result.get("payment_url"):
            await callback.answer("❌ Не удалось создать платеж MulenPay", show_alert=True)
            await _refresh_markup()
            return

        payment_url = payment_result["payment_url"]
        message_text = (
            "🧪 <b>Тестовый платеж MulenPay</b>\n\n"
            f"💰 Сумма: {texts.format_price(amount_kopeks)}\n"
            f"🆔 ID: {payment_result['mulen_payment_id']}"
        )
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="💳 Перейти к оплате",
                        url=payment_url,
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text="📊 Проверить статус",
                        callback_data=f"check_mulenpay_{payment_result['local_payment_id']}",
                    )
                ],
            ]
        )
        await callback.message.answer(message_text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer("✅ Ссылка на платеж MulenPay отправлена", show_alert=True)
        await _refresh_markup()
        return

    if method == "pal24":
        if not settings.is_pal24_enabled():
            await callback.answer("❌ PayPalych отключен", show_alert=True)
            return

        amount_kopeks = 10 * 100
        payment_result = await payment_service.create_pal24_payment(
            db=db,
            user_id=db_user.id,
            amount_kopeks=amount_kopeks,
            description="Тестовый платеж PayPalych (админ)",
            language=language or "ru",
        )

        if not payment_result:
            await callback.answer("❌ Не удалось создать платеж PayPalych", show_alert=True)
            await _refresh_markup()
            return

        sbp_url = (
            payment_result.get("sbp_url")
            or payment_result.get("transfer_url")
            or payment_result.get("link_url")
        )
        card_url = payment_result.get("card_url")
        fallback_url = payment_result.get("link_page_url") or payment_result.get("link_url")

        if not (sbp_url or card_url or fallback_url):
            await callback.answer("❌ Не удалось создать платеж PayPalych", show_alert=True)
            await _refresh_markup()
            return

        if not sbp_url:
            sbp_url = fallback_url

        default_sbp_text = texts.t(
            "PAL24_SBP_PAY_BUTTON",
            "🏦 Оплатить через PayPalych (СБП)",
        )
        sbp_button_text = settings.get_pal24_sbp_button_text(default_sbp_text)

        default_card_text = texts.t(
            "PAL24_CARD_PAY_BUTTON",
            "💳 Оплатить банковской картой (PayPalych)",
        )
        card_button_text = settings.get_pal24_card_button_text(default_card_text)

        pay_rows: list[list[types.InlineKeyboardButton]] = []
        if sbp_url:
            pay_rows.append([
                types.InlineKeyboardButton(
                    text=sbp_button_text,
                    url=sbp_url,
                )
            ])

        if card_url and card_url != sbp_url:
            pay_rows.append([
                types.InlineKeyboardButton(
                    text=card_button_text,
                    url=card_url,
                )
            ])

        if not pay_rows and fallback_url:
            pay_rows.append([
                types.InlineKeyboardButton(
                    text=sbp_button_text,
                    url=fallback_url,
                )
            ])

        message_text = (
            "🧪 <b>Тестовый платеж PayPalych</b>\n\n"
            f"💰 Сумма: {texts.format_price(amount_kopeks)}\n"
            f"🆔 Bill ID: {payment_result['bill_id']}"
        )
        keyboard_rows = pay_rows + [
            [
                types.InlineKeyboardButton(
                    text="📊 Проверить статус",
                    callback_data=f"check_pal24_{payment_result['local_payment_id']}",
                )
            ],
        ]

        reply_markup = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
        await callback.message.answer(message_text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer("✅ Ссылка на платеж PayPalych отправлена", show_alert=True)
        await _refresh_markup()
        return

    if method == "stars":
        if not settings.TELEGRAM_STARS_ENABLED:
            await callback.answer("❌ Telegram Stars отключены", show_alert=True)
            return

        stars_rate = settings.get_stars_rate()
        amount_kopeks = max(1, int(round(stars_rate * 100)))
        payload = f"admin_stars_test_{db_user.id}_{int(time.time())}"
        try:
            invoice_link = await payment_service.create_stars_invoice(
                amount_kopeks=amount_kopeks,
                description="Тестовый платеж Telegram Stars (админ)",
                payload=payload,
            )
        except Exception:
            invoice_link = None

        if not invoice_link:
            await callback.answer("❌ Не удалось создать платеж Telegram Stars", show_alert=True)
            await _refresh_markup()
            return

        stars_amount = TelegramStarsService.calculate_stars_from_rubles(amount_kopeks / 100)
        message_text = (
            "🧪 <b>Тестовый платеж Telegram Stars</b>\n\n"
            f"💰 Сумма: {texts.format_price(amount_kopeks)}\n"
            f"⭐ К оплате: {stars_amount}"
        )
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t("PAYMENT_TELEGRAM_STARS", "⭐ Открыть счет"),
                        url=invoice_link,
                    )
                ]
            ]
        )
        await callback.message.answer(message_text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer("✅ Ссылка на платеж Stars отправлена", show_alert=True)
        await _refresh_markup()
        return

    if method == "cryptobot":
        if not settings.is_cryptobot_enabled():
            await callback.answer("❌ CryptoBot отключен", show_alert=True)
            return

        amount_rubles = 100.0
        try:
            current_rate = await currency_converter.get_usd_to_rub_rate()
        except Exception:
            current_rate = None

        if not current_rate or current_rate <= 0:
            current_rate = 100.0

        amount_usd = round(amount_rubles / current_rate, 2)
        if amount_usd < 1:
            amount_usd = 1.0

        payment_result = await payment_service.create_cryptobot_payment(
            db=db,
            user_id=db_user.id,
            amount_usd=amount_usd,
            asset=settings.CRYPTOBOT_DEFAULT_ASSET,
            description=f"Тестовый платеж CryptoBot {amount_rubles:.0f} ₽ ({amount_usd:.2f} USD)",
            payload=f"admin_cryptobot_test_{db_user.id}_{int(time.time())}",
        )

        if not payment_result:
            await callback.answer("❌ Не удалось создать платеж CryptoBot", show_alert=True)
            await _refresh_markup()
            return

        payment_url = (
            payment_result.get("bot_invoice_url")
            or payment_result.get("mini_app_invoice_url")
            or payment_result.get("web_app_invoice_url")
        )

        if not payment_url:
            await callback.answer("❌ Не удалось получить ссылку на оплату CryptoBot", show_alert=True)
            await _refresh_markup()
            return

        amount_kopeks = int(amount_rubles * 100)
        message_text = (
            "🧪 <b>Тестовый платеж CryptoBot</b>\n\n"
            f"💰 Сумма к зачислению: {texts.format_price(amount_kopeks)}\n"
            f"💵 К оплате: {amount_usd:.2f} USD\n"
            f"🪙 Актив: {payment_result['asset']}"
        )
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(text="🪙 Открыть счет", url=payment_url)
                ],
                [
                    types.InlineKeyboardButton(
                        text="📊 Проверить статус",
                        callback_data=f"check_cryptobot_{payment_result['local_payment_id']}",
                    )
                ],
            ]
        )
        await callback.message.answer(message_text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer("✅ Ссылка на платеж CryptoBot отправлена", show_alert=True)
        await _refresh_markup()
        return

    await callback.answer("❌ Неизвестный способ тестирования платежа", show_alert=True)
    await _refresh_markup()


@admin_required
@error_handler
async def show_bot_config_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 4)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return
    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer()
    await callback.answer()


@admin_required
@error_handler
async def start_edit_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 4)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return
    definition = bot_configuration_service.get_definition(key)

    summary = bot_configuration_service.get_setting_summary(key)
    texts = get_texts(db_user.language)

    instructions = [
        "✏️ <b>Редактирование настройки</b>",
        f"Название: {summary['name']}",
        f"Ключ: <code>{summary['key']}</code>",
        f"Тип: {summary['type']}",
        f"Текущее значение: {summary['current']}",
        "\nОтправьте новое значение сообщением.",
    ]

    if definition.is_optional:
        instructions.append("Отправьте 'none' или оставьте пустым для сброса на значение по умолчанию.")

    instructions.append("Для отмены отправьте 'cancel'.")

    await callback.message.edit_text(
        "\n".join(instructions),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.BACK,
                        callback_data=(
                            f"botcfg_setting:{group_key}:{category_page}:{settings_page}:{token}"
                        ),
                    )
                ]
            ]
        ),
    )

    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await state.set_state(BotConfigStates.waiting_for_value)
    await callback.answer()


@admin_required
@error_handler
async def handle_edit_setting(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()
    key = data.get("setting_key")
    group_key = data.get("setting_group_key", CATEGORY_FALLBACK_KEY)
    category_page = data.get("setting_category_page", 1)
    settings_page = data.get("setting_settings_page", 1)

    if not key:
        await message.answer("Не удалось определить редактируемую настройку. Попробуйте снова.")
        await state.clear()
        return

    old_value = bot_configuration_service.get_current_value(key)
    try:
        value = bot_configuration_service.parse_user_value(key, message.text or "")
    except ValueError as error:
        await message.answer(f"⚠️ {error}")
        return

    await bot_configuration_service.set_value(db, key, value)
    await db.commit()
    _log_setting_change(key, old_value, value, "manual_input")

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await message.answer("✅ Настройка обновлена")
    await message.answer(text, reply_markup=keyboard)
    await state.clear()
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )


@admin_required
@error_handler
async def handle_direct_setting_input(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()

    key = data.get("setting_key")
    group_key = data.get("setting_group_key", CATEGORY_FALLBACK_KEY)
    category_page = int(data.get("setting_category_page", 1) or 1)
    settings_page = int(data.get("setting_settings_page", 1) or 1)

    if not key:
        return

    try:
        value = bot_configuration_service.parse_user_value(key, message.text or "")
    except ValueError as error:
        await message.answer(f"⚠️ {error}")
        return

    old_value = bot_configuration_service.get_current_value(key)
    await bot_configuration_service.set_value(db, key, value)
    await db.commit()
    _log_setting_change(key, old_value, value, "direct_input")

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await message.answer("✅ Настройка обновлена")
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    await state.clear()
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )


@admin_required
@error_handler
async def handle_list_input(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()
    key = data.get("setting_key")
    operation = data.get("list_operation")
    group_key = data.get("setting_group_key", CATEGORY_FALLBACK_KEY)
    category_page = int(data.get("setting_category_page", 1) or 1)
    settings_page = int(data.get("setting_settings_page", 1) or 1)

    if not key or operation not in {"add", "remove"}:
        await message.answer("⚠️ Не удалось определить список для изменения. Попробуйте снова.")
        await state.clear()
        return

    definition = bot_configuration_service.get_definition(key)
    current_raw = bot_configuration_service.get_current_value(key)
    items = _to_list(current_raw)

    raw_text = (message.text or "").strip()
    if not raw_text:
        await message.answer("⚠️ Введите хотя бы одно значение.")
        return

    new_elements = [element.strip() for element in raw_text.split(",") if element.strip()]
    if not new_elements:
        await message.answer("⚠️ Введите хотя бы одно значение.")
        return

    changed = False
    if operation == "add":
        for element in new_elements:
            if element not in items:
                items.append(element)
                changed = True
        if not changed:
            await message.answer("ℹ️ Эти значения уже присутствуют в списке.")
            return
    else:
        removed = 0
        for element in new_elements:
            while element in items:
                items.remove(element)
                removed += 1
        if removed == 0:
            await message.answer("ℹ️ Ни одно из указанных значений не найдено в списке.")
            return

    new_value_str = ", ".join(items)
    old_value = current_raw
    await bot_configuration_service.set_value(db, key, new_value_str)
    await db.commit()
    _log_setting_change(key, old_value, new_value_str, f"list_{operation}")

    await message.answer("✅ Список обновлен")
    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    await state.clear()
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )


@admin_required
@error_handler
async def start_copy_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 5)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""

    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return

    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await state.set_state(BotConfigStates.waiting_for_copy_source)
    await callback.message.answer(
        "📋 <b>Копирование значения</b>\n\n"
        "Введите ключ настройки, из которой нужно скопировать значение.",
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_copy_source(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()
    target_key = data.get("setting_key")
    group_key = data.get("setting_group_key", CATEGORY_FALLBACK_KEY)
    category_page = int(data.get("setting_category_page", 1) or 1)
    settings_page = int(data.get("setting_settings_page", 1) or 1)

    if not target_key:
        await message.answer("⚠️ Не удалось определить целевую настройку.")
        await state.clear()
        return

    source_key = (message.text or "").strip().upper()
    if not source_key:
        await message.answer("⚠️ Укажите ключ настройки.")
        return

    try:
        bot_configuration_service.get_definition(source_key)
    except KeyError:
        await message.answer("⚠️ Такой настройки не существует.")
        return

    source_value = bot_configuration_service.get_current_value(source_key)
    serialized = bot_configuration_service.serialize_value(source_key, source_value)
    if serialized is None:
        serialized = ""

    try:
        parsed_value = bot_configuration_service.parse_user_value(target_key, serialized)
    except ValueError as error:
        await message.answer(f"⚠️ Не удалось преобразовать значение: {error}")
        return

    old_value = bot_configuration_service.get_current_value(target_key)
    await bot_configuration_service.set_value(db, target_key, parsed_value)
    await db.commit()
    _log_setting_change(target_key, old_value, parsed_value, f"copy:{source_key}")

    await message.answer("✅ Значение скопировано")
    text = _render_setting_text(target_key)
    keyboard = _build_setting_keyboard(target_key, group_key, category_page, settings_page)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    await state.clear()
    await _store_setting_context(
        state,
        key=target_key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )


@admin_required
@error_handler
async def start_settings_search(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.set_state(BotConfigStates.waiting_for_search_query)
    await state.update_data(botcfg_origin="bot_config", botcfg_timestamp=time.time())
    await callback.message.answer(
        "🔍 <b>Поиск по настройкам</b>\n\nВведите название, ключ или описание настройки.",
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_search_query(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    query = (message.text or "").strip().lower()
    if len(query) < 2:
        await message.answer("⚠️ Поисковый запрос должен содержать минимум 2 символа.")
        return

    results = []
    for definition in _iter_all_definitions():
        metadata = _get_setting_metadata(definition.key, definition)
        haystack = " ".join(
            filter(
                None,
                [
                    definition.display_name.lower(),
                    definition.key.lower(),
                    (metadata.description or "").lower(),
                    (metadata.format_hint or "").lower(),
                ],
            )
        )
        if query in haystack:
            results.append(definition)
        if len(results) >= 20:
            break

    if not results:
        await message.answer("ℹ️ Ничего не найдено.")
        await state.clear()
        return

    rows: list[list[types.InlineKeyboardButton]] = []
    for definition in results:
        group_key = _find_menu_key_for_category(definition.category_key)
        callback_token = bot_configuration_service.get_callback_token(definition.key)
        current_value = bot_configuration_service.get_current_value(definition.key)
        status_icon = _setting_status_icon(definition, current_value)
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"{status_icon} {definition.display_name}",
                    callback_data=f"botcfg_setting:{group_key}:1:1:{callback_token}",
                )
            ]
        )

    summary_lines = [
        "🔍 <b>Результаты поиска</b>",
        f"Найдено: {len(results)}",
        "Выберите настройку из списка:",
    ]

    await message.answer(
        "\n".join(summary_lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await state.clear()


@admin_required
@error_handler
async def show_history(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    if not SETTINGS_HISTORY:
        await callback.message.answer("ℹ️ История изменений пуста.")
        await callback.answer()
        return

    lines = ["📊 <b>Последние изменения настроек</b>"]

    for entry in list(SETTINGS_HISTORY)[:10]:
        key = entry["key"]
        timestamp: datetime = entry["timestamp"]
        source = entry.get("source", "manual")
        try:
            definition = bot_configuration_service.get_definition(key)
            metadata = _get_setting_metadata(key, definition)
            old_display = _format_setting_value(definition, entry.get("old"), metadata, short=True)
            new_display = _format_setting_value(definition, entry.get("new"), metadata, short=True)
        except KeyError:
            old_display = str(entry.get("old"))
            new_display = str(entry.get("new"))
        time_str = timestamp.strftime("%d.%m %H:%M")
        lines.append(
            f"• <code>{key}</code> ({time_str})\n"
            f"  было: {old_display}\n  стало: {new_display}\n  источник: {source}"
        )

    await callback.message.answer("\n".join(lines), parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def show_presets_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    rows: list[list[types.InlineKeyboardButton]] = []
    for preset_key, title in PRESET_TITLES.items():
        rows.append([
            types.InlineKeyboardButton(
                text=f"🎯 {title}",
                callback_data=f"botcfg_apply_preset:{preset_key}",
            )
        ])

    if CUSTOM_PRESETS:
        for name, payload in sorted(CUSTOM_PRESETS.items()):
            rows.append([
                types.InlineKeyboardButton(
                    text=f"⭐ {payload.get('title', name)}",
                    callback_data=f"botcfg_apply_preset:custom:{name}",
                )
            ])

    rows.append([
        types.InlineKeyboardButton(
            text="💾 Сохранить текущие настройки",
            callback_data="botcfg_save_preset",
        )
    ])
    rows.append([
        types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_bot_config")
    ])

    description_lines = [
        "🎛 <b>Пресеты настроек</b>",
        "Быстро применяйте готовые конфигурации или сохраните свою.",
    ]

    await callback.message.answer(
        "\n".join(description_lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await callback.answer()


async def _apply_preset_values(
    db: AsyncSession,
    preset_values: Dict[str, Any],
    *,
    source: str,
) -> List[str]:
    applied: List[str] = []
    for key, raw_value in preset_values.items():
        try:
            definition = bot_configuration_service.get_definition(key)
        except KeyError:
            continue

        old_value = bot_configuration_service.get_current_value(key)

        if isinstance(raw_value, str):
            try:
                new_value = bot_configuration_service.parse_user_value(key, raw_value)
            except ValueError:
                continue
        else:
            new_value = raw_value

        if new_value == old_value:
            continue

        await bot_configuration_service.set_value(db, key, new_value)
        applied.append(key)
        _log_setting_change(key, old_value, new_value, source)

    if applied:
        await db.commit()

    return applied


@admin_required
@error_handler
async def apply_preset(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split(":", 1)
    preset_id = parts[1] if len(parts) > 1 else ""

    if preset_id.startswith("custom:"):
        preset_key = preset_id.split(":", 1)[1]
        preset_entry = CUSTOM_PRESETS.get(preset_key)
        if not preset_entry:
            await callback.answer("Этот пресет больше недоступен", show_alert=True)
            return
        preset_values = preset_entry.get("values", {})
        source = f"preset:custom:{preset_key}"
        title = preset_entry.get("title", preset_key)
    else:
        if preset_id not in PRESET_TITLES:
            await callback.answer("Неизвестный пресет", show_alert=True)
            return
        preset_values = PREDEFINED_PRESETS.get(preset_id, {})
        source = f"preset:{preset_id}"
        title = PRESET_TITLES.get(preset_id, preset_id)

    applied_keys = await _apply_preset_values(db, preset_values, source=source)

    if not applied_keys:
        await callback.answer("Настройки уже соответствуют пресету")
        return

    summary = "\n".join(f"• <code>{key}</code>" for key in applied_keys)
    await callback.message.answer(
        f"✅ Пресет <b>{title}</b> применен. Обновлены параметры:\n{summary}",
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def start_save_preset(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.set_state(BotConfigStates.waiting_for_preset_name)
    await callback.message.answer(
        "💾 <b>Сохранение пресета</b>\n\nВведите название для вашего пресета.",
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_preset_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    name_raw = (message.text or "").strip()
    if not name_raw:
        await message.answer("⚠️ Название не может быть пустым.")
        return

    key = name_raw.lower().replace(" ", "_")
    snapshot: Dict[str, str] = {}
    for definition in _iter_all_definitions():
        value = bot_configuration_service.get_current_value(definition.key)
        serialized = bot_configuration_service.serialize_value(definition.key, value)
        snapshot[definition.key] = "" if serialized is None else serialized

    CUSTOM_PRESETS[key] = {
        "title": name_raw,
        "values": snapshot,
        "created_at": datetime.utcnow(),
    }

    await message.answer(f"✅ Пресет <b>{name_raw}</b> сохранен.", parse_mode="HTML")
    await state.clear()


@admin_required
@error_handler
async def export_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    lines: List[str] = []
    for definition in _iter_all_definitions():
        value = bot_configuration_service.get_current_value(definition.key)
        serialized = bot_configuration_service.serialize_value(definition.key, value)
        if serialized is None:
            serialized = ""
        lines.append(f"{definition.key}={serialized}")

    content = "\n".join(lines).encode("utf-8")
    document = BufferedInputFile(content, filename="bot-settings.env")
    await callback.message.answer_document(
        document,
        caption="📤 Экспорт актуальных настроек",
    )
    await callback.answer()


@admin_required
@error_handler
async def start_import_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.set_state(BotConfigStates.waiting_for_import)
    await callback.message.answer(
        "📥 <b>Импорт настроек</b>\n\nОтправьте содержимое .env файла сообщением.",
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_import_settings(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    content = (message.text or "").strip()
    if not content:
        await message.answer("⚠️ Сообщение пустое. Отправьте содержимое .env файла.")
        return

    changes: List[str] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        try:
            bot_configuration_service.get_definition(key)
        except KeyError:
            continue

        old_value = bot_configuration_service.get_current_value(key)
        try:
            parsed_value = bot_configuration_service.parse_user_value(key, raw_value)
        except ValueError:
            continue

        if parsed_value == old_value:
            continue

        await bot_configuration_service.set_value(db, key, parsed_value)
        _log_setting_change(key, old_value, parsed_value, "import")
        changes.append(key)

    if changes:
        await db.commit()
        summary = "\n".join(f"• <code>{key}</code>" for key in changes)
        await message.answer(
            f"✅ Импорт завершен. Обновлено {len(changes)} настроек:\n{summary}",
            parse_mode="HTML",
        )
    else:
        await message.answer("ℹ️ Новых изменений не найдено.")

    await state.clear()


@admin_required
@error_handler
async def show_help(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    text = (
        "❓ <b>Помощь по панели настроек</b>\n\n"
        "• Используйте поиск, чтобы быстро находить параметры.\n"
        "• Пресеты помогают мгновенно применять проверенные конфигурации.\n"
        "• История изменений хранит последние 10 операций.\n"
        "• Экспортируйте .env перед большими изменениями и при необходимости импортируйте его обратно."
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def reset_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 4)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return
    old_value = bot_configuration_service.get_current_value(key)
    await bot_configuration_service.reset_value(db, key)
    await db.commit()
    new_value = bot_configuration_service.get_current_value(key)
    _log_setting_change(key, old_value, new_value, "reset")

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer("Сброшено к значению по умолчанию")


@admin_required
@error_handler
async def toggle_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 4)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return
    current = bot_configuration_service.get_current_value(key)
    new_value = not bool(current)
    await bot_configuration_service.set_value(db, key, new_value)
    await db.commit()
    _log_setting_change(key, current, new_value, "toggle")

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer("Обновлено")


@admin_required
@error_handler
async def apply_setting_choice(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 5)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    choice_token = parts[5] if len(parts) > 5 else ""

    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return

    try:
        value = bot_configuration_service.resolve_choice_token(key, choice_token)
    except KeyError:
        await callback.answer("Это значение больше недоступно", show_alert=True)
        return

    old_value = bot_configuration_service.get_current_value(key)
    await bot_configuration_service.set_value(db, key, value)
    await db.commit()
    _log_setting_change(key, old_value, value, "choice")

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer("Значение обновлено")


@admin_required
@error_handler
async def set_boolean_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 6)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    desired_flag = parts[5] if len(parts) > 5 else "1"

    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return

    target_value = desired_flag == "1"
    current_value = bool(bot_configuration_service.get_current_value(key))

    if current_value == target_value:
        await callback.answer("Значение уже установлено")
        return

    await bot_configuration_service.set_value(db, key, target_value)
    await db.commit()
    _log_setting_change(key, current_value, target_value, "bool_set")

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer("Обновлено")


@admin_required
@error_handler
async def apply_recommended_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 5)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""

    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return

    definition = bot_configuration_service.get_definition(key)
    metadata = _get_setting_metadata(key, definition)
    if not metadata.recommended or not metadata.recommended.strip():
        await callback.answer("Для этой настройки нет рекомендуемого значения", show_alert=True)
        return

    try:
        new_value = bot_configuration_service.parse_user_value(key, metadata.recommended)
    except ValueError as error:
        await callback.answer(f"Не удалось применить рекомендуемое: {error}", show_alert=True)
        return

    old_value = bot_configuration_service.get_current_value(key)
    await bot_configuration_service.set_value(db, key, new_value)
    await db.commit()
    _log_setting_change(key, old_value, new_value, "recommended")

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer("Рекомендация применена")


async def _start_list_operation(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    *,
    operation: str,
) -> None:
    parts = callback.data.split(":", 5)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""

    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return

    current_items = _to_list(bot_configuration_service.get_current_value(key))

    if operation == "remove" and not current_items:
        await callback.answer("Список пуст — нечего удалять", show_alert=True)
        return

    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await state.update_data(list_operation=operation)
    await state.set_state(BotConfigStates.waiting_for_list_input)

    if operation == "add":
        prompt = (
            "➕ <b>Добавление значения</b>\n\n"
            "Введите элемент, который нужно добавить в список."
        )
    else:
        items_preview = "\n".join(f"• {item}" for item in current_items[:20])
        prompt = (
            "➖ <b>Удаление значения</b>\n\n"
            "Текущий список:\n"
            f"{items_preview or '—'}\n\nВведите элемент, который нужно удалить."
        )

    await callback.message.answer(prompt, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def start_list_add(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await _start_list_operation(callback, db_user, state, operation="add")


@admin_required
@error_handler
async def start_list_remove(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await _start_list_operation(callback, db_user, state, operation="remove")


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        show_bot_config_menu,
        F.data == "admin_bot_config",
    )
    dp.callback_query.register(
        show_bot_config_group,
        F.data.startswith("botcfg_group:") & (~F.data.endswith(":noop")),
    )
    dp.callback_query.register(
        show_bot_config_category,
        F.data.startswith("botcfg_cat:"),
    )
    dp.callback_query.register(
        test_remnawave_connection,
        F.data.startswith("botcfg_test_remnawave:"),
    )
    dp.callback_query.register(
        test_payment_provider,
        F.data.startswith("botcfg_test_payment:"),
    )
    dp.callback_query.register(
        show_bot_config_setting,
        F.data.startswith("botcfg_setting:"),
    )
    dp.callback_query.register(
        start_edit_setting,
        F.data.startswith("botcfg_edit:"),
    )
    dp.callback_query.register(
        reset_setting,
        F.data.startswith("botcfg_reset:"),
    )
    dp.callback_query.register(
        toggle_setting,
        F.data.startswith("botcfg_toggle:"),
    )
    dp.callback_query.register(
        apply_setting_choice,
        F.data.startswith("botcfg_choice:"),
    )
    dp.callback_query.register(
        set_boolean_setting,
        F.data.startswith("botcfg_bool:"),
    )
    dp.callback_query.register(
        apply_recommended_setting,
        F.data.startswith("botcfg_apply_rec:"),
    )
    dp.callback_query.register(
        start_list_add,
        F.data.startswith("botcfg_list_add:"),
    )
    dp.callback_query.register(
        start_list_remove,
        F.data.startswith("botcfg_list_remove:"),
    )
    dp.callback_query.register(
        start_copy_setting,
        F.data.startswith("botcfg_copy:"),
    )
    dp.callback_query.register(
        start_settings_search,
        F.data == "botcfg_search",
    )
    dp.callback_query.register(
        show_history,
        F.data == "botcfg_history",
    )
    dp.callback_query.register(
        show_presets_menu,
        F.data == "botcfg_presets",
    )
    dp.callback_query.register(
        apply_preset,
        F.data.startswith("botcfg_apply_preset:"),
    )
    dp.callback_query.register(
        start_save_preset,
        F.data == "botcfg_save_preset",
    )
    dp.callback_query.register(
        export_settings,
        F.data == "botcfg_export",
    )
    dp.callback_query.register(
        start_import_settings,
        F.data == "botcfg_import",
    )
    dp.callback_query.register(
        show_help,
        F.data == "botcfg_help",
    )
    dp.message.register(
        handle_direct_setting_input,
        StateFilter(None),
        F.text,
        BotConfigInputFilter(),
    )
    dp.message.register(
        handle_edit_setting,
        BotConfigStates.waiting_for_value,
    )
    dp.message.register(
        handle_copy_source,
        BotConfigStates.waiting_for_copy_source,
    )
    dp.message.register(
        handle_search_query,
        BotConfigStates.waiting_for_search_query,
    )
    dp.message.register(
        handle_preset_name,
        BotConfigStates.waiting_for_preset_name,
    )
    dp.message.register(
        handle_import_settings,
        BotConfigStates.waiting_for_import,
    )
    dp.message.register(
        handle_list_input,
        BotConfigStates.waiting_for_list_input,
    )

