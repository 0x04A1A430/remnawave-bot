import io
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from aiogram import Dispatcher, F, types
from aiogram.filters import BaseFilter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SystemSetting, User
from app.database.crud.system_setting import delete_system_setting, upsert_system_setting
from app.localization.texts import get_texts
from app.config import settings
from app.services.remnawave_service import RemnaWaveService
from app.services.payment_service import PaymentService
from app.services.tribute_service import TributeService
from app.services.system_settings_service import bot_configuration_service
from app.states import BotConfigStates
from app.utils.decorators import admin_required, error_handler
from app.utils.currency_converter import currency_converter
from app.external.telegram_stars import TelegramStarsService


SETTINGS_PAGE_SIZE = 8


@dataclass(frozen=True)
class SpecCategory:
    key: str
    title: str
    description: str
    icon: str
    category_keys: Tuple[str, ...]


SPEC_CATEGORIES: Tuple[SpecCategory, ...] = (
    SpecCategory(
        key="core",
        title="🤖 Основные",
        description="Базовые настройки бота и поведение по умолчанию.",
        icon="🤖",
        category_keys=("CHANNEL",),
    ),
    SpecCategory(
        key="support",
        title="💬 Поддержка",
        description="Система тикетов, контакты и SLA.",
        icon="💬",
        category_keys=("SUPPORT",),
    ),
    SpecCategory(
        key="payments",
        title="💳 Платежные системы",
        description="Управление всеми способами оплаты и текстами чеков.",
        icon="💳",
        category_keys=("PAYMENT", "TELEGRAM", "CRYPTOBOT", "YOOKASSA", "TRIBUTE", "MULENPAY", "PAL24"),
    ),
    SpecCategory(
        key="subscriptions",
        title="📅 Подписки и цены",
        description="Периоды, тарифы, трафик и автопродление.",
        icon="📅",
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
    SpecCategory(
        key="trial",
        title="🎁 Пробный период",
        description="Настройки бесплатного доступа и ограничений.",
        icon="🎁",
        category_keys=("TRIAL",),
    ),
    SpecCategory(
        key="referral",
        title="👥 Реферальная программа",
        description="Бонусы, комиссии и уведомления за приглашения.",
        icon="👥",
        category_keys=("REFERRAL",),
    ),
    SpecCategory(
        key="notifications",
        title="🔔 Уведомления",
        description="Админ-уведомления, отчеты и SLA.",
        icon="🔔",
        category_keys=("ADMIN_NOTIFICATIONS", "ADMIN_REPORTS", "NOTIFICATIONS"),
    ),
    SpecCategory(
        key="branding",
        title="🎨 Интерфейс и брендинг",
        description="Логотип, тексты, языки и Mini App.",
        icon="🎨",
        category_keys=(
            "LOCALIZATION",
            "INTERFACE_BRANDING",
            "INTERFACE_SUBSCRIPTION",
            "CONNECT_BUTTON",
            "HAPP",
            "SKIP",
            "ADDITIONAL",
            "MINIAPP",
        ),
    ),
    SpecCategory(
        key="database",
        title="💾 База данных",
        description="Настройки PostgreSQL, SQLite и Redis.",
        icon="💾",
        category_keys=("DATABASE", "POSTGRES", "SQLITE", "REDIS"),
    ),
    SpecCategory(
        key="remnawave",
        title="🌐 RemnaWave API",
        description="Интеграция с панелью RemnaWave и тест соединения.",
        icon="🌐",
        category_keys=("REMNAWAVE",),
    ),
    SpecCategory(
        key="servers",
        title="📊 Статус серверов",
        description="Мониторинг инфраструктуры и внешние метрики.",
        icon="📊",
        category_keys=("SERVER", "MONITORING"),
    ),
    SpecCategory(
        key="maintenance",
        title="🔧 Обслуживание",
        description="Техработы, резервные копии и проверки обновлений.",
        icon="🔧",
        category_keys=("MAINTENANCE", "BACKUP", "VERSION"),
    ),
    SpecCategory(
        key="advanced",
        title="⚡ Расширенные",
        description="Webhook, Web API, логирование и режим разработки.",
        icon="⚡",
        category_keys=("WEBHOOK", "WEB_API", "LOG", "DEBUG"),
    ),
)

SPEC_CATEGORY_MAP: Dict[str, SpecCategory] = {category.key: category for category in SPEC_CATEGORIES}
CATEGORY_TO_SPEC: Dict[str, str] = {}
for category in SPEC_CATEGORIES:
    for cat_key in category.category_keys:
        CATEGORY_TO_SPEC[cat_key] = category.key

MAPPED_CATEGORY_KEYS = set(CATEGORY_TO_SPEC.keys())
DEFAULT_SPEC_KEY = SPEC_CATEGORIES[0].key
CUSTOM_PRESET_PREFIX = "botcfg_preset::"

BUILTIN_PRESETS: Dict[str, Dict[str, Any]] = {
    "recommended": {
        "title": "Рекомендуемые настройки",
        "description": "Сбалансированная конфигурация для стабильной работы.",
        "changes": {
            "SUPPORT_MENU_ENABLED": True,
            "SUPPORT_SYSTEM_MODE": "both",
            "SUPPORT_TICKET_SLA_ENABLED": True,
            "ENABLE_NOTIFICATIONS": True,
            "ADMIN_REPORTS_ENABLED": True,
            "ADMIN_REPORTS_SEND_TIME": "09:00",
            "REFERRAL_NOTIFICATIONS_ENABLED": True,
        },
    },
    "minimal": {
        "title": "Минимальная конфигурация",
        "description": "Только основные функции без дополнительных уведомлений.",
        "changes": {
            "SUPPORT_MENU_ENABLED": False,
            "ENABLE_NOTIFICATIONS": False,
            "ADMIN_NOTIFICATIONS_ENABLED": False,
            "ADMIN_REPORTS_ENABLED": False,
            "TRIAL_DURATION_DAYS": 0,
        },
    },
    "secure": {
        "title": "Максимальная безопасность",
        "description": "Ограничение внешнего доступа и усиленный контроль.",
        "changes": {
            "ENABLE_DEEP_LINKS": False,
            "WEB_API_ENABLED": False,
            "MAINTENANCE_AUTO_ENABLE": True,
            "CONNECT_BUTTON_MODE": "guide",
            "REFERRAL_NOTIFICATIONS_ENABLED": False,
        },
    },
    "testing": {
        "title": "Для тестирования",
        "description": "Удобно для стендов и проверки интеграций.",
        "changes": {
            "DEBUG": True,
            "ENABLE_NOTIFICATIONS": False,
            "TELEGRAM_STARS_ENABLED": True,
            "YOOKASSA_ENABLED": False,
            "CRYPTOBOT_ENABLED": False,
            "MAINTENANCE_MODE": False,
        },
    },
}

QUICK_ACTIONS: Dict[str, Dict[str, Any]] = {
    "enable_notifications": {
        "title": "🟢 Включить все уведомления",
        "description": "Активирует пользовательские и админские уведомления.",
        "changes": {
            "ENABLE_NOTIFICATIONS": True,
            "ADMIN_NOTIFICATIONS_ENABLED": True,
            "ADMIN_REPORTS_ENABLED": True,
            "REFERRAL_NOTIFICATIONS_ENABLED": True,
        },
    },
    "disable_payments": {
        "title": "⚪ Отключить все платежи",
        "description": "Мгновенно выключает все интеграции оплаты.",
        "changes": {
            "YOOKASSA_ENABLED": False,
            "CRYPTOBOT_ENABLED": False,
            "MULENPAY_ENABLED": False,
            "PAL24_ENABLED": False,
            "TRIBUTE_ENABLED": False,
            "TELEGRAM_STARS_ENABLED": False,
        },
    },
    "enable_maintenance": {
        "title": "🔧 Включить режим обслуживания",
        "description": "Переводит бота в режим техработ с текущим сообщением.",
        "changes": {
            "MAINTENANCE_MODE": True,
        },
    },
}


def _format_actor(db_user: User) -> str:
    try:
        full_name = db_user.full_name  # type: ignore[attr-defined]
    except AttributeError:
        full_name = None
    if not full_name:
        full_name = db_user.username or f"ID{db_user.telegram_id}"
    return f"{full_name}#{db_user.telegram_id}"


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


def _iter_all_definitions() -> Iterable[Any]:
    for category_key, _, _ in bot_configuration_service.get_categories():
        for definition in bot_configuration_service.get_settings_for_category(category_key):
            yield definition


def _preset_storage_key(slug: str) -> str:
    return f"{CUSTOM_PRESET_PREFIX}{slug}"


async def _load_custom_presets(db: AsyncSession) -> Dict[str, Dict[str, Any]]:
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.like(f"{CUSTOM_PRESET_PREFIX}%"))
    )
    presets: Dict[str, Dict[str, Any]] = {}
    for setting in result.scalars():
        slug = setting.key[len(CUSTOM_PRESET_PREFIX) :]
        try:
            payload = json.loads(setting.value or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        changes = payload.get("changes")
        if not isinstance(changes, dict):
            continue
        presets[slug] = {
            "title": payload.get("title") or f"Пользовательский ({slug})",
            "description": payload.get("description") or "Сохраненный администратором пресет.",
            "changes": changes,
        }
    return presets


def _parse_spec_payload(payload: str) -> Tuple[str, int]:
    parts = payload.split(":")
    spec_key = parts[1] if len(parts) > 1 else DEFAULT_SPEC_KEY
    try:
        page = max(1, int(parts[2]))
    except (IndexError, ValueError):
        page = 1
    return spec_key, page


def _get_spec_settings(spec_key: str) -> List[Any]:
    category = SPEC_CATEGORY_MAP.get(spec_key)
    if not category:
        return []

    definitions: List[Any] = []
    seen: set[str] = set()

    for category_key in category.category_keys:
        for definition in bot_configuration_service.get_settings_for_category(category_key):
            if definition.key not in seen:
                definitions.append(definition)
                seen.add(definition.key)

    if spec_key == "advanced":
        for category_key, _, _ in bot_configuration_service.get_categories():
            if category_key not in MAPPED_CATEGORY_KEYS:
                for definition in bot_configuration_service.get_settings_for_category(category_key):
                    if definition.key not in seen:
                        definitions.append(definition)
                        seen.add(definition.key)

    definitions.sort(key=lambda definition: definition.display_name.lower())
    return definitions


async def _apply_changeset(
    db: AsyncSession,
    changes: Dict[str, Any],
    *,
    db_user: User,
    reason: str,
) -> Tuple[List[Tuple[str, Any]], List[Tuple[str, str]]]:
    actor = _format_actor(db_user)
    applied: List[Tuple[str, Any]] = []
    failed: List[Tuple[str, str]] = []

    for key, value in changes.items():
        try:
            if value is None:
                await bot_configuration_service.reset_value(
                    db, key, actor=actor, reason=reason
                )
                applied.append((key, value))
            else:
                prepared_value = value
                if isinstance(value, str):
                    try:
                        prepared_value = bot_configuration_service.parse_user_value(
                            key, value
                        )
                    except ValueError:
                        prepared_value = value
                await bot_configuration_service.set_value(
                    db, key, prepared_value, actor=actor, reason=reason
                )
                applied.append((key, prepared_value))
        except Exception as error:  # pragma: no cover - defensive
            failed.append((key, str(error)))

    if applied:
        await db.commit()
    else:
        await db.rollback()

    return applied, failed


def _get_spec_key_for_category(category_key: str) -> str:
    return CATEGORY_TO_SPEC.get(category_key, "advanced")


def _get_spec_page_for_setting(spec_key: str, setting_key: str) -> int:
    definitions = _get_spec_settings(spec_key)
    for index, definition in enumerate(definitions):
        if definition.key == setting_key:
            return (index // SETTINGS_PAGE_SIZE) + 1
    return 1


def _compute_category_health(spec_key: str) -> Tuple[str, str]:
    if spec_key == "core":
        if settings.CHANNEL_IS_REQUIRED_SUB and not settings.CHANNEL_LINK:
            return "🟡", "Добавьте ссылку на обязательный канал"
        if settings.CHANNEL_IS_REQUIRED_SUB:
            return "🟢", "Обязательная подписка активна"
        return "⚪", "Канал не обязателен"

    if spec_key == "support":
        return (
            "🟢" if settings.SUPPORT_MENU_ENABLED else "⚪",
            "Меню поддержки включено" if settings.SUPPORT_MENU_ENABLED else "Меню поддержки скрыто",
        )

    if spec_key == "payments":
        active = sum(
            [
                1 if settings.is_yookassa_enabled() else 0,
                1 if settings.is_cryptobot_enabled() else 0,
                1 if settings.is_mulenpay_enabled() else 0,
                1 if settings.is_pal24_enabled() else 0,
                1 if settings.TRIBUTE_ENABLED else 0,
                1 if settings.TELEGRAM_STARS_ENABLED else 0,
            ]
        )
        if active:
            return "🟢", f"Активных методов: {active}"
        return "🔴", "Нет настроенных платежных систем"

    if spec_key == "subscriptions":
        periods = [period.strip() for period in (settings.AVAILABLE_SUBSCRIPTION_PERIODS or "").split(",") if period.strip()]
        base_price_ok = bool(settings.BASE_SUBSCRIPTION_PRICE)
        if base_price_ok and periods:
            return "🟢", f"Доступно периодов: {len(periods)}"
        return "🟡", "Проверьте периоды и цены"

    if spec_key == "trial":
        if settings.TRIAL_DURATION_DAYS > 0:
            return "🟢", f"{settings.TRIAL_DURATION_DAYS} дн. пробного периода"
        return "⚪", "Триал отключен"

    if spec_key == "referral":
        if settings.REFERRAL_COMMISSION_PERCENT > 0:
            return "🟢", f"Комиссия {settings.REFERRAL_COMMISSION_PERCENT}%"
        return "⚪", "Комиссии не настроены"

    if spec_key == "notifications":
        if settings.ENABLE_NOTIFICATIONS:
            return "🟢", "Пользовательские уведомления активны"
        return "⚪", "Уведомления выключены"

    if spec_key == "branding":
        if settings.ENABLE_LOGO_MODE:
            return "🟢", "Брендирование включено"
        return "⚪", "Используется базовый интерфейс"

    if spec_key == "database":
        return "🟢", f"Режим БД: {settings.DATABASE_MODE}"

    if spec_key == "remnawave":
        if settings.REMNAWAVE_API_URL and settings.REMNAWAVE_API_KEY:
            return "🟢", "API подключен"
        return "🟡", "Проверьте URL и ключ"

    if spec_key == "servers":
        if settings.SERVER_STATUS_MODE != "disabled":
            return "🟢", f"Режим: {settings.SERVER_STATUS_MODE}"
        return "⚪", "Мониторинг выключен"

    if spec_key == "maintenance":
        if settings.MAINTENANCE_MODE:
            return "🟡", "Техработы активны"
        if settings.BACKUP_AUTO_ENABLED:
            return "🟢", "Бэкапы включены"
        return "⚪", "Автобэкапы отключены"

    if spec_key == "advanced":
        if settings.DEBUG or settings.WEB_API_ENABLED:
            return "🟡", "Проверьте режимы разработки"
        return "🟢", "Продакшен режим"

    return "⚪", "Без статуса"


def _render_spec_category_text(spec_key: str, page: int, language: str) -> str:
    category = SPEC_CATEGORY_MAP.get(spec_key)
    if not category:
        return ""

    definitions = _get_spec_settings(spec_key)
    total_pages = max(1, math.ceil(len(definitions) / SETTINGS_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * SETTINGS_PAGE_SIZE
    end = start + SETTINGS_PAGE_SIZE
    sliced = definitions[start:end]

    lines = [
        f"🏠 <b>Главная</b> → {category.icon} <b>{category.title}</b>",
        "",
        category.description,
        "",
        f"Страница {page}/{total_pages}",
        "",
    ]

    for definition in sliced:
        entry = bot_configuration_service.get_setting_dashboard_entry(definition.key)
        metadata = bot_configuration_service.get_metadata(definition.key)
        lines.append(f"{entry['state_icon']} {entry['icon']} <b>{entry['name']}</b>")
        lines.append(f"   {entry['value']}")
        if entry["has_override"]:
            lines.append("   ♻️ Переопределено в БД")
        if metadata.recommended is not None:
            recommended_display = bot_configuration_service.format_setting_value(
                definition.key, metadata.recommended
            )
            if recommended_display != entry["value"]:
                lines.append(f"   ✨ Рекомендуемое: {recommended_display}")

    lines.append("")
    lines.append("ℹ️ Используйте кнопки ниже для редактирования и справки.")
    return "\n".join(lines)


def _build_spec_category_keyboard(
    spec_key: str,
    page: int,
    language: str,
) -> types.InlineKeyboardMarkup:
    definitions = _get_spec_settings(spec_key)
    total_pages = max(1, math.ceil(len(definitions) / SETTINGS_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * SETTINGS_PAGE_SIZE
    end = start + SETTINGS_PAGE_SIZE
    sliced = definitions[start:end]

    rows: list[list[types.InlineKeyboardButton]] = []
    texts = get_texts(language)

    if spec_key == "remnawave":
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🔌 Проверить подключение",
                    callback_data=f"botcfg_test_remnawave:{spec_key}:{page}",
                )
            ]
        )

    if spec_key == "payments":
        def _test_button(label: str, method: str) -> types.InlineKeyboardButton:
            return types.InlineKeyboardButton(
                text=label,
                callback_data=f"botcfg_test_payment:{method}:{spec_key}:{page}",
            )

        rows.extend(
            [
                [_test_button(texts.t("PAYMENT_CARD_YOOKASSA", "💳 YooKassa · тест"), "yookassa")],
                [_test_button(texts.t("PAYMENT_CARD_TRIBUTE", "💳 Tribute · тест"), "tribute")],
                [_test_button(texts.t("PAYMENT_CARD_MULENPAY", "💳 MulenPay · тест"), "mulenpay")],
                [_test_button(texts.t("PAYMENT_CARD_PAL24", "💳 PayPalych · тест"), "pal24")],
                [_test_button(texts.t("PAYMENT_TELEGRAM_STARS", "⭐ Telegram Stars · тест"), "stars")],
                [_test_button(texts.t("PAYMENT_CRYPTOBOT", "🪙 CryptoBot · тест"), "cryptobot")],
            ]
        )

    for definition in sliced:
        entry = bot_configuration_service.get_setting_dashboard_entry(definition.key)
        callback_token = bot_configuration_service.get_callback_token(definition.key)
        name_prefix = "★ " if entry["has_override"] else ""
        button_text = f"{entry['icon']} {name_prefix}{entry['name']}"
        info_callback = f"botcfg_info:{spec_key}:{page}:1:{callback_token}"
        edit_callback = f"botcfg_setting:{spec_key}:{page}:1:{callback_token}"
        rows.append(
            [
                types.InlineKeyboardButton(text=button_text, callback_data=edit_callback),
                types.InlineKeyboardButton(text="ℹ️", callback_data=info_callback),
            ]
        )

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"botcfg_group:{spec_key}:{page - 1}",
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
                    callback_data=f"botcfg_group:{spec_key}:{page + 1}",
                )
            )
        rows.append(nav_row)

    rows.append(
        [
            types.InlineKeyboardButton(text="🔍 Поиск", callback_data="botcfg_search:start"),
            types.InlineKeyboardButton(text="⚡ Быстрые действия", callback_data="botcfg_quick_menu"),
        ]
    )

    rows.append(
        [
            types.InlineKeyboardButton(text="⬅️ К разделам", callback_data="admin_bot_config"),
            types.InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin_panel"),
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_main_keyboard() -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []
    category_buttons = [
        types.InlineKeyboardButton(
            text=f"{category.icon} {category.title}",
            callback_data=f"botcfg_group:{category.key}:1",
        )
        for category in SPEC_CATEGORIES
    ]

    for chunk in _chunk(category_buttons, 2):
        rows.append(list(chunk))

    rows.append([types.InlineKeyboardButton(text="🔍 Найти настройку", callback_data="botcfg_search:start")])
    rows.append([types.InlineKeyboardButton(text="🎛 Пресеты", callback_data="botcfg_presets")])
    rows.append(
        [
            types.InlineKeyboardButton(text="📤 Экспорт .env", callback_data="botcfg_export"),
            types.InlineKeyboardButton(text="📥 Импорт .env", callback_data="botcfg_import"),
        ]
    )
    rows.append([types.InlineKeyboardButton(text="🕑 История изменений", callback_data="botcfg_history")])
    rows.append([types.InlineKeyboardButton(text="⚡ Быстрые действия", callback_data="botcfg_quick_menu")])
    rows.append(
        [
            types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_settings"),
            types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="admin_panel"),
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_setting_keyboard(
    key: str,
    group_key: str,
    category_page: int,
    settings_page: int,
) -> types.InlineKeyboardMarkup:
    definition = bot_configuration_service.get_definition(key)
    metadata = bot_configuration_service.get_metadata(key)
    rows: list[list[types.InlineKeyboardButton]] = []
    callback_token = bot_configuration_service.get_callback_token(key)
    current_value = bot_configuration_service.get_current_value(key)

    choice_options = bot_configuration_service.get_choice_options(key)
    if choice_options:
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

    if definition.python_type is bool:
        toggle_text = "❌ Выключить" if bool(current_value) else "✅ Включить"
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=toggle_text,
                    callback_data=(
                        f"botcfg_toggle:{group_key}:{category_page}:{settings_page}:{callback_token}"
                    ),
                )
            ]
        )

    rows.append(
        [
            types.InlineKeyboardButton(
                text="✏️ Изменить",
                callback_data=(
                    f"botcfg_edit:{group_key}:{category_page}:{settings_page}:{callback_token}"
                ),
            )
        ]
    )

    if metadata.recommended is not None:
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="✨ Применить рекомендуемое",
                    callback_data=(
                        f"botcfg_recommend:{group_key}:{category_page}:{settings_page}:{callback_token}"
                    ),
                )
            ]
        )

    rows.append(
        [
            types.InlineKeyboardButton(
                text="ℹ️ Помощь",
                callback_data=(
                    f"botcfg_info:{group_key}:{category_page}:{settings_page}:{callback_token}"
                ),
            )
        ]
    )

    if bot_configuration_service.has_override(key):
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="♻️ Сбросить",
                    callback_data=(
                        f"botcfg_reset:{group_key}:{category_page}:{settings_page}:{callback_token}"
                    ),
                )
            ]
        )

    rows.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ К списку",
                callback_data=f"botcfg_group:{group_key}:{category_page}",
            ),
            types.InlineKeyboardButton(text="🏠 Панель", callback_data="admin_bot_config"),
        ]
    )

    rows.append(
        [
            types.InlineKeyboardButton(
                text="⚙️ Настройки",
                callback_data="admin_submenu_settings",
            )
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _render_setting_text(key: str, spec_category: SpecCategory) -> str:
    summary = bot_configuration_service.get_setting_summary(key)
    metadata = bot_configuration_service.get_metadata(key)

    lines = [
        f"🏠 <b>Главная</b> → {spec_category.icon} <b>{spec_category.title}</b> → ⚙️ <b>{summary['name']}</b>",
        "",
    ]

    if metadata.description:
        lines.append(f"📝 {metadata.description}")
        lines.append("")

    lines.extend(
        [
            f"🔑 <b>Ключ:</b> <code>{summary['key']}</code>",
            f"📂 <b>Категория:</b> {summary['category_label']}",
            f"📦 <b>Тип:</b> {summary['type']}",
            f"📘 <b>Текущее значение:</b> {summary['current']}",
            f"📗 <b>По умолчанию:</b> {summary['original']}",
            f"📥 <b>Переопределено:</b> {'✅ Да' if summary['has_override'] else '❌ Нет'}",
        ]
    )

    if metadata.format_hint:
        lines.append(f"📐 <b>Формат:</b> {metadata.format_hint}")
    if metadata.example:
        lines.append(f"💡 <b>Пример:</b> {metadata.example}")
    if metadata.warning:
        lines.append(f"⚠️ <b>Важно:</b> {metadata.warning}")
    if metadata.dependencies:
        lines.append(f"🔗 <b>Связано:</b> {metadata.dependencies}")
    if metadata.recommended is not None:
        recommended_display = bot_configuration_service.format_setting_value(
            key, metadata.recommended
        )
        lines.append(f"✨ <b>Рекомендуемое:</b> {recommended_display}")

    choices = bot_configuration_service.get_choice_options(key)
    if choices:
        current_raw = bot_configuration_service.get_current_value(key)
        lines.append("")
        lines.append("<b>Доступные значения:</b>")
        for option in choices:
            marker = "✅" if current_raw == option.value else "•"
            value_display = bot_configuration_service.format_setting_value(
                key, option.value
            )
            description = option.description or ""
            if description:
                lines.append(
                    f"{marker} {option.label} — <code>{value_display}</code>\n   {description}"
                )
            else:
                lines.append(f"{marker} {option.label} — <code>{value_display}</code>")

    return "\n".join(lines)


@admin_required
@error_handler
async def show_bot_config_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    acknowledge: bool = True,
):
    lines = ["⚙️ <b>Панель управления ботом</b>", ""]

    for category in SPEC_CATEGORIES:
        status_icon, summary = _compute_category_health(category.key)
        lines.append(f"{status_icon} {category.icon} <b>{category.title}</b> — {summary}")

    lines.append("")
    lines.append("Выберите раздел или воспользуйтесь поиском ниже.")

    keyboard = _build_main_keyboard()
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    if acknowledge:
        await callback.answer()


@admin_required
@error_handler
async def show_bot_config_group(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    spec_key, page = _parse_spec_payload(callback.data)
    category = SPEC_CATEGORY_MAP.get(spec_key)
    if not category:
        await callback.answer("Раздел больше недоступен", show_alert=True)
        return

    text = _render_spec_category_text(spec_key, page, db_user.language)
    keyboard = _build_spec_category_keyboard(spec_key, page, db_user.language)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def start_search_workflow(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.set_state(BotConfigStates.waiting_for_search_query)
    await state.update_data(
        botcfg_origin="bot_config_search",
        search_return_payload=callback.data,
    )

    lines = [
        "🔍 <b>Поиск по настройкам</b>",
        "",
        "Введите часть названия, ключа или описания настройки.",
        "Можно вводить несколько слов для уточнения.",
        "Напишите <code>cancel</code>, чтобы выйти из поиска.",
    ]

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🏠 Панель", callback_data="admin_bot_config"
                ),
                types.InlineKeyboardButton(
                    text="❌ Отмена", callback_data="botcfg_search:cancel"
                ),
            ]
        ]
    )

    await callback.message.answer("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)
    await callback.answer("Введите запрос для поиска", show_alert=True)


@admin_required
@error_handler
async def cancel_search(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    await callback.answer("Поиск отменен")
    await show_bot_config_menu(callback, db_user, db, acknowledge=False)


@admin_required
@error_handler
async def handle_search_query(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введите запрос для поиска настроек.")
        return

    if query.lower() in {"cancel", "отмена", "стоп"}:
        await message.answer(
            "Поиск отменен.",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="🏠 Панель", callback_data="admin_bot_config")]]
            ),
        )
        await state.clear()
        return

    keys = bot_configuration_service.search_settings(query)
    if not keys:
        await message.answer(
            "😕 Ничего не найдено. Попробуйте уточнить запрос или используйте другое ключевое слово.",
        )
        return

    lines = ["🔎 <b>Найдены настройки</b>", ""]
    keyboard_rows: List[List[types.InlineKeyboardButton]] = []

    for key in keys:
        definition = bot_configuration_service.get_definition(key)
        metadata = bot_configuration_service.get_metadata(key)
        spec_key = _get_spec_key_for_category(definition.category_key)
        spec_category = SPEC_CATEGORY_MAP.get(spec_key, SPEC_CATEGORY_MAP[DEFAULT_SPEC_KEY])
        page = _get_spec_page_for_setting(spec_key, key)
        callback_token = bot_configuration_service.get_callback_token(key)

        summary = bot_configuration_service.get_setting_summary(key)
        value_preview = summary["current"]

        lines.append(
            f"{spec_category.icon} <b>{definition.display_name}</b> — <code>{key}</code>\n"
            f"Текущее значение: {value_preview}\n"
            f"Категория: {summary['category_label']}"
        )
        if metadata.description:
            lines.append(_shorten_text(metadata.description))
        lines.append("")

        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"{spec_category.icon} {definition.display_name}",
                    callback_data=f"botcfg_setting:{spec_key}:{page}:1:{callback_token}",
                )
            ]
        )

    keyboard_rows.append([
        types.InlineKeyboardButton(text="🔁 Новый поиск", callback_data="botcfg_search:start"),
        types.InlineKeyboardButton(text="🏠 Панель", callback_data="admin_bot_config"),
    ])

    await message.answer(
        "\n".join(lines).rstrip(),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        parse_mode="HTML",
    )
    await state.update_data(botcfg_search_last_query=query)


