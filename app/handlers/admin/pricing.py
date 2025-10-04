import html
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.localization.texts import get_texts
from app.services.system_settings_service import bot_configuration_service
from app.states import PricingStates
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


PriceItem = Tuple[str, str, int]


@dataclass(frozen=True)
class SectionItem:
    key: str
    label: str
    value: Any
    display: str
    short_display: str


@dataclass(frozen=True)
class SectionSettingDefinition:
    key: str
    label_key: str
    label_default: str
    type: str
    summary_label_key: Optional[str] = None
    summary_label_default: Optional[str] = None
    prompt_key: Optional[str] = None
    prompt_default: Optional[str] = None
    include_in_summary: bool = True


@dataclass(frozen=True)
class CustomSectionConfig:
    title_key: str
    title_default: str
    button_key: str
    button_default: str
    summary_key: str
    summary_default: str
    items: Tuple[SectionSettingDefinition, ...]


PRICE_KEY_PREFIXES: Tuple[str, ...] = ("PRICE_",)
PRICE_KEY_EXTRAS: Tuple[str, ...] = ("BASE_SUBSCRIPTION_PRICE", "PRICE_PER_DEVICE")
ALLOWED_PERIOD_VALUES: Tuple[int, ...] = (14, 30, 60, 90, 180, 360)


