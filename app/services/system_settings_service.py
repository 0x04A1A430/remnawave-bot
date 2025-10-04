import hashlib
import json
import logging
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple, Type, Union, get_args, get_origin

from app.database.universal_migration import ensure_default_web_api_token

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
class SettingMetadata:
    display_name: Optional[str] = None
    description: Optional[str] = None
    format_hint: Optional[str] = None
    example: Optional[str] = None
    warning: Optional[str] = None
    dependencies: Optional[str] = None
    icon: Optional[str] = None
    input_type: Optional[str] = None
    unit: Optional[str] = None
    recommended: Optional[Any] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)
    secret: Optional[bool] = None
    category_description: Optional[str] = None


@dataclass(slots=True)
class SettingDefinition:
    key: str
    category_key: str
    category_label: str
    python_type: Type[Any]
    type_label: str
    is_optional: bool
    display_name_override: Optional[str] = None
    icon_override: Optional[str] = None

    @property
    def display_name(self) -> str:
        return self.display_name_override or _title_from_key(self.key)

    @property
    def icon(self) -> str:
        return self.icon_override or "⚙️"


@dataclass(slots=True)
class ChoiceOption:
    value: Any
    label: str
    description: Optional[str] = None


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

    CATEGORY_DESCRIPTIONS: Dict[str, str] = {
        "SUPPORT": "Настройки поддержки, тикетов и SLA.",
        "LOCALIZATION": "Языки и тексты интерфейса.",
        "MAINTENANCE": "Режим технических работ и сообщение для пользователей.",
        "CHANNEL": "Обязательная подписка на канал и переходы.",
        "ADMIN_NOTIFICATIONS": "Мгновенные уведомления администраторам.",
        "ADMIN_REPORTS": "Регулярные отчеты и сводки.",
        "TRIAL": "Параметры пробного периода.",
        "PAID_SUBSCRIPTION": "Базовые лимиты платных подписок.",
        "PERIODS": "Доступные периоды подписки.",
        "SUBSCRIPTION_PRICES": "Цены на подписки по периодам.",
        "TRAFFIC": "Лимиты и сброс трафика.",
        "TRAFFIC_PACKAGES": "Пакеты дополнительного трафика и их стоимость.",
        "DISCOUNTS": "Скидки и промо-настройки.",
        "PAYMENT": "Общие платежные параметры.",
        "TELEGRAM": "Telegram Stars и покупки в приложении.",
        "CRYPTOBOT": "Оплата через CryptoBot.",
        "YOOKASSA": "Интеграция YooKassa.",
        "TRIBUTE": "Интеграция Tribute.",
        "MULENPAY": "Интеграция MulenPay.",
        "PAL24": "Интеграция PayPalych (PAL24).",
        "REMNAWAVE": "Соединение с RemnaWave API.",
        "REFERRAL": "Реферальная программа и бонусы.",
        "AUTOPAY": "Автопродление подписок.",
        "INTERFACE_BRANDING": "Логотип и брендовые элементы.",
        "INTERFACE_SUBSCRIPTION": "Ссылка на подписку в интерфейсе.",
        "CONNECT_BUTTON": "Действие кнопки «Подключиться».",
        "HAPP": "Интеграция Happ и CryptoLink.",
        "SKIP": "Опции быстрого старта и пропуска шагов.",
        "ADDITIONAL": "Дополнительные конфигурации приложения.",
        "MINIAPP": "Настройки мини-приложения Telegram.",
        "DATABASE": "Выбор и подключение базы данных.",
        "POSTGRES": "Параметры PostgreSQL.",
        "SQLITE": "Путь и параметры SQLite.",
        "REDIS": "Настройки кеша Redis.",
        "MONITORING": "Мониторинг и хранение логов.",
        "NOTIFICATIONS": "Пользовательские уведомления и SLA.",
        "SERVER": "Проверка статуса серверов.",
        "BACKUP": "Резервное копирование.",
        "VERSION": "Проверка обновлений.",
        "LOG": "Логирование и файлы.",
        "WEBHOOK": "Webhook Telegram.",
        "WEB_API": "Встроенный Web API.",
        "DEBUG": "Режимы разработки и отладки.",
    }

    METADATA_KEY_OVERRIDES: Dict[str, SettingMetadata] = {
        "MAINTENANCE_MODE": SettingMetadata(
            display_name="Режим обслуживания",
            description="Переводит бота в режим технических работ и скрывает основные функции.",
            format_hint="Используйте кнопки включения/выключения или ответьте 'вкл/выкл'.",
            example="вкл",
            warning="Пользователи не смогут использовать бота, пока режим активен.",
            dependencies="MAINTENANCE_MESSAGE",
            icon="🔧",
            input_type="toggle",
            recommended=False,
        ),
        "MAINTENANCE_MESSAGE": SettingMetadata(
            description="Текст, который увидит пользователь во время технических работ.",
            format_hint="Обычный текст, поддерживается базовое форматирование Telegram.",
            example="🔧 Ведутся технические работы...",
            dependencies="MAINTENANCE_MODE",
            icon="💬",
        ),
        "DEBUG": SettingMetadata(
            display_name="Режим отладки",
            description="Включает подробные логи для разработчиков.",
            warning="Не держите включенным в продакшене — возможна утечка чувствительных данных.",
            icon="🐞",
            input_type="toggle",
            recommended=False,
        ),
        "ENABLE_NOTIFICATIONS": SettingMetadata(
            description="Включает отправку уведомлений пользователям о подписках, триалах и лимитах.",
            dependencies="NOTIFICATION_RETRY_ATTEMPTS, NOTIFICATION_CACHE_HOURS",
            icon="🔔",
            input_type="toggle",
            recommended=True,
        ),
        "ADMIN_NOTIFICATIONS_ENABLED": SettingMetadata(
            description="Рассылка мгновенных уведомлений администраторам о важных событиях.",
            dependencies="ADMIN_NOTIFICATIONS_CHAT_ID",
            icon="📣",
            input_type="toggle",
        ),
        "ADMIN_REPORTS_SEND_TIME": SettingMetadata(
            description="Время отправки ежедневных отчетов администраторам.",
            format_hint="Введите время в формате ЧЧ:ММ.",
            example="09:00",
            input_type="time",
            icon="🕒",
        ),
        "AVAILABLE_SUBSCRIPTION_PERIODS": SettingMetadata(
            description="Список доступных периодов подписки в днях.",
            format_hint="Перечислите значения через запятую.",
            example="30,90,180",
            input_type="list",
            icon="📅",
        ),
        "BASE_SUBSCRIPTION_PRICE": SettingMetadata(
            description="Базовая стоимость подписки в копейках.",
            format_hint="Введите цену в рублях, например 99 000.",
            example="99 000",
            input_type="price",
            unit="₽",
            icon="💰",
        ),
        "TRIAL_DURATION_DAYS": SettingMetadata(
            description="Количество дней пробного периода.",
            example="3",
            unit="дней",
            icon="🎁",
            recommended=3,
        ),
        "YOOKASSA_ENABLED": SettingMetadata(
            description="Включает прием платежей через YooKassa.",
            warning="Не активируйте без действующих ключей магазина.",
            dependencies="YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY",
            icon="💸",
            input_type="toggle",
        ),
        "CRYPTOBOT_ENABLED": SettingMetadata(
            description="Разрешает оплату через CryptoBot.",
            dependencies="CRYPTOBOT_API_TOKEN",
            icon="🪙",
            input_type="toggle",
        ),
        "REMNAWAVE_API_URL": SettingMetadata(
            description="Базовый URL панели RemnaWave.",
            format_hint="Полный адрес, например https://panel.remnawave.com",
            example="https://panel.remnawave.com",
            icon="🌐",
        ),
        "DATABASE_MODE": SettingMetadata(
            description="Выбор источника данных: auto, sqlite или postgresql.",
            format_hint="Введите одно из значений auto/sqlite/postgresql.",
            example="postgresql",
            icon="💾",
        ),
        "REFERRAL_COMMISSION_PERCENT": SettingMetadata(
            description="Процент комиссии для пригласившего пользователя.",
            unit="%",
            example="25",
            icon="👥",
        ),
        "BACKUP_TIME": SettingMetadata(
            description="Время запуска автоматического бэкапа.",
            format_hint="ЧЧ:ММ, 24-часовой формат.",
            example="03:00",
            input_type="time",
            icon="💾",
        ),
        "WEB_API_ENABLED": SettingMetadata(
            description="Включает встроенный Web API для интеграций.",
            warning="Убедитесь, что токены доступа настроены и хранятся безопасно.",
            icon="🌐",
            input_type="toggle",
        ),
        "ENABLE_DEEP_LINKS": SettingMetadata(
            description="Позволяет открывать бота через глубокие ссылки.",
            warning="Отключение сделает недоступными промо-ссылки и мини-приложение.",
            icon="🔗",
            input_type="toggle",
        ),
    }

    METADATA_PREFIX_HINTS: Tuple[Tuple[str, SettingMetadata], ...] = (
        (
            "PRICE_",
            SettingMetadata(
                icon="💰",
                input_type="price",
                format_hint="Введите цену в рублях, разделяя тысячи пробелами.",
                example="9 990",
                unit="₽",
            ),
        ),
        (
            "YOOKASSA_",
            SettingMetadata(
                icon="💸",
                category_description="Настройка платежей через YooKassa.",
            ),
        ),
        (
            "CRYPTOBOT_",
            SettingMetadata(icon="🪙", category_description="Интеграция с CryptoBot."),
        ),
        (
            "MULENPAY_",
            SettingMetadata(icon="💳", category_description="Интеграция MulenPay."),
        ),
        (
            "PAL24_",
            SettingMetadata(icon="🏦", category_description="Интеграция PayPalych (PAL24)."),
        ),
        (
            "TRIBUTE_",
            SettingMetadata(icon="🎁", category_description="Настройки Tribute."),
        ),
        (
            "TELEGRAM_STARS",
            SettingMetadata(icon="⭐", category_description="Платежи через Telegram Stars."),
        ),
        (
            "TRIAL_",
            SettingMetadata(icon="🎁", category_description="Параметры пробного периода."),
        ),
        (
            "REFERRAL_",
            SettingMetadata(icon="👥", category_description="Реферальная программа."),
        ),
        (
            "BACKUP_",
            SettingMetadata(icon="💾", category_description="Автоматические резервные копии."),
        ),
    )

    METADATA_SUFFIX_HINTS: Tuple[Tuple[str, SettingMetadata], ...] = (
        (
            "_ENABLED",
            SettingMetadata(
                input_type="toggle",
                format_hint="Используйте кнопки или отправьте 'вкл'/'выкл'.",
                example="вкл",
            ),
        ),
        (
            "_IDS",
            SettingMetadata(
                input_type="list",
                format_hint="Перечислите значения через запятую.",
                example="123456789,987654321",
            ),
        ),
        (
            "_PERCENT",
            SettingMetadata(
                input_type="number",
                unit="%",
                format_hint="Введите целое число от 0 до 100.",
                example="25",
            ),
        ),
        (
            "_KOPEKS",
            SettingMetadata(
                input_type="price",
                unit="₽",
                format_hint="Введите цену в рублях — бот сконвертирует в копейки.",
                example="500",
            ),
        ),
        (
            "_HOURS",
            SettingMetadata(
                unit="часов",
                input_type="number",
                example="24",
            ),
        ),
        (
            "_MINUTES",
            SettingMetadata(
                unit="минут",
                input_type="number",
                example="15",
            ),
        ),
        (
            "_SECONDS",
            SettingMetadata(
                unit="секунд",
                input_type="number",
                example="60",
            ),
        ),
        (
            "_DAYS",
            SettingMetadata(
                unit="дней",
                input_type="number",
                example="30",
            ),
        ),
        (
            "_TIME",
            SettingMetadata(
                input_type="time",
                format_hint="Введите время в формате ЧЧ:ММ.",
                example="12:30",
            ),
        ),
        (
            "_URL",
            SettingMetadata(
                input_type="text",
                format_hint="Полный URL, начинающийся с http или https.",
                example="https://example.com",
            ),
        ),
    )

    SECRET_KEY_PATTERNS: Tuple[str, ...] = (
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "API_KEY",
        "PRIVATE_KEY",
    )

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

    _definitions: Dict[str, SettingDefinition] = {}
    _original_values: Dict[str, Any] = settings.model_dump()
    _overrides_raw: Dict[str, Optional[str]] = {}
    _callback_tokens: Dict[str, str] = {}
    _token_to_key: Dict[str, str] = {}
    _choice_tokens: Dict[str, Dict[Any, str]] = {}
    _choice_token_lookup: Dict[str, Dict[str, Any]] = {}
    _metadata_cache: Dict[str, SettingMetadata] = {}
    _history: deque[Dict[str, Any]] = deque(maxlen=10)

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

            definition = SettingDefinition(
                key=key,
                category_key=category_key or "other",
                category_label=category_label,
                python_type=python_type,
                type_label=type_label,
                is_optional=is_optional,
            )

            metadata = cls._build_metadata(definition)
            if metadata.display_name:
                definition.display_name_override = metadata.display_name
            if metadata.icon:
                definition.icon_override = metadata.icon

            cls._definitions[key] = definition
            cls._metadata_cache[key] = metadata

            cls._register_callback_token(key)
            if key in cls.CHOICES:
                cls._ensure_choice_tokens(key)


    @classmethod
    def _build_metadata(cls, definition: SettingDefinition) -> SettingMetadata:
        key = definition.key
        base_metadata = SettingMetadata(
            icon=cls._extract_category_icon(definition.category_label),
            category_description=cls.CATEGORY_DESCRIPTIONS.get(definition.category_key),
        )

        metadata = cls._merge_metadata(base_metadata, cls._metadata_for_python_type(definition))

        for prefix, hint in cls.METADATA_PREFIX_HINTS:
            if key.startswith(prefix):
                metadata = cls._merge_metadata(metadata, hint)

        for suffix, hint in cls.METADATA_SUFFIX_HINTS:
            if key.endswith(suffix):
                metadata = cls._merge_metadata(metadata, hint)

        key_override = cls.METADATA_KEY_OVERRIDES.get(key)
        if key_override:
            metadata = cls._merge_metadata(metadata, key_override)

        if metadata.display_name is None:
            metadata.display_name = cls._guess_display_name(key)

        if metadata.description is None:
            metadata.description = cls._default_description(definition)

        if metadata.input_type is None:
            metadata.input_type = cls._default_input_type(definition)

        if metadata.format_hint is None:
            metadata.format_hint = cls._default_format_hint(metadata)

        if metadata.example is None:
            metadata.example = cls._default_example(metadata)

        if metadata.secret is None and cls._is_secret_key(key):
            metadata.secret = True

        return metadata

    @classmethod
    def _metadata_for_python_type(cls, definition: SettingDefinition) -> SettingMetadata:
        python_type = definition.python_type
        if python_type is bool:
            return SettingMetadata(
                input_type="toggle",
                format_hint="Используйте кнопки включения/выключения или ответьте 'вкл'/'выкл'.",
                example="вкл",
            )
        if python_type is int:
            return SettingMetadata(
                input_type="number",
                format_hint="Введите целое число.",
                example="10",
            )
        if python_type is float:
            return SettingMetadata(
                input_type="number",
                format_hint="Введите число, можно использовать запятую.",
                example="1,5",
            )
        return SettingMetadata(
            input_type="text",
            format_hint="Введите текстовое значение.",
            example="Пример",
        )

    @staticmethod
    def _merge_metadata(base: SettingMetadata, override: SettingMetadata) -> SettingMetadata:
        if override is base:
            return base

        merged = SettingMetadata(
            display_name=override.display_name or base.display_name,
            description=override.description or base.description,
            format_hint=override.format_hint or base.format_hint,
            example=override.example or base.example,
            warning=override.warning or base.warning,
            dependencies=override.dependencies or base.dependencies,
            icon=override.icon or base.icon,
            input_type=override.input_type or base.input_type,
            unit=override.unit or base.unit,
            recommended=override.recommended if override.recommended is not None else base.recommended,
            secret=override.secret if override.secret is not None else base.secret,
            category_description=override.category_description or base.category_description,
        )

        if base.tags or override.tags:
            tags: List[str] = list(base.tags)
            for tag in override.tags:
                if tag not in tags:
                    tags.append(tag)
            merged.tags = tuple(tags)

        return merged

    @staticmethod
    def _extract_category_icon(category_label: str) -> Optional[str]:
        if not category_label:
            return None
        stripped = category_label.strip()
        if not stripped:
            return None
        first_char = stripped[0]
        if first_char.isascii():
            return None
        return first_char

    @staticmethod
    def _guess_display_name(key: str) -> Optional[str]:
        if key.endswith("_ENABLED"):
            base = key[:-8]
            return _title_from_key(base)
        if key.endswith("_URL"):
            base = key[:-4]
            return f"{_title_from_key(base)} URL"
        if key.endswith("_ID"):
            base = key[:-3]
            return f"{_title_from_key(base)} ID"
        if key.endswith("_TIME"):
            base = key[:-5]
            return f"{_title_from_key(base)} Время"
        return _title_from_key(key)

    @staticmethod
    def _default_description(definition: SettingDefinition) -> str:
        return (
            f"Настройка «{definition.display_name}» в категории "
            f"{definition.category_label}."
        )

    @staticmethod
    def _default_input_type(definition: SettingDefinition) -> str:
        if definition.python_type is bool:
            return "toggle"
        if definition.python_type in {int, float}:
            return "number"
        return "text"

    @staticmethod
    def _default_format_hint(metadata: SettingMetadata) -> str:
        mapping = {
            "toggle": "Используйте кнопки включения/выключения.",
            "number": "Введите числовое значение.",
            "price": "Введите сумму в рублях.",
            "time": "Введите время в формате ЧЧ:ММ.",
            "list": "Перечислите значения через запятую.",
            "text": "Введите текстовое значение.",
        }
        return mapping.get(metadata.input_type or "text", "Введите значение и отправьте сообщением.")

    @staticmethod
    def _default_example(metadata: SettingMetadata) -> str:
        mapping = {
            "toggle": "вкл",
            "number": "10",
            "price": "9 990",
            "time": "12:00",
            "list": "значение1, значение2",
            "text": "пример",
        }
        return mapping.get(metadata.input_type or "text", "пример")

    @classmethod
    def get_metadata(cls, key: str) -> SettingMetadata:
        cls.initialize_definitions()
        metadata = cls._metadata_cache.get(key)
        if metadata is None:
            definition = cls._definitions[key]
            metadata = cls._build_metadata(definition)
            cls._metadata_cache[key] = metadata
        return metadata

    @classmethod
    def _is_secret_key(cls, key: str) -> bool:
        upper = key.upper()
        return any(pattern in upper for pattern in cls.SECRET_KEY_PATTERNS)

    @staticmethod
    def _mask_secret(value: Any) -> str:
        text = str(value or "")
        if not text:
            return "—"
        if len(text) <= 4:
            return "••••"
        return "••••••••" + text[-4:]

    @staticmethod
    def _format_rubles(raw_value: Any) -> str:
        try:
            dec_value = Decimal(str(raw_value))
        except InvalidOperation:
            return str(raw_value)

        rubles = dec_value / Decimal(100)
        quantized = rubles.quantize(Decimal("0.01"))

        if quantized == quantized.to_integral_value():
            integer = int(quantized)
            formatted = f"{integer:,}".replace(",", " ")
        else:
            formatted = f"{quantized:.2f}".replace(",", " ")

        return f"{formatted} ₽"

    @staticmethod
    def _parse_time(text: str) -> str:
        if not re.fullmatch(r"\d{1,2}:\d{2}", text):
            raise ValueError("Используйте формат ЧЧ:ММ")
        hours_str, minutes_str = text.split(":", 1)
        hours = int(hours_str)
        minutes = int(minutes_str)
        if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
            raise ValueError("Часы должны быть 0-23, минуты 0-59")
        return f"{hours:02d}:{minutes:02d}"

    @staticmethod
    def _parse_price(text: str) -> int:
        normalized = text.replace(" ", "").replace("₽", "").replace(",", ".")
        if not normalized:
            raise ValueError("Введите сумму в рублях")
        try:
            value = Decimal(normalized)
        except InvalidOperation as error:
            raise ValueError("Некорректное значение цены") from error
        if value < 0:
            raise ValueError("Цена не может быть отрицательной")
        kopeks = (value * 100).quantize(Decimal("1"))
        return int(kopeks)

    @staticmethod
    def _parse_list(text: str) -> str:
        if not text:
            return ""
        normalized = text.replace("\n", ",")
        items = [item.strip() for item in normalized.split(",") if item.strip()]
        return ",".join(items)

    @classmethod
    def format_setting_value(
        cls,
        key: str,
        value: Any,
        *,
        include_unit: bool = True,
        mask_secrets: bool = True,
    ) -> str:
        metadata = cls.get_metadata(key)
        definition = cls.get_definition(key)

        if value is None or value == "":
            return "—"

        if mask_secrets and (metadata.secret or cls._is_secret_key(key)):
            return cls._mask_secret(value)

        input_type = metadata.input_type or cls._default_input_type(definition)
        unit = metadata.unit if include_unit else None

        if input_type == "toggle":
            return "ВКЛЮЧЕНО" if bool(value) else "ВЫКЛЮЧЕНО"

        if input_type == "price":
            return cls._format_rubles(value)

        if input_type == "list":
            if isinstance(value, str):
                items = [item.strip() for item in value.split(",") if item.strip()]
            elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
                items = [str(item).strip() for item in value]
            else:
                items = [str(value)]
            if not items:
                return "—"
            return " • ".join(items)

        if input_type == "time":
            return str(value)

        if input_type == "number":
            try:
                number = Decimal(str(value))
                if number == number.to_integral_value():
                    rendered = f"{int(number)}"
                else:
                    rendered = str(number).replace(".", ",")
            except InvalidOperation:
                rendered = str(value)
            if unit:
                return f"{rendered} {unit}"
            return rendered

        if unit:
            return f"{value} {unit}"

        return str(value)

    @classmethod
    def get_state_icon(cls, key: str, value: Any) -> str:
        metadata = cls.get_metadata(key)
        definition = cls.get_definition(key)
        input_type = metadata.input_type or cls._default_input_type(definition)

        if input_type == "toggle":
            return "✅" if bool(value) else "❌"
        if value in (None, "", [], {}):
            return "⚪"
        return "🟢"

    @classmethod
    def get_setting_dashboard_entry(cls, key: str) -> Dict[str, Any]:
        definition = cls.get_definition(key)
        metadata = cls.get_metadata(key)
        current = cls.get_current_value(key)
        return {
            "key": key,
            "name": definition.display_name,
            "icon": metadata.icon or definition.icon,
            "state_icon": cls.get_state_icon(key, current),
            "value": cls.format_setting_value(key, current),
            "has_override": cls.has_override(key),
            "description": metadata.description or cls._default_description(definition),
            "recommended": metadata.recommended,
            "unit": metadata.unit,
            "category_description": metadata.category_description,
        }

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
        formatted = cls.format_setting_value(key, value)
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

        metadata = cls.get_metadata(key)
        input_type = metadata.input_type or cls._default_input_type(definition)

        python_type = definition.python_type

        if input_type == "toggle" or python_type is bool:
            lowered = text.lower()
            if lowered in {"1", "true", "on", "yes", "да", "вкл", "enable", "enabled"}:
                return True
            if lowered in {"0", "false", "off", "no", "нет", "выкл", "disable", "disabled"}:
                return False
            raise ValueError("Введите 'true' или 'false' (или 'да'/'нет')")

        if input_type == "price":
            parsed_value = cls._parse_price(text)
        elif input_type == "time":
            parsed_value = cls._parse_time(text)
        elif input_type == "list":
            parsed_value = cls._parse_list(text)
        elif python_type is int:
            parsed_value = int(text)
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
        actor: Optional[str] = None,
        reason: str = "manual",
    ) -> None:
        old_value = cls.get_current_value(key)
        raw_value = cls.serialize_value(key, value)
        await upsert_system_setting(db, key, raw_value)
        cls._overrides_raw[key] = raw_value
        cls._apply_to_settings(key, value)

        cls._record_history(key, old_value, value, actor=actor, reason=reason)
        logger.info(
            "Настройка %s обновлена: %s → %s (%s)",
            key,
            cls.format_setting_value(key, old_value),
            cls.format_setting_value(key, value),
            actor or "system",
        )

        if key in {"WEB_API_DEFAULT_TOKEN", "WEB_API_DEFAULT_TOKEN_NAME"}:
            await cls._sync_default_web_api_token()

    @classmethod
    async def reset_value(
        cls,
        db: AsyncSession,
        key: str,
        *,
        actor: Optional[str] = None,
        reason: str = "reset",
    ) -> None:
        old_value = cls.get_current_value(key)
        await delete_system_setting(db, key)
        cls._overrides_raw.pop(key, None)
        original = cls.get_original_value(key)
        cls._apply_to_settings(key, original)

        cls._record_history(key, old_value, original, actor=actor, reason=reason)
        logger.info(
            "Настройка %s сброшена: %s → %s (%s)",
            key,
            cls.format_setting_value(key, old_value),
            cls.format_setting_value(key, original),
            actor or "system",
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
            "current": cls.format_setting_value(key, current),
            "original": cls.format_setting_value(key, original),
            "type": definition.type_label,
            "category_key": definition.category_key,
            "category_label": definition.category_label,
            "has_override": has_override,
        }

    @classmethod
    def _record_history(
        cls,
        key: str,
        old_value: Any,
        new_value: Any,
        *,
        actor: Optional[str],
        reason: str,
    ) -> None:
        definition = cls.get_definition(key)
        entry = {
            "timestamp": datetime.utcnow(),
            "key": key,
            "name": definition.display_name,
            "old": cls.format_setting_value(key, old_value),
            "new": cls.format_setting_value(key, new_value),
            "actor": actor,
            "reason": reason,
        }
        cls._history.appendleft(entry)

    @classmethod
    def get_history(cls) -> List[Dict[str, Any]]:
        return list(cls._history)

    @classmethod
    def generate_env_dump(cls, *, include_secrets: bool = True) -> str:
        cls.initialize_definitions()
        lines: List[str] = []
        for key in sorted(cls._definitions.keys()):
            value = cls.get_current_value(key)
            raw = cls.serialize_value(key, value)
            if raw is None:
                continue
            if not include_secrets and cls._is_secret_key(key):
                lines.append(f"{key}=<hidden>")
            else:
                escaped = raw.replace("\\", "\\\\").replace("\n", "\\n")
                lines.append(f"{key}={escaped}")
        return "\n".join(lines) + "\n"

    @classmethod
    def parse_env_dump(cls, content: str) -> Dict[str, Any]:
        cls.initialize_definitions()
        result: Dict[str, Any] = {}
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if key not in cls._definitions:
                continue
            value_text = raw_value.strip().strip('"').strip("'")
            value_text = value_text.replace("\\n", "\n")
            try:
                parsed_value = cls.parse_user_value(key, value_text)
            except ValueError as error:
                raise ValueError(f"{key}: {error}") from error
            result[key] = parsed_value
        return result

    @classmethod
    def search_settings(cls, query: str, limit: int = 12) -> List[str]:
        cls.initialize_definitions()
        normalized = (query or "").strip().lower()
        if not normalized:
            return []

        tokens = [token for token in re.split(r"\s+", normalized) if token]
        if not tokens:
            return []

        scored: List[Tuple[float, str]] = []
        for key, definition in cls._definitions.items():
            if key in cls.EXCLUDED_KEYS:
                continue

            metadata = cls.get_metadata(key)
            haystacks: List[str] = [
                definition.display_name.lower(),
                definition.category_label.lower(),
                key.lower(),
            ]
            if metadata.description:
                haystacks.append(metadata.description.lower())
            if metadata.tags:
                haystacks.extend(tag.lower() for tag in metadata.tags)

            score = 0.0
            for token in tokens:
                for haystack in haystacks:
                    if token == haystack:
                        score += 5.0
                    elif token in haystack:
                        score += 1.0 + (len(token) / max(len(haystack), 1))

            if score > 0:
                if definition.category_key.startswith("PAYMENT"):
                    score += 0.1
                scored.append((score, key))

        scored.sort(key=lambda item: (-item[0], cls._definitions[item[1]].display_name.lower()))
        return [key for _, key in scored[:limit]]


bot_configuration_service = BotConfigurationService

