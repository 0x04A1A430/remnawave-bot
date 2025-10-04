import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from decimal import Decimal, InvalidOperation
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
)

from app.database.universal_migration import ensure_default_web_api_token

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, settings
from app.database.crud.system_setting import (
    delete_system_setting,
    get_recent_system_setting_changes,
    log_system_setting_change,
    upsert_system_setting,
)
from app.database.database import AsyncSessionLocal
from app.database.models import SystemSetting, SystemSettingChange


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

    @property
    def display_name(self) -> str:
        return _title_from_key(self.key)


@dataclass(slots=True)
class ChoiceOption:
    value: Any
    label: str
    description: Optional[str] = None


class SettingInputType(str, Enum):
    TOGGLE = "toggle"
    TEXT = "text"
    NUMBER = "number"
    PRICE = "price"
    LIST = "list"
    CHOICE = "choice"
    TIME = "time"


@dataclass(slots=True)
class SettingMeta:
    description: str = ""
    format_hint: str = ""
    example: str = ""
    warning: str = ""
    dependencies: tuple[str, ...] = ()
    icon: str = "⚙️"
    recommended: Optional[str] = None
    unit: Optional[str] = None


@dataclass(slots=True)
class PresetDefinition:
    key: str
    label: str
    description: str
    summary: str
    changes: Dict[str, Any]


@dataclass(slots=True)
class DashboardCategory:
    key: str
    title: str
    description: str
    service_categories: tuple[str, ...]