CUSTOM_SECTIONS: Dict[str, CustomSectionConfig] = {
    "trial": CustomSectionConfig(
        title_key="ADMIN_PRICING_SECTION_TRIAL_TITLE",
        title_default="🎁 Пробный период",
        button_key="ADMIN_PRICING_BUTTON_TRIAL",
        button_default="🎁 Пробный период",
        summary_key="ADMIN_PRICING_MENU_SUMMARY_TRIAL",
        summary_default="• Пробный период: {summary}",
        items=(
            SectionSettingDefinition(
                key="TRIAL_DURATION_DAYS",
                label_key="ADMIN_PRICING_TRIAL_DURATION",
                label_default="Длительность пробного периода (дней)",
                type="int",
                summary_label_key="ADMIN_PRICING_TRIAL_SUMMARY_DURATION",
                summary_label_default="Дни",
            ),
            SectionSettingDefinition(
                key="TRIAL_TRAFFIC_LIMIT_GB",
                label_key="ADMIN_PRICING_TRIAL_TRAFFIC",
                label_default="Лимит трафика триала (ГБ)",
                type="int",
                summary_label_key="ADMIN_PRICING_TRIAL_SUMMARY_TRAFFIC",
                summary_label_default="Трафик",
            ),
            SectionSettingDefinition(
                key="TRIAL_DEVICE_LIMIT",
                label_key="ADMIN_PRICING_TRIAL_DEVICES",
                label_default="Количество устройств в триале",
                type="int",
                summary_label_key="ADMIN_PRICING_TRIAL_SUMMARY_DEVICES",
                summary_label_default="Устройства",
            ),
            SectionSettingDefinition(
                key="TRIAL_ADD_REMAINING_DAYS_TO_PAID",
                label_key="ADMIN_PRICING_TRIAL_ADD_REMAINING",
                label_default="Добавлять остаток триала к платной подписке",
                type="bool",
                summary_label_key="ADMIN_PRICING_TRIAL_SUMMARY_ADD_REMAINING",
                summary_label_default="Перенос дней",
            ),
            SectionSettingDefinition(
                key="TRIAL_SQUAD_UUID",
                label_key="ADMIN_PRICING_TRIAL_SQUAD",
                label_default="UUID сквада для пробного периода",
                type="text",
                include_in_summary=False,
            ),
        ),
    ),
    "subscription": CustomSectionConfig(
        title_key="ADMIN_PRICING_SECTION_SUBSCRIPTION_TITLE",
        title_default="⚙️ Параметры подписки",
        button_key="ADMIN_PRICING_BUTTON_SUBSCRIPTION",
        button_default="⚙️ Параметры подписки",
        summary_key="ADMIN_PRICING_MENU_SUMMARY_SUBSCRIPTION",
        summary_default="• Параметры подписки: {summary}",
        items=(
            SectionSettingDefinition(
                key="BASE_SUBSCRIPTION_PRICE",
                label_key="ADMIN_PRICING_SUBSCRIPTION_BASE_PRICE",
                label_default="Базовая стоимость подписки",
                type="price",
                summary_label_key="ADMIN_PRICING_SUBSCRIPTION_SUMMARY_PRICE",
                summary_label_default="База",
            ),
            SectionSettingDefinition(
                key="DEFAULT_DEVICE_LIMIT",
                label_key="ADMIN_PRICING_SUBSCRIPTION_DEFAULT_DEVICES",
                label_default="Устройств по умолчанию",
                type="int",
                summary_label_key="ADMIN_PRICING_SUBSCRIPTION_SUMMARY_DEFAULT_DEVICES",
                summary_label_default="Устройств",
            ),
            SectionSettingDefinition(
                key="MAX_DEVICES_LIMIT",
                label_key="ADMIN_PRICING_SUBSCRIPTION_MAX_DEVICES",
                label_default="Максимум устройств",
                type="int",
                summary_label_key="ADMIN_PRICING_SUBSCRIPTION_SUMMARY_MAX_DEVICES",
                summary_label_default="Макс устройств",
            ),
        ),
    ),
    "availability": CustomSectionConfig(
        title_key="ADMIN_PRICING_SECTION_AVAILABILITY_TITLE",
        title_default="📆 Выводимые периоды",
        button_key="ADMIN_PRICING_BUTTON_AVAILABILITY",
        button_default="📆 Выводимые периоды",
        summary_key="ADMIN_PRICING_MENU_SUMMARY_AVAILABILITY",
        summary_default="• Выводимые периоды: {summary}",
        items=(
            SectionSettingDefinition(
                key="AVAILABLE_SUBSCRIPTION_PERIODS",
                label_key="ADMIN_PRICING_AVAILABILITY_SUBSCRIPTIONS",
                label_default="Периоды подписки",
                type="periods",
                summary_label_key="ADMIN_PRICING_AVAILABILITY_SUMMARY_SUBSCRIPTIONS",
                summary_label_default="Подписка",
            ),
            SectionSettingDefinition(
                key="AVAILABLE_RENEWAL_PERIODS",
                label_key="ADMIN_PRICING_AVAILABILITY_RENEWALS",
                label_default="Периоды продления",
                type="periods",
                summary_label_key="ADMIN_PRICING_AVAILABILITY_SUMMARY_RENEWALS",
                summary_label_default="Продление",
            ),
        ),
    ),
    "traffic_settings": CustomSectionConfig(
        title_key="ADMIN_PRICING_SECTION_TRAFFIC_SETTINGS_TITLE",
        title_default="📊 Лимиты трафика",
        button_key="ADMIN_PRICING_BUTTON_TRAFFIC_SETTINGS",
        button_default="📊 Лимиты трафика",
        summary_key="ADMIN_PRICING_MENU_SUMMARY_TRAFFIC_SETTINGS",
        summary_default="• Лимиты трафика: {summary}",
        items=(
            SectionSettingDefinition(
                key="DEFAULT_TRAFFIC_LIMIT_GB",
                label_key="ADMIN_PRICING_TRAFFIC_DEFAULT_LIMIT",
                label_default="Лимит трафика по умолчанию (ГБ)",
                type="int",
                summary_label_key="ADMIN_PRICING_TRAFFIC_SUMMARY_DEFAULT",
                summary_label_default="По умолчанию",
            ),
            SectionSettingDefinition(
                key="TRAFFIC_SELECTION_MODE",
                label_key="ADMIN_PRICING_TRAFFIC_SELECTION_MODE",
                label_default="Режим выбора трафика",
                type="choice",
                summary_label_key="ADMIN_PRICING_TRAFFIC_SUMMARY_MODE",
                summary_label_default="Режим",
            ),
            SectionSettingDefinition(
                key="FIXED_TRAFFIC_LIMIT_GB",
                label_key="ADMIN_PRICING_TRAFFIC_FIXED_LIMIT",
                label_default="Фиксированный лимит (ГБ)",
                type="int",
                summary_label_key="ADMIN_PRICING_TRAFFIC_SUMMARY_FIXED",
                summary_label_default="Фикс",
            ),
            SectionSettingDefinition(
                key="DEFAULT_TRAFFIC_RESET_STRATEGY",
                label_key="ADMIN_PRICING_TRAFFIC_RESET_STRATEGY",
                label_default="Стратегия сброса трафика",
                type="choice",
                summary_label_key="ADMIN_PRICING_TRAFFIC_SUMMARY_RESET",
                summary_label_default="Сброс",
            ),
            SectionSettingDefinition(
                key="RESET_TRAFFIC_ON_PAYMENT",
                label_key="ADMIN_PRICING_TRAFFIC_RESET_ON_PAYMENT",
                label_default="Сбрасывать трафик при оплате",
                type="bool",
                summary_label_key="ADMIN_PRICING_TRAFFIC_SUMMARY_RESET_PAYMENT",
                summary_label_default="Сброс при оплате",
            ),
        ),
    ),
}