def _shorten_text(text: str, limit: int = 120) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


@admin_required
@error_handler
async def show_quick_actions_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    lines = ["⚡ <b>Быстрые действия</b>", ""]
    buttons: List[types.InlineKeyboardButton] = []

    for key, action in QUICK_ACTIONS.items():
        lines.append(f"{action['title']}")
        lines.append(f"— {action['description']}")
        lines.append("")
        buttons.append(
            types.InlineKeyboardButton(text=action["title"], callback_data=f"botcfg_quick:{key}")
        )

    keyboard_rows = [list(chunk) for chunk in _chunk(buttons, 1)]
    keyboard_rows.append(
        [
            types.InlineKeyboardButton(text="🔁 Обновить", callback_data="botcfg_quick_menu"),
            types.InlineKeyboardButton(text="🏠 Панель", callback_data="admin_bot_config"),
        ]
    )

    await callback.message.edit_text(
        "\n".join(lines).rstrip(),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def apply_quick_action(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    action_key = callback.data.split(":", 1)[1] if ":" in callback.data else ""
    action = QUICK_ACTIONS.get(action_key)
    if not action:
        await callback.answer("Действие недоступно", show_alert=True)
        return

    applied, failed = await _apply_changeset(
        db,
        action["changes"],
        db_user=db_user,
        reason=f"quick:{action_key}",
    )

    lines = [f"{action['title']}", ""]
    if applied:
        lines.append(f"✅ Применено: {len(applied)} настроек")
    if failed:
        lines.append(f"⚠️ Ошибок: {len(failed)}")
        for key, error in failed[:5]:
            lines.append(f"• {key}: {error}")
    lines.append("")
    lines.append("Вы можете открыть панель и проверить результат.")

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🏠 Панель", callback_data="admin_bot_config")],
            [types.InlineKeyboardButton(text="⚡ К действиям", callback_data="botcfg_quick_menu")],
        ]
    )

    await callback.message.edit_text(
        "\n".join(lines).rstrip(),
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer("Готово")


@admin_required
@error_handler
async def show_presets_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    acknowledge: bool = True,
):
    custom_presets = await _load_custom_presets(db)
    lines = ["🎛 <b>Пресеты настроек</b>", ""]

    if BUILTIN_PRESETS:
        lines.append("<b>Встроенные пресеты:</b>")
        for slug, preset in BUILTIN_PRESETS.items():
            lines.append(f"• {preset['title']} — {preset['description']}")
        lines.append("")

    if custom_presets:
        lines.append("<b>Пользовательские пресеты:</b>")
        for slug, preset in custom_presets.items():
            lines.append(f"• {preset['title']} — {preset['description']}")
        lines.append("")
    else:
        lines.append("Пользовательские пресеты пока не сохранены.")
        lines.append("")

    keyboard_rows: List[List[types.InlineKeyboardButton]] = []
    for slug, preset in BUILTIN_PRESETS.items():
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=preset["title"],
                    callback_data=f"botcfg_preset_apply:{slug}",
                )
            ]
        )

    for slug, preset in custom_presets.items():
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=preset["title"],
                    callback_data=f"botcfg_preset_apply:{slug}",
                ),
                types.InlineKeyboardButton(
                    text="🗑️", callback_data=f"botcfg_preset_delete:{slug}"
                ),
            ]
        )

    keyboard_rows.append(
        [
            types.InlineKeyboardButton(
                text="💾 Сохранить текущие настройки", callback_data="botcfg_preset_save"
            )
        ]
    )
    keyboard_rows.append(
        [
            types.InlineKeyboardButton(text="🏠 Панель", callback_data="admin_bot_config"),
            types.InlineKeyboardButton(text="⚡ Быстрые", callback_data="botcfg_quick_menu"),
        ]
    )

    await callback.message.edit_text(
        "\n".join(lines).rstrip(),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        parse_mode="HTML",
    )
    if acknowledge:
        await callback.answer()


