import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, List, Tuple

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


def _language_code(language: str | None) -> str:
    return (language or "ru").split("-")[0].lower()


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

    text = (
        f"💰 <b>{texts.t('ADMIN_PRICING_MENU_TITLE', 'Управление ценами')}</b>\n\n"
        f"{texts.t('ADMIN_PRICING_MENU_DESCRIPTION', 'Быстрый доступ к тарифам и пакетам.')}\n\n"
        f"<b>{texts.t('ADMIN_PRICING_MENU_SUMMARY', 'Краткая сводка:')}</b>\n"
        f"{texts.t('ADMIN_PRICING_MENU_SUMMARY_PERIODS', '• Периоды: {summary}').format(summary=summary_periods)}\n"
        f"{texts.t('ADMIN_PRICING_MENU_SUMMARY_TRAFFIC', '• Трафик: {summary}').format(summary=summary_traffic)}\n"
        f"{texts.t('ADMIN_PRICING_MENU_SUMMARY_EXTRA', '• Дополнительно: {summary}').format(summary=summary_extra)}\n\n"
        f"{texts.t('ADMIN_PRICING_MENU_PROMPT', 'Выберите раздел для редактирования:')}"
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
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
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_panel")],
        ]
    )

    return text, keyboard


def _build_section(
    section: str,
    language: str,
) -> Tuple[str, types.InlineKeyboardMarkup]:
    texts = get_texts(language)
    lang_code = _language_code(language)

    if section == "periods":
        items = _get_period_items(lang_code)
        title = texts.t("ADMIN_PRICING_SECTION_PERIODS_TITLE", "🗓 Периоды подписки")
    elif section == "traffic":
        items = _get_traffic_items(lang_code)
        title = texts.t("ADMIN_PRICING_SECTION_TRAFFIC_TITLE", "📦 Пакеты трафика")
    else:
        items = _get_extra_items(lang_code)
        title = texts.t("ADMIN_PRICING_SECTION_EXTRA_TITLE", "➕ Дополнительные опции")

    lines = [title, ""]

    if items:
        for key, label, price in items:
            lines.append(f"• {label} — {settings.format_price(price)}")
        lines.append("")
        lines.append(texts.t("ADMIN_PRICING_SECTION_PROMPT", "Выберите что изменить:"))
    else:
        lines.append(texts.t("ADMIN_PRICING_SECTION_EMPTY", "Нет доступных значений."))

    keyboard_rows: List[List[types.InlineKeyboardButton]] = []
    for key, label, price in items:
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"{label} • {settings.format_price(price)}",
                    callback_data=f"admin_pricing_edit:{section}:{key}",
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

    await state.update_data(
        pricing_key=key,
        pricing_section=section,
        pricing_message_id=callback.message.message_id,
    )
    await state.set_state(PricingStates.waiting_for_value)

    prompt = (
        f"💰 <b>{texts.t('ADMIN_PRICING_EDIT_TITLE', 'Изменение цены')}</b>\n\n"
        f"{texts.t('ADMIN_PRICING_EDIT_TARGET', 'Текущий тариф')}: <b>{label}</b>\n"
        f"{texts.t('ADMIN_PRICING_EDIT_CURRENT', 'Текущее значение')}: <b>{settings.format_price(getattr(settings, key, 0))}</b>\n\n"
        f"{texts.t('ADMIN_PRICING_EDIT_PROMPT', 'Введите новую стоимость в рублях (например 990 или 990.50). Для бесплатного тарифа укажите 0.')}"
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

    try:
        price_kopeks = _parse_price_input(raw_value)
    except ValueError:
        await message.answer(
            texts.t(
                "ADMIN_PRICING_EDIT_INVALID",
                "Не удалось распознать цену. Укажите число в рублях (например 990 или 990.50).",
            )
        )
        return

    await bot_configuration_service.set_value(db, key, price_kopeks)
    await db.commit()

    label = _resolve_label(section, key, db_user.language)
    await message.answer(
        texts.t("ADMIN_PRICING_EDIT_SUCCESS", "Цена для {item} обновлена: {price}").format(
            item=label,
            price=settings.format_price(price_kopeks),
        )
    )

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