CUSTOM_SECTION_ORDER: Tuple[str, ...] = tuple(CUSTOM_SECTIONS.keys())

CUSTOM_DEFINITION_BY_KEY: Dict[str, SectionSettingDefinition] = {}
for _section_key in CUSTOM_SECTION_ORDER:
    _section_config = CUSTOM_SECTIONS[_section_key]
    for _definition in _section_config.items:
        CUSTOM_DEFINITION_BY_KEY[_definition.key] = _definition


def _language_code(language: str | None) -> str:
    return (language or "ru").split("-")[0].lower()


def _is_price_key(key: str) -> bool:
    return key in PRICE_KEY_EXTRAS or any(key.startswith(prefix) for prefix in PRICE_KEY_PREFIXES)


def _get_item_type(key: str) -> str:
    definition = CUSTOM_DEFINITION_BY_KEY.get(key)
    if definition:
        return definition.type
    if _is_price_key(key):
        return "price"
    return "text"


def _format_setting_value(key: str, language: str, *, short: bool = False) -> str:
    lang_code = _language_code(language)
    value = getattr(settings, key, None)

    if value is None:
        return "—"

    if isinstance(value, str):
        if not value.strip():
            return "—"
        if not short:
            return value
        return value if len(value) <= 20 else f"{value[:17]}…"

    if _is_price_key(key) and isinstance(value, (int, float)):
        return settings.format_price(int(value))

    if isinstance(value, bool):
        if short:
            if lang_code == "ru":
                return "✅ Вкл" if value else "❌ Выкл"
            return "✅ On" if value else "❌ Off"
        if lang_code == "ru":
            return "Включено" if value else "Выключено"
        return "Enabled" if value else "Disabled"

    if short:
        formatted = bot_configuration_service.format_value_for_list(key)
        if formatted:
            return formatted

    formatted_full = bot_configuration_service.format_value_human(key, value)
    return formatted_full if formatted_full else str(value)


def _build_custom_section_items(section: str, language: str) -> List[SectionItem]:
    texts = get_texts(language)
    items: List[SectionItem] = []

    config = CUSTOM_SECTIONS[section]
    for definition in config.items:
        label = texts.t(definition.label_key, definition.label_default)
        value = getattr(settings, definition.key, None)
        display = _format_setting_value(definition.key, language)
        short_display = _format_setting_value(definition.key, language, short=True)
        items.append(
            SectionItem(
                key=definition.key,
                label=label,
                value=value,
                display=display,
                short_display=short_display,
            )
        )

    return items


def _build_custom_summary(
    section: str,
    items: Iterable[SectionItem],
    language: str,
    fallback: str,
) -> str:
    texts = get_texts(language)
    config = CUSTOM_SECTIONS[section]
    definitions: Dict[str, SectionSettingDefinition] = {
        definition.key: definition for definition in config.items
    }

    parts: List[str] = []
    for item in items:
        definition = definitions.get(item.key)
        if not definition or not definition.include_in_summary:
            continue

        label_default = definition.summary_label_default or definition.label_default
        if definition.summary_label_key:
            label = texts.t(definition.summary_label_key, label_default)
        else:
            label = label_default

        short_value = item.short_display or "—"
        parts.append(f"{label}: {short_value}")

    return ", ".join(parts) if parts else fallback