@admin_required
@error_handler
async def apply_preset(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    slug = callback.data.split(":", 1)[1] if ":" in callback.data else ""
    preset = BUILTIN_PRESETS.get(slug)
    if not preset:
        custom_presets = await _load_custom_presets(db)
        preset = custom_presets.get(slug)
    if not preset:
        await callback.answer("Пресет не найден", show_alert=True)
        return

    applied, failed = await _apply_changeset(
        db,
        preset["changes"],
        db_user=db_user,
        reason=f"preset:{slug}",
    )

    lines = [f"{preset['title']}", ""]
    if applied:
        lines.append(f"✅ Применено настроек: {len(applied)}")
    if failed:
        lines.append(f"⚠️ Ошибок: {len(failed)}")
        for key, error in failed[:5]:
            lines.append(f"• {key}: {error}")
    if not failed:
        lines.append("Все изменения успешно применены.")

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🏠 Панель", callback_data="admin_bot_config")],
            [types.InlineKeyboardButton(text="🎛 К пресетам", callback_data="botcfg_presets")],
        ]
    )

    await callback.message.edit_text(
        "\n".join(lines).rstrip(),
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer("Пресет применен")


@admin_required
@error_handler
async def start_save_preset(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.set_state(BotConfigStates.waiting_for_preset_name)
    await state.update_data(botcfg_origin="bot_config_preset")

    await callback.message.answer(
        "💾 <b>Сохранение пресета</b>\n\nВведите имя пресета. Используйте короткое и понятное название.",
        parse_mode="HTML",
    )
    await callback.answer("Введите название пресета")


@admin_required
@error_handler
async def handle_preset_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым.")
        return

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        await message.answer("Используйте латинские буквы и цифры в названии.")
        return

    if slug in BUILTIN_PRESETS:
        await message.answer("Это имя зарезервировано. Выберите другое название.")
        return

    custom_presets = await _load_custom_presets(db)
    if slug in custom_presets:
        await message.answer("Пресет с таким именем уже существует. Укажите другое имя.")
        return

    snapshot: Dict[str, Any] = {}
    for definition in _iter_all_definitions():
        snapshot[definition.key] = bot_configuration_service.get_current_value(
            definition.key
        )

    payload = {
        "title": name,
        "description": f"Сохранено администратором {db_user.username or db_user.telegram_id}",
        "changes": snapshot,
    }

    await upsert_system_setting(
        db,
        _preset_storage_key(slug),
        json.dumps(payload, ensure_ascii=False, default=str),
    )
    await db.commit()

    await message.answer(
        f"✅ Пресет <b>{name}</b> сохранен.",
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="🎛 К пресетам", callback_data="botcfg_presets")]]
        ),
    )
    await state.clear()