class BotConfigurationService:
    EXCLUDED_KEYS: set[str] = {"BOT_TOKEN", "ADMIN_IDS"}

    CATEGORY_TITLES: Dict[str, str] = {
        "SUPPORT": "💬 Ссылка на поддержку",
        "LOCALIZATION": "🌍 Языковые настройки",
        "MAINTENANCE": "🛠️ Режим обслуживания",
        "CHANNEL": "📣 Обязательная подписка",
        "ADMIN_NOTIFICATIONS": "🔔 Уведомления админам",
        "ADMIN_REPORTS": "📊 Автоматические отчеты",
        "TRIAL": "🎁 Триальная подписка",
        "PAID_SUBSCRIPTION": "💰 Платные подписки",
        "PERIODS": "📅 Периоды подписки",
        "SUBSCRIPTION_PRICES": "💵 Цены за периоды",
        "TRAFFIC": "🚦 Настройки трафика",
        "TRAFFIC_PACKAGES": "📦 Пакеты трафика",
        "DISCOUNTS": "🎯 Промо и скидки",
        "PAYMENT": "⚙️ Общие настройки платежей",
        "TELEGRAM": "⭐ Telegram Stars",
        "CRYPTOBOT": "💎 CryptoBot",
        "YOOKASSA": "💸 YooKassa",
        "TRIBUTE": "🎁 Tribute",
        "MULENPAY": "💰 MulenPay",
        "PAL24": "🏦 Pal24/PayPalych",
        "REMNAWAVE": "🔗 RemnaWave API",
        "REFERRAL": "🤝 Реферальная система",
        "AUTOPAY": "🔄 Автопродление",
        "INTERFACE_BRANDING": "🖼️ Визуальные настройки",
        "INTERFACE_SUBSCRIPTION": "🔗 Скрыть ссылку подписки",
        "CONNECT_BUTTON": "🚀 Кнопка «Подключиться»",
        "HAPP": "🅷 Happ настройки",
        "SKIP": "⚡ Быстрый старт",
        "ADDITIONAL": "📱 Приложения и DeepLinks",
        "MINIAPP": "📱 Mini App",
        "DATABASE": "🗄️ Режим БД",
        "POSTGRES": "🐘 PostgreSQL",
        "SQLITE": "💾 SQLite",
        "REDIS": "🧠 Redis",
        "MONITORING": "📈 Общий мониторинг",
        "NOTIFICATIONS": "🔔 Уведомления пользователям",
        "SERVER": "🖥️ Статус серверов",
        "BACKUP": "💾 Система бэкапов",
        "VERSION": "🔄 Обновления",
        "LOG": "📝 Логирование",
        "WEBHOOK": "🌐 Webhook",
        "WEB_API": "🌐 Web API",
        "DEBUG": "🔧 Режим разработки",
    }

    CATEGORY_KEY_OVERRIDES: Dict[str, str] = {
        "DATABASE_URL": "DATABASE",
        "DATABASE_MODE": "DATABASE",
        "LOCALES_PATH": "DATABASE",
        "DEFAULT_DEVICE_LIMIT": "PAID_SUBSCRIPTION",
        "DEFAULT_TRAFFIC_LIMIT_GB": "PAID_SUBSCRIPTION",
        "MAX_DEVICES_LIMIT": "PAID_SUBSCRIPTION",
        "PRICE_PER_DEVICE": "PAID_SUBSCRIPTION",
        "DEFAULT_TRAFFIC_RESET_STRATEGY": "TRAFFIC",
        "RESET_TRAFFIC_ON_PAYMENT": "TRAFFIC",
        "TRAFFIC_SELECTION_MODE": "TRAFFIC",
        "FIXED_TRAFFIC_LIMIT_GB": "TRAFFIC",
        "AVAILABLE_SUBSCRIPTION_PERIODS": "PERIODS",
        "AVAILABLE_RENEWAL_PERIODS": "PERIODS",
        "BASE_SUBSCRIPTION_PRICE": "SUBSCRIPTION_PRICES",
        "PRICE_14_DAYS": "SUBSCRIPTION_PRICES",
        "PRICE_30_DAYS": "SUBSCRIPTION_PRICES",
        "PRICE_60_DAYS": "SUBSCRIPTION_PRICES",
        "PRICE_90_DAYS": "SUBSCRIPTION_PRICES",
        "PRICE_180_DAYS": "SUBSCRIPTION_PRICES",
        "PRICE_360_DAYS": "SUBSCRIPTION_PRICES",
        "PRICE_TRAFFIC_5GB": "TRAFFIC_PACKAGES",
        "PRICE_TRAFFIC_10GB": "TRAFFIC_PACKAGES",
        "PRICE_TRAFFIC_25GB": "TRAFFIC_PACKAGES",
        "PRICE_TRAFFIC_50GB": "TRAFFIC_PACKAGES",
        "PRICE_TRAFFIC_100GB": "TRAFFIC_PACKAGES",
        "PRICE_TRAFFIC_250GB": "TRAFFIC_PACKAGES",
        "PRICE_TRAFFIC_500GB": "TRAFFIC_PACKAGES",
        "PRICE_TRAFFIC_1000GB": "TRAFFIC_PACKAGES",
        "PRICE_TRAFFIC_UNLIMITED": "TRAFFIC_PACKAGES",
        "TRAFFIC_PACKAGES_CONFIG": "TRAFFIC_PACKAGES",
        "BASE_PROMO_GROUP_PERIOD_DISCOUNTS_ENABLED": "DISCOUNTS",
        "BASE_PROMO_GROUP_PERIOD_DISCOUNTS": "DISCOUNTS",
        "REFERRED_USER_REWARD": "REFERRAL",
        "DEFAULT_AUTOPAY_DAYS_BEFORE": "AUTOPAY",
        "MIN_BALANCE_FOR_AUTOPAY_KOPEKS": "AUTOPAY",
        "TRIAL_WARNING_HOURS": "NOTIFICATIONS",
        "ENABLE_NOTIFICATIONS": "NOTIFICATIONS",
        "NOTIFICATION_RETRY_ATTEMPTS": "NOTIFICATIONS",
        "NOTIFICATION_CACHE_HOURS": "NOTIFICATIONS",
        "MONITORING_LOGS_RETENTION_DAYS": "MONITORING",
        "ENABLE_LOGO_MODE": "INTERFACE_BRANDING",
        "LOGO_FILE": "INTERFACE_BRANDING",
        "HIDE_SUBSCRIPTION_LINK": "INTERFACE_SUBSCRIPTION",
        "CONNECT_BUTTON_MODE": "CONNECT_BUTTON",
        "MINIAPP_CUSTOM_URL": "CONNECT_BUTTON",
        "APP_CONFIG_PATH": "ADDITIONAL",
        "ENABLE_DEEP_LINKS": "ADDITIONAL",
        "APP_CONFIG_CACHE_TTL": "ADDITIONAL",
        "DEFAULT_LANGUAGE": "LOCALIZATION",
        "AVAILABLE_LANGUAGES": "LOCALIZATION",
        "PAYMENT_SERVICE_NAME": "PAYMENT",
        "PAYMENT_BALANCE_DESCRIPTION": "PAYMENT",
        "PAYMENT_SUBSCRIPTION_DESCRIPTION": "PAYMENT",
        "PAYMENT_BALANCE_TEMPLATE": "PAYMENT",
        "PAYMENT_SUBSCRIPTION_TEMPLATE": "PAYMENT",
        "INACTIVE_USER_DELETE_MONTHS": "MONITORING",
        "LANGUAGE_SELECTION_ENABLED": "LOCALIZATION",
    }

    CATEGORY_PREFIX_OVERRIDES: Dict[str, str] = {
        "SUPPORT_": "SUPPORT",
        "ADMIN_NOTIFICATIONS": "ADMIN_NOTIFICATIONS",
        "ADMIN_REPORTS": "ADMIN_REPORTS",
        "CHANNEL_": "CHANNEL",
        "POSTGRES_": "POSTGRES",
        "SQLITE_": "SQLITE",
        "REDIS_": "REDIS",
        "REMNAWAVE": "REMNAWAVE",
        "TRIAL_": "TRIAL",
        "TRAFFIC_PACKAGES": "TRAFFIC_PACKAGES",
        "PRICE_TRAFFIC": "TRAFFIC_PACKAGES",
        "TRAFFIC_": "TRAFFIC",
        "REFERRAL_": "REFERRAL",
        "AUTOPAY_": "AUTOPAY",
        "TELEGRAM_STARS": "TELEGRAM",
        "TRIBUTE_": "TRIBUTE",
        "YOOKASSA_": "YOOKASSA",
        "CRYPTOBOT_": "CRYPTOBOT",
        "MULENPAY_": "MULENPAY",
        "PAL24_": "PAL24",
        "PAYMENT_": "PAYMENT",
        "CONNECT_BUTTON_HAPP": "HAPP",
        "HAPP_": "HAPP",
        "SKIP_": "SKIP",
        "MINIAPP_": "MINIAPP",
        "MONITORING_": "MONITORING",
        "NOTIFICATION_": "NOTIFICATIONS",
        "SERVER_STATUS": "SERVER",
        "MAINTENANCE_": "MAINTENANCE",
        "VERSION_CHECK": "VERSION",
        "BACKUP_": "BACKUP",
        "WEBHOOK_": "WEBHOOK",
        "LOG_": "LOG",
        "WEB_API_": "WEB_API",
        "DEBUG": "DEBUG",
    }

    CHOICES: Dict[str, List[ChoiceOption]] = {
        "DATABASE_MODE": [
            ChoiceOption("auto", "🤖 Авто"),
            ChoiceOption("postgresql", "🐘 PostgreSQL"),
            ChoiceOption("sqlite", "💾 SQLite"),
        ],
        "REMNAWAVE_AUTH_TYPE": [
            ChoiceOption("api_key", "🔑 API Key"),
            ChoiceOption("basic_auth", "🧾 Basic Auth"),
        ],
        "REMNAWAVE_USER_DELETE_MODE": [
            ChoiceOption("delete", "🗑 Удалять"),
            ChoiceOption("disable", "🚫 Деактивировать"),
        ],
        "TRAFFIC_SELECTION_MODE": [
            ChoiceOption("selectable", "📦 Выбор пакетов"),
            ChoiceOption("fixed", "📏 Фиксированный лимит"),
        ],
        "DEFAULT_TRAFFIC_RESET_STRATEGY": [
            ChoiceOption("NO_RESET", "♾️ Без сброса"),
            ChoiceOption("DAY", "📅 Ежедневно"),
            ChoiceOption("WEEK", "🗓 Еженедельно"),
            ChoiceOption("MONTH", "📆 Ежемесячно"),
        ],
        "SUPPORT_SYSTEM_MODE": [
            ChoiceOption("tickets", "🎫 Только тикеты"),
            ChoiceOption("contact", "💬 Только контакт"),
            ChoiceOption("both", "🔁 Оба варианта"),
        ],
        "CONNECT_BUTTON_MODE": [
            ChoiceOption("guide", "📘 Гайд"),
            ChoiceOption("miniapp_subscription", "🧾 Mini App подписка"),
            ChoiceOption("miniapp_custom", "🧩 Mini App (ссылка)"),
            ChoiceOption("link", "🔗 Прямая ссылка"),
            ChoiceOption("happ_cryptolink", "🪙 Happ CryptoLink"),
        ],
        "SERVER_STATUS_MODE": [
            ChoiceOption("disabled", "🚫 Отключено"),
            ChoiceOption("external_link", "🌐 Внешняя ссылка"),
            ChoiceOption("external_link_miniapp", "🧭 Mini App ссылка"),
            ChoiceOption("xray", "📊 XRay Checker"),
        ],
        "YOOKASSA_PAYMENT_MODE": [
            ChoiceOption("full_payment", "💳 Полная оплата"),
            ChoiceOption("partial_payment", "🪙 Частичная оплата"),
            ChoiceOption("advance", "💼 Аванс"),
            ChoiceOption("full_prepayment", "📦 Полная предоплата"),
            ChoiceOption("partial_prepayment", "📦 Частичная предоплата"),
            ChoiceOption("credit", "💰 Кредит"),
            ChoiceOption("credit_payment", "💸 Погашение кредита"),
        ],
        "YOOKASSA_PAYMENT_SUBJECT": [
            ChoiceOption("commodity", "📦 Товар"),
            ChoiceOption("excise", "🥃 Подакцизный товар"),
            ChoiceOption("job", "🛠 Работа"),
            ChoiceOption("service", "🧾 Услуга"),
            ChoiceOption("gambling_bet", "🎲 Ставка"),
            ChoiceOption("gambling_prize", "🏆 Выигрыш"),
            ChoiceOption("lottery", "🎫 Лотерея"),
            ChoiceOption("lottery_prize", "🎁 Приз лотереи"),
            ChoiceOption("intellectual_activity", "🧠 Интеллектуальная деятельность"),
            ChoiceOption("payment", "💱 Платеж"),
            ChoiceOption("agent_commission", "🤝 Комиссия агента"),
            ChoiceOption("composite", "🧩 Композитный"),
            ChoiceOption("another", "📄 Другое"),
        ],
        "YOOKASSA_VAT_CODE": [
            ChoiceOption(1, "1 — НДС не облагается"),
            ChoiceOption(2, "2 — НДС 0%"),
            ChoiceOption(3, "3 — НДС 10%"),
            ChoiceOption(4, "4 — НДС 20%"),
            ChoiceOption(5, "5 — НДС 10/110"),
            ChoiceOption(6, "6 — НДС 20/120"),
        ],
        "MULENPAY_LANGUAGE": [
            ChoiceOption("ru", "🇷🇺 Русский"),
            ChoiceOption("en", "🇬🇧 Английский"),
        ],
        "LOG_LEVEL": [
            ChoiceOption("DEBUG", "🐞 Debug"),
            ChoiceOption("INFO", "ℹ️ Info"),
            ChoiceOption("WARNING", "⚠️ Warning"),
            ChoiceOption("ERROR", "❌ Error"),
            ChoiceOption("CRITICAL", "🔥 Critical"),
        ],
    }

    CATEGORY_DESCRIPTIONS: Dict[str, str] = {
        "SUPPORT": "Контактные данные поддержки, режимы тикетов и SLA.",
        "LOCALIZATION": "Доступные языки и язык по умолчанию для бота.",
        "MAINTENANCE": "Параметры режима обслуживания и сообщения пользователям.",
        "CHANNEL": "Настройка обязательной подписки и канала с новостями.",
        "ADMIN_NOTIFICATIONS": "Куда отправлять уведомления администраторам.",
        "ADMIN_REPORTS": "Отчеты для администраторов и время отправки.",
        "TRIAL": "Пробный период, лимиты и предупреждения.",
        "PAID_SUBSCRIPTION": "Базовые лимиты и стоимость платных тарифов.",
        "PERIODS": "Доступные периоды подписки и продления.",
        "SUBSCRIPTION_PRICES": "Стоимость подписок по периодам в копейках.",
        "TRAFFIC": "Лимиты трафика, сброс и варианты выбора пользователем.",
        "TRAFFIC_PACKAGES": "Конфигурация дополнительных пакетов трафика.",
        "DISCOUNTS": "Глобальные скидки и промо-настройки.",
        "PAYMENT": "Общие тексты и описание платежей.",
        "TELEGRAM": "Оплата через Telegram Stars и ее параметры.",
        "CRYPTOBOT": "Интеграция CryptoBot и поддерживаемые валюты.",
        "YOOKASSA": "Параметры YooKassa и реквизиты магазина.",
        "TRIBUTE": "Настройки платежей Tribute.",
        "MULENPAY": "Интеграция MulenPay и тексты кнопок.",
        "PAL24": "Параметры PayPalych (PAL24) и тексты кнопок оплаты.",
        "REMNAWAVE": "Интеграция с RemnaWave и ключи доступа.",
        "REFERRAL": "Размер бонусов и условия реферальной программы.",
        "AUTOPAY": "Автопродление подписок и минимальные остатки.",
        "INTERFACE_BRANDING": "Логотипы и визуальные элементы.",
        "INTERFACE_SUBSCRIPTION": "Настройки отображения ссылки на подписку.",
        "CONNECT_BUTTON": "Поведение кнопки “Подключиться”.",
        "HAPP": "Интеграция Happ и соответствующие ссылки.",
        "SKIP": "Сценарии быстрого старта.",
        "ADDITIONAL": "Дополнительные miniapp и deep-link настройки.",
        "MINIAPP": "Mini App и его индивидуальные параметры.",
        "DATABASE": "Общий режим работы базы данных.",
        "POSTGRES": "Параметры подключения к PostgreSQL.",
        "SQLITE": "Настройки локальной SQLite базы.",
        "REDIS": "Подключение к Redis для кэша.",
        "MONITORING": "Интервалы проверок и хранение логов мониторинга.",
        "NOTIFICATIONS": "Push-уведомления и тайминги для пользователей.",
        "SERVER": "Источники статуса серверов и учетные данные.",
        "BACKUP": "Бэкапы базы и их расписание.",
        "VERSION": "Проверка обновлений и связанные параметры.",
        "LOG": "Уровень логирования и хранение логов.",
        "WEBHOOK": "URL вебхуков и параметры SSL.",
        "WEB_API": "Web API токены и параметры доступа.",
        "DEBUG": "Режим отладки и дополнительные проверки.",
    }

    DASHBOARD_CATEGORIES: tuple[DashboardCategory, ...] = (
        DashboardCategory(
            key="core",
            title="🤖 Основные",
            description="Базовые параметры запуска, логики и безопасности бота.",
            service_categories=("PAYMENT", "AUTOPAY", "CHANNEL", "VERSION", "MAINTENANCE"),
        ),
        DashboardCategory(
            key="support",
            title="💬 Поддержка",
            description="Система тикетов, контакты и SLA для команды поддержки.",
            service_categories=("SUPPORT", "ADMIN_NOTIFICATIONS", "ADMIN_REPORTS"),
        ),
        DashboardCategory(
            key="payments",
            title="💳 Платежные системы",
            description="Настройка YooKassa, CryptoBot, MulenPay, PAL24, Tribute и Telegram Stars.",
            service_categories=(
                "PAYMENT",
                "YOOKASSA",
                "CRYPTOBOT",
                "MULENPAY",
                "PAL24",
                "TRIBUTE",
                "TELEGRAM",
            ),
        ),
        DashboardCategory(
            key="subscriptions",
            title="📅 Подписки и цены",
            description="Периоды, стоимость, трафик и дополнительные пакеты.",
            service_categories=(
                "PAID_SUBSCRIPTION",
                "PERIODS",
                "SUBSCRIPTION_PRICES",
                "TRAFFIC",
                "TRAFFIC_PACKAGES",
                "DISCOUNTS",
            ),
        ),
        DashboardCategory(
            key="trial",
            title="🎁 Пробный период",
            description="Условия тестового доступа, трафик и уведомления.",
            service_categories=("TRIAL",),
        ),
        DashboardCategory(
            key="referral",
            title="👥 Реферальная программа",
            description="Размер бонусов, комиссии и уведомления по рефералам.",
            service_categories=("REFERRAL",),
        ),
        DashboardCategory(
            key="notifications",
            title="🔔 Уведомления",
            description="Оповещения админам, пользователям и регламенты SLA.",
            service_categories=("ADMIN_NOTIFICATIONS", "ADMIN_REPORTS", "NOTIFICATIONS", "MONITORING"),
        ),
        DashboardCategory(
            key="interface",
            title="🎨 Интерфейс и брендинг",
            description="Логотипы, тексты, языки и Mini App.",
            service_categories=(
                "INTERFACE_BRANDING",
                "INTERFACE_SUBSCRIPTION",
                "CONNECT_BUTTON",
                "HAPP",
                "SKIP",
                "MINIAPP",
                "LOCALIZATION",
            ),
        ),
        DashboardCategory(
            key="database",
            title="💾 База данных",
            description="Режимы хранения и параметры подключения к БД и кэшу.",
            service_categories=("DATABASE", "POSTGRES", "SQLITE", "REDIS"),
        ),
        DashboardCategory(
            key="remnawave",
            title="🌐 RemnaWave API",
            description="Интеграция с RemnaWave VPN панелью и ключи доступа.",
            service_categories=("REMNAWAVE",),
        ),
        DashboardCategory(
            key="servers",
            title="📊 Статус серверов",
            description="Мониторинг инфраструктуры и источники метрик.",
            service_categories=("SERVER", "MONITORING"),
        ),
        DashboardCategory(
            key="maintenance",
            title="🔧 Обслуживание",
            description="Режим ТО, бэкапы и обновления.",
            service_categories=("MAINTENANCE", "BACKUP", "VERSION"),
        ),
        DashboardCategory(
            key="advanced",
            title="⚡ Расширенные",
            description="Web API, Webhook, глубокие ссылки и отладка.",
            service_categories=("WEB_API", "WEBHOOK", "DEBUG", "LOG", "ADDITIONAL"),
        ),
        DashboardCategory(
            key="other",
            title="📦 Прочие",
            description="Настройки, которые пока не попали в основные разделы.",
            service_categories=(),
        ),
    )

    SETTING_META_OVERRIDES: Dict[str, SettingMeta] = {
        "SUPPORT_MENU_ENABLED": SettingMeta(
            icon="💬",
            description="Включает раздел поддержки в главном меню пользователя.",
            format_hint="Переключатель",
            example="True",
            warning="При отключении пользователи не смогут открыть тикеты.",
            dependencies=("SUPPORT_SYSTEM_MODE",),
            recommended="Включено",
        ),
        "SUPPORT_USERNAME": SettingMeta(
            icon="👩‍💼",
            description="Имя пользователя Telegram для прямого контакта со службой поддержки.",
            format_hint="Формат @username",
            example="@remnawave_support",
            warning="Проверьте, что аккаунт разрешает личные сообщения.",
        ),
        "SUPPORT_SYSTEM_MODE": SettingMeta(
            icon="🎫",
            description="Какой сценарий поддержки доступен пользователю: тикеты, прямой контакт или оба варианта.",
            format_hint="Выбор из списка",
            example="both",
            dependencies=("SUPPORT_MENU_ENABLED",),
        ),
        "SUPPORT_TICKET_SLA_ENABLED": SettingMeta(
            icon="⏱️",
            description="Отслеживать время ответа на тикеты и присылать напоминания администраторам.",
            format_hint="Переключатель",
            example="True",
            warning="Требует указанных периодов SLA, иначе уведомления будут частыми.",
        ),
        "SUPPORT_TICKET_SLA_MINUTES": SettingMeta(
            icon="🕒",
            description="Количество минут на первый ответ в тикете перед напоминанием.",
            format_hint="Целое число минут",
            example="5",
            recommended="5-30 минут",
            dependencies=("SUPPORT_TICKET_SLA_ENABLED",),
            unit="мин",
        ),
        "MAINTENANCE_MODE": SettingMeta(
            icon="🛠",
            description="Переводит бота в режим обслуживания и скрывает функционал от пользователей.",
            format_hint="Переключатель",
            example="True",
            warning="Все продажи и выдача подписок остановятся.",
        ),
        "MAINTENANCE_MESSAGE": SettingMeta(
            icon="📝",
            description="Текст, который увидит пользователь в режиме обслуживания.",
            format_hint="Текст до 500 символов",
            example="🔧 Идут технические работы…",
        ),
        "REMNAWAVE_API_URL": SettingMeta(
            icon="🌐",
            description="Базовый URL API RemnaWave для интеграции.",
            format_hint="https://host/api",
            example="https://panel.remnawave.com/api",
            warning="Должен быть доступен из сети бота.",
            dependencies=("REMNAWAVE_API_KEY", "REMNAWAVE_SECRET_KEY"),
        ),
        "REMNAWAVE_API_KEY": SettingMeta(
            icon="🔑",
            description="Публичный ключ доступа RemnaWave.",
            format_hint="Строка",
            example="rw_live_xxxxx",
            warning="Не передавайте ключ третьим лицам.",
        ),
        "REMNAWAVE_SECRET_KEY": SettingMeta(
            icon="🛡",
            description="Секретный ключ RemnaWave для подписи запросов.",
            format_hint="Строка",
            example="rw_secret_xxxxx",
            warning="Храните в секрете, используйте только на сервере бота.",
        ),
        "YOOKASSA_ENABLED": SettingMeta(
            icon="💳",
            description="Активирует оплату через YooKassa.",
            format_hint="Переключатель",
            example="True",
            dependencies=("YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY"),
        ),
        "YOOKASSA_SHOP_ID": SettingMeta(
            icon="🏢",
            description="Идентификатор магазина YooKassa.",
            format_hint="Число или строка",
            example="123456",
            warning="Используйте данные из личного кабинета YooKassa.",
        ),
        "YOOKASSA_SECRET_KEY": SettingMeta(
            icon="🔐",
            description="Секретный ключ YooKassa для API.",
            format_hint="Строка",
            example="live_xxx",
            warning="Никому не передавайте секретный ключ.",
        ),
        "BASE_SUBSCRIPTION_PRICE": SettingMeta(
            icon="💰",
            description="Базовая цена подписки за 30 дней в копейках.",
            format_hint="Введите цену в рублях",
            example="990",
            unit="₽",
        ),
        "PRICE_30_DAYS": SettingMeta(
            icon="📆",
            description="Стоимость подписки на 30 дней.",
            format_hint="Введите цену в рублях",
            example="990",
            unit="₽",
        ),
        "TRIAL_DURATION_DAYS": SettingMeta(
            icon="🎁",
            description="Длительность бесплатного периода в днях.",
            format_hint="Целое число дней",
            example="3",
            recommended="3-7 дней",
            unit="дн",
        ),
        "TRIAL_TRAFFIC_LIMIT_GB": SettingMeta(
            icon="📶",
            description="Объем трафика для триала.",
            format_hint="Целое число ГБ",
            example="10",
            unit="ГБ",
        ),
        "ENABLE_NOTIFICATIONS": SettingMeta(
            icon="🔔",
            description="Включает уведомления пользователям о статусе подписки.",
            format_hint="Переключатель",
            example="True",
        ),
        "REFERRAL_COMMISSION_PERCENT": SettingMeta(
            icon="👥",
            description="Процент комиссии, который получает приглашенный реферал.",
            format_hint="Число от 0 до 100",
            example="25",
            unit="%",
        ),
        "DATABASE_MODE": SettingMeta(
            icon="💾",
            description="Режим выбора между PostgreSQL, SQLite или автоматическим определением.",
            format_hint="Выбор из списка",
            example="auto",
            warning="При переключении перезапустите бота после миграции данных.",
        ),
        "ADMIN_REPORTS_SEND_TIME": SettingMeta(
            icon="🕰️",
            description="Время ежедневной отправки отчетов администраторам.",
            format_hint="ЧЧ:ММ",
            example="09:00",
            dependencies=("ADMIN_REPORTS_ENABLED",),
        ),
    }

    SETTING_META_PREFIXES: tuple[tuple[str, SettingMeta], ...] = (
        (
            "PRICE_",
            SettingMeta(
                icon="💵",
                description="Стоимость в копейках. При вводе используйте рубли, бот сам конвертирует.",
                format_hint="Введите сумму в рублях",
                example="1490",
                unit="₽",
            ),
        ),
        (
            "YOOKASSA_",
            SettingMeta(
                icon="💳",
                description="Параметры интеграции YooKassa.",
                format_hint="Смотрите документацию YooKassa",
                example="",
            ),
        ),
        (
            "CRYPTOBOT_",
            SettingMeta(
                icon="🪙",
                description="Параметры CryptoBot: токен бота, валюты и вебхуки.",
                format_hint="Строковые значения",
                example="",
            ),
        ),
        (
            "PAL24_",
            SettingMeta(
                icon="🏦",
                description="Параметры PayPalych / PAL24.",
                format_hint="Строковые значения",
                example="",
            ),
        ),
        (
            "TRIBUTE_",
            SettingMeta(
                icon="🎁",
                description="Интеграция Tribute и данные вебхука.",
                format_hint="Строковые значения",
                example="",
            ),
        ),
        (
            "REMNAWAVE",
            SettingMeta(
                icon="🌐",
                description="Параметры RemnaWave API.",
                format_hint="Укажите URL и ключи",
                example="",
            ),
        ),
        (
            "REFERRAL_",
            SettingMeta(
                icon="👥",
                description="Настройки бонусов для реферальной программы.",
                format_hint="Целые числа в копейках или процентах",
                example="",
            ),
        ),
    )

    SETTING_ICON_OVERRIDES: Dict[str, str] = {
        "SUPPORT_MENU_ENABLED": "💬",
        "SUPPORT_SYSTEM_MODE": "🎫",
        "SUPPORT_TICKET_SLA_ENABLED": "⏱️",
        "SUPPORT_TICKET_SLA_MINUTES": "🕒",
        "MAINTENANCE_MODE": "🛠",
        "MAINTENANCE_MESSAGE": "📝",
        "YOOKASSA_ENABLED": "💳",
        "CRYPTOBOT_ENABLED": "🪙",
        "TELEGRAM_STARS_ENABLED": "⭐",
        "TRIAL_DURATION_DAYS": "🎁",
        "ENABLE_NOTIFICATIONS": "🔔",
        "DATABASE_MODE": "💾",
        "REMNAWAVE_API_URL": "🌐",
        "REMNAWAVE_API_KEY": "🔑",
        "REMNAWAVE_SECRET_KEY": "🛡",
    }

    INPUT_TYPE_OVERRIDES: Dict[str, SettingInputType] = {
        "AUTOPAY_WARNING_DAYS": SettingInputType.LIST,
        "AVAILABLE_SUBSCRIPTION_PERIODS": SettingInputType.LIST,
        "AVAILABLE_RENEWAL_PERIODS": SettingInputType.LIST,
        "ADMIN_IDS": SettingInputType.LIST,
        "CRYPTOBOT_ASSETS": SettingInputType.LIST,
        "ADMIN_REPORTS_SEND_TIME": SettingInputType.TIME,
        "BACKUP_TIME": SettingInputType.TIME,
        "BASE_SUBSCRIPTION_PRICE": SettingInputType.PRICE,
        "PRICE_PER_DEVICE": SettingInputType.PRICE,
        "MIN_BALANCE_FOR_AUTOPAY_KOPEKS": SettingInputType.PRICE,
    }

    LIST_SETTING_KEYS: set[str] = {
        "AVAILABLE_SUBSCRIPTION_PERIODS",
        "AVAILABLE_RENEWAL_PERIODS",
        "AUTOPAY_WARNING_DAYS",
        "CRYPTOBOT_ASSETS",
    }

    TIME_SETTING_KEYS: set[str] = {
        "ADMIN_REPORTS_SEND_TIME",
        "BACKUP_TIME",
    }

    PRICE_KEY_PREFIXES: tuple[str, ...] = ("PRICE_",)

    PRICE_KEY_SUFFIXES: tuple[str, ...] = ("_KOPEKS",)

    SENSITIVE_KEYS: set[str] = {
        "YOOKASSA_SECRET_KEY",
        "CRYPTOBOT_TOKEN",
        "REMNAWAVE_SECRET_KEY",
        "REMNAWAVE_PASSWORD",
        "MULENPAY_API_KEY",
        "PAL24_API_KEY",
        "TRIBUTE_API_KEY",
        "WEB_API_DEFAULT_TOKEN",
    }

    PRESETS: tuple[PresetDefinition, ...] = (
        PresetDefinition(
            key="recommended",
            label="Рекомендуемые настройки",
            description="Баланс между безопасностью, аналитикой и удобством для пользователей.",
            summary="Включает уведомления, контроль SLA и рекомендуемые цены.",
            changes={
                "SUPPORT_TICKET_SLA_ENABLED": True,
                "ENABLE_NOTIFICATIONS": True,
                "TRIAL_DURATION_DAYS": 3,
                "TRIAL_TRAFFIC_LIMIT_GB": 10,
                "MAINTENANCE_AUTO_ENABLE": True,
                "DEFAULT_AUTOPAY_DAYS_BEFORE": 3,
            },
        ),
        PresetDefinition(
            key="minimum",
            label="Минимальная конфигурация",
            description="Подходит для быстрых тестов и стендов разработки.",
            summary="Отключает платежи и уведомления, включает тестовый режим.",
            changes={
                "YOOKASSA_ENABLED": False,
                "ENABLE_NOTIFICATIONS": False,
                "TELEGRAM_STARS_ENABLED": False,
                "DEBUG": True if "DEBUG" in Settings.model_fields else False,
            },
        ),
        PresetDefinition(
            key="security",
            label="Максимальная безопасность",
            description="Повышенное логирование, отключение лишних интеграций и обязательные проверки.",
            summary="Усиленные уведомления, минимум внешних платежей и ручные подтверждения.",
            changes={
                "ENABLE_NOTIFICATIONS": True,
                "SUPPORT_TICKET_SLA_ENABLED": True,
                "YOOKASSA_SBP_ENABLED": False,
                "MAINTENANCE_AUTO_ENABLE": False,
            },
        ),
        PresetDefinition(
            key="testing",
            label="Для тестирования",
            description="Удобно для QA: включает платежные песочницы и логирование.",
            summary="Активирует режим отладки и тестовые платежные шлюзы.",
            changes={
                "YOOKASSA_ENABLED": False,
                "TRIBUTE_ENABLED": False,
                "MAINTENANCE_MODE": False,
                "ENABLE_NOTIFICATIONS": False,
            },
        ),
    )

    _definitions: Dict[str, SettingDefinition] = {}
    _original_values: Dict[str, Any] = settings.model_dump()
    _overrides_raw: Dict[str, Optional[str]] = {}
    _callback_tokens: Dict[str, str] = {}
    _token_to_key: Dict[str, str] = {}
    _choice_tokens: Dict[str, Dict[Any, str]] = {}
    _choice_token_lookup: Dict[str, Dict[str, Any]] = {}
    _definitions_by_category: Dict[str, List[SettingDefinition]] = {}

    @classmethod
    def _rebuild_category_index(cls) -> None:
        grouped: Dict[str, List[SettingDefinition]] = defaultdict(list)
        for definition in cls._definitions.values():
            grouped[definition.category_key].append(definition)

        for definitions in grouped.values():
            definitions.sort(key=lambda item: item.display_name)

        cls._definitions_by_category = dict(grouped)

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

            cls._definitions[key] = SettingDefinition(
                key=key,
                category_key=category_key or "other",
                category_label=category_label,
                python_type=python_type,
                type_label=type_label,
                is_optional=is_optional,
            )

            cls._register_callback_token(key)
            if key in cls.CHOICES:
                cls._ensure_choice_tokens(key)

        cls._rebuild_category_index()


    @classmethod
    def _resolve_category_key(cls, key: str) -> str:
        override = cls.CATEGORY_KEY_OVERRIDES.get(key)
        if override:
            return override

        for prefix, category in sorted(
            cls.CATEGORY_PREFIX_OVERRIDES.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if key.startswith(prefix):
                return category

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
        result: List[Tuple[str, str, int]] = []
        for category_key, items in cls._definitions_by_category.items():
            label = items[0].category_label
            result.append((category_key, label, len(items)))

        result.sort(key=lambda item: item[1])
        return result

    @classmethod
    def get_settings_for_category(cls, category_key: str) -> List[SettingDefinition]:
        cls.initialize_definitions()
        return list(cls._definitions_by_category.get(category_key, []))

    @classmethod
    def get_dashboard_items(
        cls,
    ) -> List[Tuple[DashboardCategory, List[SettingDefinition]]]:
        cls.initialize_definitions()
        grouped = cls._definitions_by_category
        assigned: set[str] = set()
        result: List[Tuple[DashboardCategory, List[SettingDefinition]]] = []

        for category in cls.DASHBOARD_CATEGORIES:
            if category.key == "other":
                continue

            seen_keys: set[str] = set()
            items: List[SettingDefinition] = []
            for service_category in category.service_categories:
                for definition in grouped.get(service_category, []):
                    if definition.key in seen_keys:
                        continue
                    items.append(definition)
                    seen_keys.add(definition.key)
                    assigned.add(definition.key)

            if items:
                items.sort(key=lambda definition: definition.display_name)
                result.append((category, items))

        remaining = [
            definition
            for definition in cls._definitions.values()
            if definition.key not in assigned
        ]
        if remaining:
            remaining.sort(key=lambda definition: definition.display_name)
            other_category = next(
                (category for category in cls.DASHBOARD_CATEGORIES if category.key == "other"),
                None,
            )
            if other_category:
                result.append((other_category, remaining))

        return result

    @classmethod
    def get_dashboard_category(cls, key: str) -> DashboardCategory:
        for category in cls.DASHBOARD_CATEGORIES:
            if category.key == key:
                return category
        raise KeyError(key)

    @classmethod
    def get_category_description(cls, category_key: str) -> str:
        return cls.CATEGORY_DESCRIPTIONS.get(
            category_key, "Описание появится позже."
        )

    @classmethod
    def _clone_meta(cls, meta: SettingMeta) -> SettingMeta:
        return replace(meta)

    @classmethod
    def _category_icon(cls, category_key: str) -> str:
        label = cls.CATEGORY_TITLES.get(category_key, "")
        if not label:
            return "⚙️"
        parts = label.split(" ", 1)
        if parts:
            candidate = parts[0]
            if re.match(r"^[\W_]+$", candidate):
                return candidate
        return "⚙️"

    @classmethod
    def _format_hint_for_type(cls, input_type: SettingInputType) -> str:
        hints = {
            SettingInputType.TOGGLE: "Переключатель Вкл/Выкл",
            SettingInputType.TEXT: "Текстовое значение",
            SettingInputType.NUMBER: "Целое или вещественное число",
            SettingInputType.PRICE: "Введите сумму в рублях",
            SettingInputType.LIST: "Список значений через запятую",
            SettingInputType.CHOICE: "Выбор из готовых вариантов",
            SettingInputType.TIME: "Формат ЧЧ:ММ",
        }
        return hints.get(input_type, "Значение")

    @classmethod
    def get_setting_meta(cls, key: str) -> SettingMeta:
        cls.initialize_definitions()
        meta = cls.SETTING_META_OVERRIDES.get(key)
        if meta:
            return cls._clone_meta(meta)

        for prefix, prefix_meta in cls.SETTING_META_PREFIXES:
            if key.startswith(prefix):
                return cls._clone_meta(prefix_meta)

        definition = cls.get_definition(key)
        icon = cls.SETTING_ICON_OVERRIDES.get(
            key, cls._category_icon(definition.category_key)
        )
        input_type = cls.get_input_type(key)
        return SettingMeta(
            icon=icon,
            description=cls.get_category_description(definition.category_key),
            format_hint=cls._format_hint_for_type(input_type),
        )

    @classmethod
    def get_setting_icon(cls, key: str) -> str:
        return cls.get_setting_meta(key).icon or "⚙️"

    @classmethod
    def get_input_type(cls, key: str) -> SettingInputType:
        cls.initialize_definitions()
        if key in cls.INPUT_TYPE_OVERRIDES:
            return cls.INPUT_TYPE_OVERRIDES[key]
        if cls.is_time_key(key):
            return SettingInputType.TIME
        if cls.is_list_key(key):
            return SettingInputType.LIST
        if cls.get_choice_options(key):
            return SettingInputType.CHOICE
        definition = cls.get_definition(key)
        if definition.python_type is bool:
            return SettingInputType.TOGGLE
        if definition.python_type is int:
            if cls.is_price_key(key):
                return SettingInputType.PRICE
            return SettingInputType.NUMBER
        if definition.python_type is float:
            if cls.is_price_key(key):
                return SettingInputType.PRICE
            return SettingInputType.NUMBER
        if cls.is_price_key(key):
            return SettingInputType.PRICE
        return SettingInputType.TEXT

    @classmethod
    def is_list_key(cls, key: str) -> bool:
        if key in cls.LIST_SETTING_KEYS:
            return True
        return key.endswith("_LIST") or key.endswith("_IDS") or key.endswith("_PERIODS")

    @classmethod
    def is_time_key(cls, key: str) -> bool:
        if key in cls.TIME_SETTING_KEYS:
            return True
        return key.endswith("_TIME") or key.endswith("_AT")

    @classmethod
    def is_price_key(cls, key: str) -> bool:
        if key in cls.INPUT_TYPE_OVERRIDES and cls.INPUT_TYPE_OVERRIDES[key] == SettingInputType.PRICE:
            return True
        if any(key.startswith(prefix) for prefix in cls.PRICE_KEY_PREFIXES):
            return True
        if any(key.endswith(suffix) for suffix in cls.PRICE_KEY_SUFFIXES):
            return True
        price_keys = {
            "BASE_SUBSCRIPTION_PRICE",
            "PRICE_PER_DEVICE",
            "REFERRAL_MINIMUM_TOPUP_KOPEKS",
            "REFERRAL_FIRST_TOPUP_BONUS_KOPEKS",
            "REFERRAL_INVITER_BONUS_KOPEKS",
            "REFERRED_USER_REWARD",
            "MIN_BALANCE_FOR_AUTOPAY_KOPEKS",
        }
        return key in price_keys

    @classmethod
    def mask_sensitive(cls, key: str, value: str) -> str:
        if key not in cls.SENSITIVE_KEYS:
            return value
        if value is None:
            return "—"
        value_str = str(value)
        if not value_str:
            return "—"
        length = len(value_str)
        visible = min(4, length)
        return "•" * max(0, length - visible) + value_str[-visible:]

    @classmethod
    def _format_price(cls, value: Any) -> str:
        try:
            amount = int(value)
        except (TypeError, ValueError):
            return str(value)
        rubles = amount / 100
        return f"{rubles:,.2f} ₽".replace(",", " ")

    @classmethod
    def _format_list(cls, value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, (list, tuple, set)):
            items = [str(item).strip() for item in value if str(item).strip()]
        else:
            return str(value)
        if not items:
            return "—"
        return "\n".join(f"• {item}" for item in items)

    @classmethod
    def format_value_display(
        cls, key: str, value: Any = None, *, short: bool = False
    ) -> str:
        if value is None:
            value = cls.get_current_value(key)
        if value is None:
            return "—"

        input_type = cls.get_input_type(key)
        if input_type == SettingInputType.TOGGLE:
            return "ВКЛЮЧЕН" if bool(value) else "ВЫКЛЮЧЕН"
        if input_type == SettingInputType.PRICE:
            return cls._format_price(value)
        if input_type == SettingInputType.TIME:
            return str(value)
        if input_type == SettingInputType.LIST:
            formatted = cls._format_list(value)
            return _truncate(formatted, 80) if short else formatted
        if isinstance(value, bool):
            return "ВКЛЮЧЕН" if value else "ВЫКЛЮЧЕН"
        if isinstance(value, (list, tuple, set)):
            formatted = cls._format_list(value)
            return _truncate(formatted, 80) if short else formatted
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str) and key in cls.SENSITIVE_KEYS:
            return cls.mask_sensitive(key, value)
        return str(value)

    @classmethod
    def get_status_emoji(cls, key: str) -> str:
        input_type = cls.get_input_type(key)
        value = cls.get_current_value(key)
        if input_type == SettingInputType.TOGGLE:
            return "✅" if bool(value) else "❌"
        if value in (None, "", []):
            return "⚪"
        return "🟢"

    @classmethod
    def summarize_definitions(
        cls, definitions: Iterable[SettingDefinition]
    ) -> Dict[str, int]:
        summary = {"active": 0, "disabled": 0, "empty": 0}
        for definition in definitions:
            emoji = cls.get_status_emoji(definition.key)
            if emoji in {"✅", "🟢"}:
                summary["active"] += 1
            elif emoji == "❌":
                summary["disabled"] += 1
            else:
                summary["empty"] += 1
        summary["total"] = sum(summary.values())
        return summary

    @classmethod
    def search_settings(cls, query: str) -> List[SettingDefinition]:
        cls.initialize_definitions()
        needle = query.lower().strip()
        if not needle:
            return []

        results: List[SettingDefinition] = []
        for definition in cls._definitions.values():
            haystacks = {
                definition.key.lower(),
                definition.display_name.lower(),
                cls.get_category_description(definition.category_key).lower(),
            }
            meta = cls.get_setting_meta(definition.key)
            if meta.description:
                haystacks.add(meta.description.lower())
            if meta.format_hint:
                haystacks.add(meta.format_hint.lower())
            if meta.example:
                haystacks.add(meta.example.lower())

            if any(needle in text for text in haystacks if text):
                results.append(definition)

        results.sort(key=lambda item: item.display_name)
        return results

    @classmethod
    def generate_env_snapshot(cls, include_defaults: bool = True) -> str:
        cls.initialize_definitions()
        lines: List[str] = [
            "# RemnaWave Bot configuration export",
            f"# Generated at {datetime.utcnow().isoformat()}Z",
            "",
        ]
        for definition in sorted(
            cls._definitions.values(), key=lambda item: item.key
        ):
            key = definition.key
            raw_value = cls._overrides_raw.get(key)
            if raw_value is None:
                if not include_defaults:
                    continue
                serialized = cls.serialize_value(key, cls.get_current_value(key))
                comment = "# default"
            else:
                serialized = raw_value
                comment = None

            if serialized is None:
                serialized = ""

            if comment:
                lines.append(comment)
            lines.append(f"{key}={serialized}")

        return "\n".join(lines)

    @classmethod
    def parse_env_content(cls, content: str) -> Dict[str, Optional[str]]:
        parsed: Dict[str, Optional[str]] = {}
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
                value = value[1:-1]
            parsed[key] = value or None
        return parsed

    @classmethod
    def build_import_diff(
        cls, data: Dict[str, Optional[str]]
    ) -> List[Dict[str, Any]]:
        cls.initialize_definitions()
        diff: List[Dict[str, Any]] = []

        for key, raw_value in data.items():
            if key not in cls._definitions:
                continue
            current_value = cls.get_current_value(key)
            try:
                parsed_value = cls.deserialize_value(key, raw_value)
            except Exception:
                continue

            if parsed_value == current_value:
                continue

            diff.append(
                {
                    "key": key,
                    "raw_value": raw_value,
                    "new_value": parsed_value,
                    "old_value": current_value,
                }
            )

        diff.sort(key=lambda item: item["key"])
        return diff

    @classmethod
    async def apply_import_diff(
        cls,
        db: AsyncSession,
        diff: Sequence[Dict[str, Any]],
        *,
        changed_by: Optional[int] = None,
        changed_by_username: Optional[str] = None,
        source: str = "import",
    ) -> None:
        for item in diff:
            key = item["key"]
            value = item["new_value"]
            if value is None:
                await cls.reset_value(
                    db,
                    key,
                    changed_by=changed_by,
                    changed_by_username=changed_by_username,
                    source=source,
                    reason="import-reset",
                )
            else:
                await cls.set_value(
                    db,
                    key,
                    value,
                    changed_by=changed_by,
                    changed_by_username=changed_by_username,
                    source=source,
                    reason="import",
                )

    @classmethod
    async def apply_preset(
        cls,
        db: AsyncSession,
        preset_key: str,
        *,
        changed_by: Optional[int] = None,
        changed_by_username: Optional[str] = None,
    ) -> PresetDefinition:
        for preset in cls.PRESETS:
            if preset.key == preset_key:
                for key, value in preset.changes.items():
                    if key not in cls._definitions:
                        continue
                    await cls.set_value(
                        db,
                        key,
                        value,
                        changed_by=changed_by,
                        changed_by_username=changed_by_username,
                        source=f"preset:{preset_key}",
                        reason="preset",
                    )
                return preset
        raise KeyError(preset_key)

    @classmethod
    async def get_recent_changes(
        cls, db: AsyncSession, limit: int = 10
    ) -> Sequence[SystemSettingChange]:
        return await get_recent_system_setting_changes(db, limit)

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
        formatted = cls.format_value_display(key, short=True)
        if formatted == "—":
            return formatted
        return _truncate(formatted)

    @classmethod
    def get_choice_options(cls, key: str) -> List[ChoiceOption]:
        cls.initialize_definitions()
        return cls.CHOICES.get(key, [])

    @classmethod
    def has_choices(cls, key: str) -> bool:
        return bool(cls.get_choice_options(key))

    @classmethod
    def get_callback_token(cls, key: str) -> str:
        cls.initialize_definitions()
        return cls._callback_tokens[key]

    @classmethod
    def resolve_callback_token(cls, token: str) -> str:
        cls.initialize_definitions()
        return cls._token_to_key[token]

    @classmethod
    def get_choice_token(cls, key: str, value: Any) -> Optional[str]:
        cls.initialize_definitions()
        cls._ensure_choice_tokens(key)
        return cls._choice_tokens.get(key, {}).get(value)

    @classmethod
    def resolve_choice_token(cls, key: str, token: str) -> Any:
        cls.initialize_definitions()
        cls._ensure_choice_tokens(key)
        return cls._choice_token_lookup.get(key, {})[token]

    @classmethod
    def _register_callback_token(cls, key: str) -> None:
        if key in cls._callback_tokens:
            return

        base = hashlib.blake2s(key.encode("utf-8"), digest_size=6).hexdigest()
        candidate = base
        counter = 1
        while candidate in cls._token_to_key and cls._token_to_key[candidate] != key:
            suffix = cls._encode_base36(counter)
            candidate = f"{base}{suffix}"[:16]
            counter += 1

        cls._callback_tokens[key] = candidate
        cls._token_to_key[candidate] = key

    @classmethod
    def _ensure_choice_tokens(cls, key: str) -> None:
        if key in cls._choice_tokens:
            return

        options = cls.CHOICES.get(key, [])
        value_to_token: Dict[Any, str] = {}
        token_to_value: Dict[str, Any] = {}

        for index, option in enumerate(options):
            token = cls._encode_base36(index)
            value_to_token[option.value] = token
            token_to_value[token] = option.value

        cls._choice_tokens[key] = value_to_token
        cls._choice_token_lookup[key] = token_to_value

    @staticmethod
    def _encode_base36(number: int) -> str:
        if number < 0:
            raise ValueError("number must be non-negative")
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
        if number == 0:
            return "0"
        result = []
        while number:
            number, rem = divmod(number, 36)
            result.append(alphabet[rem])
        return "".join(reversed(result))

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

        await cls._sync_default_web_api_token()

    @classmethod
    async def reload(cls) -> None:
        cls._overrides_raw.clear()
        await cls.initialize()

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

        input_type = cls.get_input_type(key)
        python_type = definition.python_type

        if input_type == SettingInputType.PRICE:
            normalized = text.replace(" ", "").replace(",", ".")
            try:
                amount = Decimal(normalized)
            except InvalidOperation as error:
                raise ValueError("Введите корректную сумму в рублях") from error
            amount = amount.quantize(Decimal("0.01"))
            kopeks = int(amount * 100)
            return kopeks

        if input_type == SettingInputType.TIME:
            if not re.match(r"^\d{1,2}:\d{2}$", text):
                raise ValueError("Используйте формат ЧЧ:ММ")
            hours, minutes = text.split(":", 1)
            hour = int(hours)
            minute = int(minutes)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("Часы от 0 до 23, минуты от 0 до 59")
            return f"{hour:02d}:{minute:02d}"

        if input_type == SettingInputType.LIST:
            items = [item.strip() for item in re.split(r"[,\n]+", text) if item.strip()]
            return ",".join(items)

        if python_type is bool:
            lowered = text.lower()
            if lowered in {"1", "true", "on", "yes", "да", "вкл", "enable", "enabled"}:
                return True
            if lowered in {"0", "false", "off", "no", "нет", "выкл", "disable", "disabled"}:
                return False
            raise ValueError("Введите 'true' или 'false' (или 'да'/'нет')")

        if python_type is int:
            parsed_value: Any = int(text)
        elif python_type is float:
            parsed_value = float(text.replace(",", "."))
        else:
            parsed_value = text

        choices = cls.get_choice_options(key)
        if choices:
            allowed_values = {option.value for option in choices}
            if python_type is str:
                lowered_map = {
                    str(option.value).lower(): option.value for option in choices
                }
                normalized = lowered_map.get(str(parsed_value).lower())
                if normalized is not None:
                    parsed_value = normalized
                elif parsed_value not in allowed_values:
                    readable = ", ".join(
                        f"{option.label} ({cls.format_value(option.value)})" for option in choices
                    )
                    raise ValueError(f"Доступные значения: {readable}")
            elif parsed_value not in allowed_values:
                readable = ", ".join(
                    f"{option.label} ({cls.format_value(option.value)})" for option in choices
                )
                raise ValueError(f"Доступные значения: {readable}")

        return parsed_value

    @classmethod
    async def set_value(
        cls,
        db: AsyncSession,
        key: str,
        value: Any,
        *,
        changed_by: Optional[int] = None,
        changed_by_username: Optional[str] = None,
        source: str = "bot_config",
        reason: Optional[str] = None,
    ) -> None:
        previous_raw = cls._overrides_raw.get(key)
        if previous_raw is None:
            previous_raw = cls.serialize_value(key, cls.get_current_value(key))

        raw_value = cls.serialize_value(key, value)
        await upsert_system_setting(db, key, raw_value)
        if raw_value is None:
            cls._overrides_raw.pop(key, None)
        else:
            cls._overrides_raw[key] = raw_value
        cls._apply_to_settings(key, value)

        await log_system_setting_change(
            db,
            key=key,
            old_value=previous_raw,
            new_value=raw_value,
            changed_by=changed_by,
            changed_by_username=changed_by_username,
            source=source,
            reason=reason,
        )

        if key in {"WEB_API_DEFAULT_TOKEN", "WEB_API_DEFAULT_TOKEN_NAME"}:
            await cls._sync_default_web_api_token()

    @classmethod
    async def reset_value(
        cls,
        db: AsyncSession,
        key: str,
        *,
        changed_by: Optional[int] = None,
        changed_by_username: Optional[str] = None,
        source: str = "bot_config",
        reason: Optional[str] = None,
    ) -> None:
        previous_raw = cls._overrides_raw.get(key)
        await delete_system_setting(db, key)
        cls._overrides_raw.pop(key, None)
        original = cls.get_original_value(key)
        cls._apply_to_settings(key, original)

        await log_system_setting_change(
            db,
            key=key,
            old_value=previous_raw,
            new_value=None,
            changed_by=changed_by,
            changed_by_username=changed_by_username,
            source=source,
            reason=reason or "reset",
        )

        if key in {"WEB_API_DEFAULT_TOKEN", "WEB_API_DEFAULT_TOKEN_NAME"}:
            await cls._sync_default_web_api_token()

    @classmethod
    def _apply_to_settings(cls, key: str, value: Any) -> None:
        try:
            setattr(settings, key, value)
        except Exception as error:
            logger.error("Не удалось применить значение %s=%s: %s", key, value, error)

    @staticmethod
    async def _sync_default_web_api_token() -> None:
        default_token = (settings.WEB_API_DEFAULT_TOKEN or "").strip()
        if not default_token:
            return

        success = await ensure_default_web_api_token()
        if not success:
            logger.warning(
                "Не удалось синхронизировать бутстрап токен веб-API после обновления настроек",
            )

    @classmethod
    def get_setting_summary(cls, key: str) -> Dict[str, Any]:
        definition = cls.get_definition(key)
        current = cls.get_current_value(key)
        original = cls.get_original_value(key)
        has_override = cls.has_override(key)

        return {
            "key": key,
            "name": definition.display_name,
            "current": cls.format_value_display(key, current),
            "original": cls.format_value_display(key, original),
            "type": definition.type_label,
            "category_key": definition.category_key,
            "category_label": definition.category_label,
            "has_override": has_override,
        }


bot_configuration_service = BotConfigurationService