def _build_instruction(
    definition: Optional[SectionSettingDefinition],
    item_type: str,
    key: str,
    language: str,
) -> str:
    texts = get_texts(language)

    if definition and definition.prompt_key:
        return texts.t(
            definition.prompt_key,
            definition.prompt_default
            or texts.t(
                "ADMIN_PRICING_SETTING_PROMPT_GENERIC",
                "Введите новое значение. Для отмены отправьте «отмена».",
            ),
        )

    if item_type == "int":
        return texts.t(
            "ADMIN_PRICING_SETTING_PROMPT_INT",
            "Введите целое число. Для отмены отправьте «отмена».",
        )

    if item_type == "text":
        return texts.t(
            "ADMIN_PRICING_SETTING_PROMPT_TEXT",
            "Введите новое значение. Чтобы очистить параметр, отправьте «пусто». Для отмены — «отмена».",
        )

    if item_type == "choice":
        options = bot_configuration_service.get_choice_options(key)
        if options:
            readable = ", ".join(
                f"{option.label} ({option.value})" for option in options
            )
            return texts.t(
                "ADMIN_PRICING_SETTING_PROMPT_CHOICE",
                "Введите одно из значений: {options}. Для отмены — «отмена».",
            ).format(options=readable)
        return texts.t(
            "ADMIN_PRICING_SETTING_PROMPT_GENERIC",
            "Введите новое значение. Для отмены отправьте «отмена».",
        )

    if item_type == "periods":
        allowed = ", ".join(str(value) for value in ALLOWED_PERIOD_VALUES)
        return texts.t(
            "ADMIN_PRICING_SETTING_PROMPT_PERIODS",
            "Введите значения через запятую из допустимого набора: {values}. Для отмены — «отмена».",
        ).format(values=allowed)

    return texts.t(
        "ADMIN_PRICING_SETTING_PROMPT_GENERIC",
        "Введите новое значение. Для отмены отправьте «отмена».",
    )


def _parse_periods_input(raw_value: str, language: str) -> str:
    texts = get_texts(language)
    cleaned = (raw_value or "").replace(" ", "").replace("\n", "").strip()

    if not cleaned:
        raise ValueError(
            texts.t(
                "ADMIN_PRICING_SETTING_PERIODS_EMPTY",
                "Список периодов не может быть пустым.",
            )
        )

    parts = [part for part in cleaned.split(",") if part]
    parsed: List[int] = []

    for part in parts:
        try:
            value = int(part)
        except ValueError as error:
            raise ValueError(
                texts.t(
                    "ADMIN_PRICING_SETTING_PERIODS_INVALID_NUMBER",
                    "Не удалось распознать значение «{value}». Укажите числа через запятую.",
                ).format(value=part)
            ) from error

        if value not in ALLOWED_PERIOD_VALUES:
            allowed = ", ".join(str(item) for item in ALLOWED_PERIOD_VALUES)
            raise ValueError(
                texts.t(
                    "ADMIN_PRICING_SETTING_PERIODS_INVALID",
                    "Недопустимое значение {value}. Доступны только: {allowed}.",
                ).format(value=value, allowed=allowed)
            )

        parsed.append(value)

    if not parsed:
        raise ValueError(
            texts.t(
                "ADMIN_PRICING_SETTING_PERIODS_EMPTY",
                "Список периодов не может быть пустым.",
            )
        )

    unique_sorted = sorted(set(parsed))
    return ",".join(str(value) for value in unique_sorted)
def _format_period_label(days: int, lang_code: str, short: bool = False) -> str:
    if short:
        suffix = "д" if lang_code == "ru" else "d"
        return f"{days}{suffix}"
    if lang_code == "ru":
        return f"{days} дней"
    if days == 1:
        return "1 day"
    return f"{days}-day plan"


def _format_traffic_label(gb: int, lang_code: str, short: bool = False) -> str:
    if gb == 0:
        return "∞" if short else ("Безлимит" if lang_code == "ru" else "Unlimited")
    unit = "ГБ" if lang_code == "ru" else "GB"
    if short:
        return f"{gb}{unit}" if lang_code == "ru" else f"{gb}{unit}"
    return f"{gb} {unit}"


def _get_period_items(lang_code: str) -> List[PriceItem]:
    items: List[PriceItem] = []
    for days in settings.get_available_subscription_periods():
        key = f"PRICE_{days}_DAYS"
        if hasattr(settings, key):
            price = getattr(settings, key)
            items.append((key, _format_period_label(days, lang_code), price))
    return items