@admin_required
@error_handler
async def delete_preset(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    slug = callback.data.split(":", 1)[1] if ":" in callback.data else ""
    key = _preset_storage_key(slug)
    await delete_system_setting(db, key)
    await db.commit()

    await callback.answer("Пресет удален")
    await show_presets_menu(callback, db_user, db, acknowledge=False)


@admin_required
@error_handler
async def export_settings_env(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    dump = bot_configuration_service.generate_env_dump(include_secrets=False)
    file = BufferedInputFile(dump.encode("utf-8"), filename="bot-settings.env")
    caption = (
        "📤 <b>Экспорт настроек</b>\n"
        "Секретные значения скрыты. Отправьте файл разработчику или сохраните как бэкап."
    )
    await callback.message.answer_document(file, caption=caption, parse_mode="HTML")
    await callback.answer("Файл сформирован")


@admin_required
@error_handler
async def start_import_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.set_state(BotConfigStates.waiting_for_import_payload)
    await state.update_data(botcfg_origin="bot_config_import")

    lines = [
        "📥 <b>Импорт настроек</b>",
        "",
        "Отправьте содержимое .env файла текстом или прикрепите файл.",
        "Секретные значения будут применены сразу после проверки.",
        "Напишите <code>cancel</code> для отмены.",
    ]

    await callback.message.answer("\n".join(lines), parse_mode="HTML")
    await callback.answer("Пришлите .env файл")


@admin_required
@error_handler
async def handle_import_payload(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    if message.text and message.text.strip().lower() in {"cancel", "отмена"}:
        await message.answer("Импорт отменен.")
        await state.clear()
        return

    content: Optional[str] = None

    if message.document:
        buffer = io.BytesIO()
        await message.document.download(destination=buffer)
        try:
            content = buffer.getvalue().decode("utf-8")
        except UnicodeDecodeError:
            await message.answer("Не удалось прочитать файл. Убедитесь, что он в UTF-8.")
            return
    elif message.text:
        content = message.text

    if not content:
        await message.answer("Пришлите текст или файл с настройками.")
        return

    try:
        parsed = bot_configuration_service.parse_env_dump(content)
    except ValueError as error:
        await message.answer(f"⚠️ Ошибка: {error}")
        return

    if not parsed:
        await message.answer("Файл не содержит подходящих настроек.")
        return

    applied, failed = await _apply_changeset(
        db,
        parsed,
        db_user=db_user,
        reason="import",
    )

    lines = ["📥 <b>Импорт завершен</b>", ""]
    if applied:
        lines.append(f"✅ Обновлено настроек: {len(applied)}")
    if failed:
        lines.append(f"⚠️ Ошибок: {len(failed)}")
        for key, error in failed[:5]:
            lines.append(f"• {key}: {error}")
    lines.append("Перейдите в панель, чтобы проверить изменения.")

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="🏠 Панель", callback_data="admin_bot_config")]]
        ),
    )
    await state.clear()