def _get_traffic_items(lang_code: str) -> List[PriceItem]:
    traffic_keys: Tuple[Tuple[int, str], ...] = (
        (5, "PRICE_TRAFFIC_5GB"),
        (10, "PRICE_TRAFFIC_10GB"),
        (25, "PRICE_TRAFFIC_25GB"),
        (50, "PRICE_TRAFFIC_50GB"),
        (100, "PRICE_TRAFFIC_100GB"),
        (250, "PRICE_TRAFFIC_250GB"),
        (500, "PRICE_TRAFFIC_500GB"),
        (1000, "PRICE_TRAFFIC_1000GB"),
        (0, "PRICE_TRAFFIC_UNLIMITED"),
    )

    items: List[PriceItem] = []
    for gb, key in traffic_keys:
        if hasattr(settings, key):
            price = getattr(settings, key)
            items.append((key, _format_traffic_label(gb, lang_code), price))
    return items


def _get_extra_items(lang_code: str) -> List[PriceItem]:
    items: List[PriceItem] = []

    if hasattr(settings, "PRICE_PER_DEVICE"):
        label = "Дополнительное устройство" if lang_code == "ru" else "Extra device"
        items.append(("PRICE_PER_DEVICE", label, settings.PRICE_PER_DEVICE))

    return items


def _build_period_summary(items: Iterable[PriceItem], lang_code: str, fallback: str) -> str:
    parts: List[str] = []
    for key, label, price in items:
        try:
            days = int(key.replace("PRICE_", "").replace("_DAYS", ""))
        except ValueError:
            days = None

        if days is not None:
            suffix = "д" if lang_code == "ru" else "d"
            short_label = f"{days}{suffix}"
        else:
            short_label = label

        parts.append(f"{short_label}: {settings.format_price(price)}")

    return ", ".join(parts) if parts else fallback


def _build_traffic_summary(items: Iterable[PriceItem], lang_code: str, fallback: str) -> str:
    parts: List[str] = []
    for key, label, price in items:
        if key.endswith("UNLIMITED"):
            short_label = "∞"
        else:
            digits = ''.join(ch for ch in key if ch.isdigit())
            unit = "ГБ" if lang_code == "ru" else "GB"
            short_label = f"{digits}{unit}" if digits else label

        parts.append(f"{short_label}: {settings.format_price(price)}")

    return ", ".join(parts) if parts else fallback


def _build_extra_summary(items: Iterable[PriceItem], fallback: str) -> str:
    parts = [f"{label}: {settings.format_price(price)}" for key, label, price in items]
    return ", ".join(parts) if parts else fallback


def _build_overview(language: str) -> Tuple[str, types.InlineKeyboardMarkup]:
    texts = get_texts(language)
    lang_code = _language_code(language)

    period_items = _get_period_items(lang_code)
    traffic_items = _get_traffic_items(lang_code)
    extra_items = _get_extra_items(lang_code)

    fallback = texts.t("ADMIN_PRICING_SUMMARY_EMPTY", "—")
    summary_periods = _build_period_summary(period_items, lang_code, fallback)
    summary_traffic = _build_traffic_summary(traffic_items, lang_code, fallback)
    summary_extra = _build_extra_summary(extra_items, fallback)

    custom_items: Dict[str, List[SectionItem]] = {}
    for section_key in CUSTOM_SECTION_ORDER:
        custom_items[section_key] = _build_custom_section_items(section_key, language)

    summary_lines: List[str] = [
        texts.t("ADMIN_PRICING_MENU_SUMMARY_PERIODS", "• Периоды: {summary}").format(
            summary=summary_periods
        ),
        texts.t("ADMIN_PRICING_MENU_SUMMARY_TRAFFIC", "• Трафик: {summary}").format(
            summary=summary_traffic
        ),
        texts.t("ADMIN_PRICING_MENU_SUMMARY_EXTRA", "• Дополнительно: {summary}").format(
            summary=summary_extra
        ),
    ]

    for section_key in CUSTOM_SECTION_ORDER:
        config = CUSTOM_SECTIONS[section_key]
        section_summary = _build_custom_summary(
            section_key, custom_items[section_key], language, fallback
        )
        summary_lines.append(
            texts.t(config.summary_key, config.summary_default).format(summary=section_summary)
        )

    text = (
        f"💰 <b>{texts.t('ADMIN_PRICING_MENU_TITLE', 'Управление ценами')}</b>\n\n"
        f"{texts.t('ADMIN_PRICING_MENU_DESCRIPTION', 'Быстрый доступ к тарифам и пакетам.')}\n\n"
        f"<b>{texts.t('ADMIN_PRICING_MENU_SUMMARY', 'Краткая сводка:')}</b>\n"
        + "\n".join(summary_lines)
        + "\n\n"
        f"{texts.t('ADMIN_PRICING_MENU_PROMPT', 'Выберите раздел для редактирования:')}"
    )

    keyboard_rows: List[List[types.InlineKeyboardButton]] = [
        [
            types.InlineKeyboardButton(
                text=texts.t("ADMIN_PRICING_BUTTON_PERIODS", "🗓 Периоды подписки"),
                callback_data="admin_pricing_section:periods",
            )
        ],
        [
            types.InlineKeyboardButton(
                text=texts.t("ADMIN_PRICING_BUTTON_TRAFFIC", "📦 Пакеты трафика"),
                callback_data="admin_pricing_section:traffic",
            )
        ],
        [
            types.InlineKeyboardButton(
                text=texts.t("ADMIN_PRICING_BUTTON_EXTRA", "➕ Дополнительно"),
                callback_data="admin_pricing_section:extra",
            )
        ],
    ]

    for section_key in CUSTOM_SECTION_ORDER:
        config = CUSTOM_SECTIONS[section_key]
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=texts.t(config.button_key, config.button_default),
                    callback_data=f"admin_pricing_section:{section_key}",
                )
            ]
        )

    keyboard_rows.append([types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_panel")])

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    return text, keyboard


def _build_section(
    section: str,
    language: str,
) -> Tuple[str, types.InlineKeyboardMarkup]:
    texts = get_texts(language)
    lang_code = _language_code(language)

    if section == "periods":
        price_items = _get_period_items(lang_code)
        section_items = [
            SectionItem(
                key=key,
                label=label,
                value=price,
                display=settings.format_price(price),
                short_display=settings.format_price(price),
            )
            for key, label, price in price_items
        ]
        title = texts.t("ADMIN_PRICING_SECTION_PERIODS_TITLE", "🗓 Периоды подписки")
    elif section == "traffic":
        price_items = _get_traffic_items(lang_code)
        section_items = [
            SectionItem(
                key=key,
                label=label,
                value=price,
                display=settings.format_price(price),
                short_display=settings.format_price(price),
            )
            for key, label, price in price_items
        ]
        title = texts.t("ADMIN_PRICING_SECTION_TRAFFIC_TITLE", "📦 Пакеты трафика")
    elif section == "extra":
        price_items = _get_extra_items(lang_code)
        section_items = [
            SectionItem(
                key=key,
                label=label,
                value=price,
                display=settings.format_price(price),
                short_display=settings.format_price(price),
            )
            for key, label, price in price_items
        ]
        title = texts.t("ADMIN_PRICING_SECTION_EXTRA_TITLE", "➕ Дополнительные опции")
    elif section in CUSTOM_SECTIONS:
        section_items = _build_custom_section_items(section, language)
        config = CUSTOM_SECTIONS[section]
        title = texts.t(config.title_key, config.title_default)
    else:
        section_items = []
        title = texts.t("ADMIN_PRICING_SECTION_EXTRA_TITLE", "➕ Дополнительные опции")

    lines = [title, ""]

    if section_items:
        for item in section_items:
            lines.append(f"• {item.label} — {item.display}")
        lines.append("")
        lines.append(texts.t("ADMIN_PRICING_SECTION_PROMPT", "Выберите что изменить:"))
    else:
        lines.append(texts.t("ADMIN_PRICING_SECTION_EMPTY", "Нет доступных значений."))

    keyboard_rows: List[List[types.InlineKeyboardButton]] = []
    for item in section_items:
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"{item.label} • {item.short_display}",
                    callback_data=f"admin_pricing_edit:{section}:{item.key}",
                )
            ]
        )

    keyboard_rows.append(
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_pricing")]
    )

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    return "\n".join(lines), keyboard


async def _render_message(
    message: types.Message,
    text: str,
    keyboard: types.InlineKeyboardMarkup,
) -> None:
    try:
        await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as error:  # message changed elsewhere
        logger.debug("Failed to edit pricing message: %s", error)
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def _render_message_by_id(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    keyboard: types.InlineKeyboardMarkup,
) -> None:
    try:
        await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except TelegramBadRequest as error:
        logger.debug("Failed to edit pricing message by id: %s", error)
        await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")