@admin_required
@error_handler
async def show_history(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    history = bot_configuration_service.get_history()
    lines = ["🕑 <b>История изменений</b>", ""]

    if not history:
        lines.append("История пока пуста. Измените любую настройку, чтобы увидеть журнал.")
    else:
        for entry in history:
            timestamp: datetime = entry["timestamp"]
            formatted = timestamp.strftime("%d.%m.%Y %H:%M:%S")
            actor = entry.get("actor") or "system"
            reason = entry.get("reason") or "manual"
            lines.append(
                f"• <b>{entry['name']}</b> (<code>{entry['key']}</code>)\n"
                f"  {formatted} — {actor}\n"
                f"  {entry['old']} → {entry['new']} ({reason})"
            )
            lines.append("")

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="🏠 Панель", callback_data="admin_bot_config")]]
    )

    await callback.message.edit_text(
        "\n".join(lines).rstrip(),
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
    parts = callback.data.split(":", 3)
    spec_key = parts[1] if len(parts) > 1 else "remnawave"
    try:
        page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        page = 1

    if spec_key not in SPEC_CATEGORY_MAP:
        await callback.answer("Раздел недоступен", show_alert=True)
        return

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

    try:
        keyboard = _build_spec_category_keyboard(spec_key, page, db_user.language)
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass

    await callback.answer(message, show_alert=True)


@admin_required
@error_handler
async def test_payment_provider(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split(":", 4)
    method = parts[1] if len(parts) > 1 else ""
    spec_key = parts[2] if len(parts) > 2 else "payments"

    try:
        page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        page = 1

    if spec_key not in SPEC_CATEGORY_MAP:
        await callback.answer("Раздел недоступен", show_alert=True)
        return

    language = db_user.language
    texts = get_texts(language)
    payment_service = PaymentService(callback.bot)

    message_text: str

    async def _refresh_markup() -> None:
        keyboard = _build_spec_category_keyboard(spec_key, page, language)
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
    spec_key = parts[1] if len(parts) > 1 else DEFAULT_SPEC_KEY
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
    category = SPEC_CATEGORY_MAP.get(spec_key, SPEC_CATEGORY_MAP[DEFAULT_SPEC_KEY])
    text = _render_setting_text(key, category)
    keyboard = _build_setting_keyboard(key, spec_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await _store_setting_context(
        state,
        key=key,
        group_key=spec_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer()


@admin_required
@error_handler
async def show_setting_info(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 4)
    spec_key = parts[1] if len(parts) > 1 else DEFAULT_SPEC_KEY
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
        await callback.answer("Настройка больше недоступна", show_alert=True)
        return

    definition = bot_configuration_service.get_definition(key)
    metadata = bot_configuration_service.get_metadata(key)
    summary = bot_configuration_service.get_setting_summary(key)
    spec_category = SPEC_CATEGORY_MAP.get(spec_key, SPEC_CATEGORY_MAP[DEFAULT_SPEC_KEY])

    lines = [
        f"ℹ️ <b>Подробности: {definition.display_name}</b>",
        f"{spec_category.icon} Раздел: {spec_category.title}",
        f"🔑 Ключ: <code>{key}</code>",
        f"📦 Тип: {summary['type']}",
        f"📘 Текущее: {summary['current']}",
        f"📗 По умолчанию: {summary['original']}",
    ]

    if metadata.description:
        lines.append("")
        lines.append(f"📝 {metadata.description}")
    if metadata.format_hint:
        lines.append(f"📐 Формат: {metadata.format_hint}")
    if metadata.example:
        lines.append(f"💡 Пример: {metadata.example}")
    if metadata.warning:
        lines.append(f"⚠️ Важно: {metadata.warning}")
    if metadata.dependencies:
        lines.append(f"🔗 Связано: {metadata.dependencies}")
    if metadata.recommended is not None:
        recommended = bot_configuration_service.format_setting_value(key, metadata.recommended)
        lines.append(f"✨ Рекомендуемое: {recommended}")

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="⚙️ Открыть настройку",
                    callback_data=f"botcfg_setting:{spec_key}:{category_page}:{settings_page}:{token}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="⬅️ К списку",
                    callback_data=f"botcfg_group:{spec_key}:{category_page}",
                ),
                types.InlineKeyboardButton(
                    text="🏠 Панель",
                    callback_data="admin_bot_config",
                ),
            ],
        ]
    )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=keyboard,
        parse_mode="HTML",
    )
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
    spec_key = parts[1] if len(parts) > 1 else DEFAULT_SPEC_KEY
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
    metadata = bot_configuration_service.get_metadata(key)
    spec_category = SPEC_CATEGORY_MAP.get(spec_key, SPEC_CATEGORY_MAP[DEFAULT_SPEC_KEY])

    instructions = [
        f"✏️ <b>Редактирование:</b> {summary['name']}",
        f"Раздел: {spec_category.icon} {spec_category.title}",
        f"Ключ: <code>{summary['key']}</code>",
        f"Тип: {summary['type']}",
        f"Текущее значение: {summary['current']}",
        "",
        "Отправьте новое значение сообщением.",
    ]

    if definition.is_optional:
        instructions.append("Отправьте 'none' или оставьте пустым для сброса на значение по умолчанию.")

    if metadata.format_hint:
        instructions.append(f"Формат: {metadata.format_hint}")
    if metadata.example:
        instructions.append(f"Пример: {metadata.example}")
    if metadata.warning:
        instructions.append(f"Важно: {metadata.warning}")

    instructions.append("Для отмены отправьте 'cancel'.")

    await callback.message.edit_text(
        "\n".join(instructions),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.BACK,
                        callback_data=(
                            f"botcfg_setting:{spec_key}:{category_page}:{settings_page}:{token}"
                        ),
                    )
                ]
            ]
        ),
        parse_mode="HTML",
    )

    await _store_setting_context(
        state,
        key=key,
        group_key=spec_key,
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
    group_key = data.get("setting_group_key", DEFAULT_SPEC_KEY)
    spec_category = SPEC_CATEGORY_MAP.get(group_key, SPEC_CATEGORY_MAP[DEFAULT_SPEC_KEY])
    category_page = data.get("setting_category_page", 1)
    settings_page = data.get("setting_settings_page", 1)

    if not key:
        await message.answer("Не удалось определить редактируемую настройку. Попробуйте снова.")
        await state.clear()
        return

    try:
        value = bot_configuration_service.parse_user_value(key, message.text or "")
    except ValueError as error:
        await message.answer(f"⚠️ {error}")
        return

    await bot_configuration_service.set_value(db, key, value)
    await db.commit()

    text = _render_setting_text(key, spec_category)
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
async def handle_direct_setting_input(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()

    key = data.get("setting_key")
    group_key = data.get("setting_group_key", DEFAULT_SPEC_KEY)
    spec_category = SPEC_CATEGORY_MAP.get(group_key, SPEC_CATEGORY_MAP[DEFAULT_SPEC_KEY])
    category_page = int(data.get("setting_category_page", 1) or 1)
    settings_page = int(data.get("setting_settings_page", 1) or 1)

    if not key:
        return

    try:
        value = bot_configuration_service.parse_user_value(key, message.text or "")
    except ValueError as error:
        await message.answer(f"⚠️ {error}")
        return

    await bot_configuration_service.set_value(db, key, value)
    await db.commit()

    text = _render_setting_text(key, spec_category)
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
async def reset_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 4)
    group_key = parts[1] if len(parts) > 1 else DEFAULT_SPEC_KEY
    spec_category = SPEC_CATEGORY_MAP.get(group_key, SPEC_CATEGORY_MAP[DEFAULT_SPEC_KEY])
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
    await bot_configuration_service.reset_value(db, key)
    await db.commit()

    text = _render_setting_text(key, spec_category)
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
    group_key = parts[1] if len(parts) > 1 else DEFAULT_SPEC_KEY
    spec_category = SPEC_CATEGORY_MAP.get(group_key, SPEC_CATEGORY_MAP[DEFAULT_SPEC_KEY])
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

    text = _render_setting_text(key, spec_category)
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
    group_key = parts[1] if len(parts) > 1 else DEFAULT_SPEC_KEY
    spec_category = SPEC_CATEGORY_MAP.get(group_key, SPEC_CATEGORY_MAP[DEFAULT_SPEC_KEY])
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

    await bot_configuration_service.set_value(db, key, value)
    await db.commit()

    text = _render_setting_text(key, spec_category)
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
        start_search_workflow,
        F.data == "botcfg_search:start",
    )
    dp.callback_query.register(
        cancel_search,
        F.data == "botcfg_search:cancel",
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
        show_setting_info,
        F.data.startswith("botcfg_info:"),
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
        show_quick_actions_menu,
        F.data == "botcfg_quick_menu",
    )
    dp.callback_query.register(
        apply_quick_action,
        F.data.startswith("botcfg_quick:"),
    )
    dp.callback_query.register(
        show_presets_menu,
        F.data == "botcfg_presets",
    )
    dp.callback_query.register(
        apply_preset,
        F.data.startswith("botcfg_preset_apply:"),
    )
    dp.callback_query.register(
        start_save_preset,
        F.data == "botcfg_preset_save",
    )
    dp.callback_query.register(
        delete_preset,
        F.data.startswith("botcfg_preset_delete:"),
    )
    dp.callback_query.register(
        export_settings_env,
        F.data == "botcfg_export",
    )
    dp.callback_query.register(
        start_import_settings,
        F.data == "botcfg_import",
    )
    dp.callback_query.register(
        show_history,
        F.data == "botcfg_history",
    )
    dp.message.register(
        handle_search_query,
        BotConfigStates.waiting_for_search_query,
    )
    dp.message.register(
        handle_import_payload,
        BotConfigStates.waiting_for_import_payload,
    )
    dp.message.register(
        handle_preset_name,
        BotConfigStates.waiting_for_preset_name,
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