def _parse_price_input(text: str) -> int:
    normalized = text.replace("₽", "").replace("р", "").replace("RUB", "")
    normalized = normalized.replace(" ", "").replace(",", ".").strip()
    if not normalized:
        raise ValueError("empty")

    try:
        value = Decimal(normalized)
    except InvalidOperation as error:
        raise ValueError("invalid") from error

    if value < 0:
        raise ValueError("negative")

    kopeks = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return kopeks


def _resolve_label(section: str, key: str, language: str) -> str:
    custom_definition = CUSTOM_DEFINITION_BY_KEY.get(key)
    if custom_definition:
        texts = get_texts(language)
        return texts.t(custom_definition.label_key, custom_definition.label_default)

    lang_code = _language_code(language)

    if section == "periods" and key.startswith("PRICE_") and key.endswith("_DAYS"):
        try:
            days = int(key.replace("PRICE_", "").replace("_DAYS", ""))
        except ValueError:
            days = None
        if days is not None:
            return _format_period_label(days, lang_code)

    if section == "traffic" and key.startswith("PRICE_TRAFFIC_"):
        if key.endswith("UNLIMITED"):
            return _format_traffic_label(0, lang_code)
        digits = ''.join(ch for ch in key if ch.isdigit())
        try:
            gb = int(digits)
        except ValueError:
            gb = None
        if gb is not None:
            return _format_traffic_label(gb, lang_code)

    if key == "PRICE_PER_DEVICE":
        return "Дополнительное устройство" if lang_code == "ru" else "Extra device"

    return key


@admin_required
@error_handler
async def show_pricing_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    text, keyboard = _build_overview(db_user.language)
    await _render_message(callback.message, text, keyboard)
    await state.clear()
    await callback.answer()


@admin_required
@error_handler
async def show_pricing_section(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    section = callback.data.split(":", 1)[1]
    text, keyboard = _build_section(section, db_user.language)
    await _render_message(callback.message, text, keyboard)
    await state.clear()
    await callback.answer()


@admin_required
@error_handler
async def start_price_edit(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    _, section, key = callback.data.split(":", 2)
    texts = get_texts(db_user.language)
    label = _resolve_label(section, key, db_user.language)

    item_type = _get_item_type(key)

    if item_type == "bool":
        current_value = getattr(settings, key, False)
        new_value = not bool(current_value)
        await bot_configuration_service.set_value(db, key, new_value)
        await db.commit()
        await state.clear()

        value_text = _format_setting_value(key, db_user.language)
        success_text = texts.t(
            "ADMIN_PRICING_SETTING_SUCCESS",
            "Параметр {item} обновлен: {value}",
        ).format(item=label, value=value_text)
        await callback.message.answer(success_text)

        section_text, section_keyboard = _build_section(section, db_user.language)
        await _render_message(callback.message, section_text, section_keyboard)
        await callback.answer()
        return

    await state.update_data(
        pricing_key=key,
        pricing_section=section,
        pricing_message_id=callback.message.message_id,
    )
    await state.set_state(PricingStates.waiting_for_value)

    if item_type == "price":
        current_price = getattr(settings, key, 0)
        prompt = (
            f"💰 <b>{texts.t('ADMIN_PRICING_EDIT_TITLE', 'Изменение цены')}</b>\n\n"
            f"{texts.t('ADMIN_PRICING_EDIT_TARGET', 'Текущий тариф')}: <b>{html.escape(label)}</b>\n"
            f"{texts.t('ADMIN_PRICING_EDIT_CURRENT', 'Текущее значение')}: <b>{settings.format_price(current_price)}</b>\n\n"
            f"{texts.t('ADMIN_PRICING_EDIT_PROMPT', 'Введите новую стоимость в рублях (например 990 или 990.50). Для бесплатного тарифа укажите 0.')}"
        )
    else:
        current_value = _format_setting_value(key, db_user.language)
        instruction = _build_instruction(definition, item_type, key, db_user.language)
        prompt = (
            f"⚙️ <b>{texts.t('ADMIN_PRICING_SETTING_EDIT_TITLE', 'Изменение параметра')}</b>\n\n"
            f"{texts.t('ADMIN_PRICING_SETTING_EDIT_TARGET', 'Параметр')}: <b>{html.escape(label)}</b>\n"
            f"{texts.t('ADMIN_PRICING_SETTING_EDIT_CURRENT', 'Текущее значение')}: <b>{html.escape(current_value)}</b>\n\n"
            f"{html.escape(instruction)}"
        )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_PRICING_EDIT_CANCEL", "❌ Отмена"),
                    callback_data=f"admin_pricing_section:{section}",
                )
            ]
        ]
    )

    await _render_message(callback.message, prompt, keyboard)
    await callback.answer()


async def process_price_input(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
) -> None:
    data = await state.get_data()
    key = data.get("pricing_key")
    section = data.get("pricing_section", "periods")
    message_id = data.get("pricing_message_id")

    texts = get_texts(db_user.language)

    if not key:
        await message.answer(texts.t("ADMIN_PRICING_EDIT_EXPIRED", "Сессия редактирования истекла."))
        await state.clear()
        return

    raw_value = message.text or ""
    if raw_value.strip().lower() in {"cancel", "отмена"}:
        await state.clear()
        section_text, section_keyboard = _build_section(section, db_user.language)
        if message_id:
            await _render_message_by_id(
                message.bot,
                message.chat.id,
                message_id,
                section_text,
                section_keyboard,
            )
        await message.answer(texts.t("ADMIN_PRICING_EDIT_CANCELLED", "Изменения отменены."))
        return

    item_type = _get_item_type(key)

    if item_type == "price":
        try:
            parsed_value: Any = _parse_price_input(raw_value)
        except ValueError:
            await message.answer(
                texts.t(
                    "ADMIN_PRICING_EDIT_INVALID",
                    "Не удалось распознать цену. Укажите число в рублях (например 990 или 990.50).",
                )
            )
            return
    elif item_type == "periods":
        try:
            parsed_value = _parse_periods_input(raw_value, db_user.language)
        except ValueError as error:
            reason = str(error).strip() or texts.t(
                "ADMIN_PRICING_SETTING_INVALID_GENERIC",
                "Не удалось обновить параметр. Проверьте ввод и попробуйте снова.",
            )
            await message.answer(
                texts.t("ADMIN_PRICING_SETTING_INVALID", "Ошибка: {reason}").format(reason=reason)
            )
            return
    else:
        try:
            parsed_value = bot_configuration_service.parse_user_value(key, raw_value)
        except ValueError as error:
            reason = str(error).strip() or texts.t(
                "ADMIN_PRICING_SETTING_INVALID_GENERIC",
                "Не удалось обновить параметр. Проверьте ввод и попробуйте снова.",
            )
            await message.answer(
                texts.t("ADMIN_PRICING_SETTING_INVALID", "Ошибка: {reason}").format(reason=reason)
            )
            return

    await bot_configuration_service.set_value(db, key, parsed_value)
    await db.commit()

    label = _resolve_label(section, key, db_user.language)
    value_text = _format_setting_value(key, db_user.language)
    success_template = (
        texts.t("ADMIN_PRICING_EDIT_SUCCESS", "Цена для {item} обновлена: {price}")
        if item_type == "price"
        else texts.t("ADMIN_PRICING_SETTING_SUCCESS", "Параметр {item} обновлен: {value}")
    )
    format_kwargs = {"item": label}
    if item_type == "price":
        format_kwargs["price"] = settings.format_price(parsed_value)
    else:
        format_kwargs["value"] = value_text
    await message.answer(success_template.format(**format_kwargs))

    await state.clear()

    if message_id:
        section_text, section_keyboard = _build_section(section, db_user.language)
        await _render_message_by_id(
            message.bot,
            message.chat.id,
            message_id,
            section_text,
            section_keyboard,
        )


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        show_pricing_menu,
        F.data.in_({"admin_pricing", "admin_subs_pricing"}),
    )
    dp.callback_query.register(
        show_pricing_section,
        F.data.startswith("admin_pricing_section:"),
    )
    dp.callback_query.register(
        start_price_edit,
        F.data.startswith("admin_pricing_edit:"),
    )
    dp.message.register(
        process_price_input,
        PricingStates.waiting_for_value,
    )
