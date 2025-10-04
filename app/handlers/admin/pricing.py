import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Tuple

from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.localization.texts import get_texts
from app.services.system_settings_service import bot_configuration_service
from app.states import AdminPricingStates
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)

PERIOD_SETTINGS: Tuple[Tuple[int, str], ...] = (
    (14, "PRICE_14_DAYS"),
    (30, "PRICE_30_DAYS"),
    (60, "PRICE_60_DAYS"),
    (90, "PRICE_90_DAYS"),
    (180, "PRICE_180_DAYS"),
    (360, "PRICE_360_DAYS"),
)

TRAFFIC_SETTINGS: Tuple[Tuple[int, str], ...] = (
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

EXTRA_SETTINGS: Tuple[Tuple[str, str], ...] = (
    ("device", "PRICE_PER_DEVICE"),
)

MAX_PRICE_RUBLES = Decimal("100000")


def _format_price(value: Optional[int]) -> str:
    return settings.format_price(int(value or 0))


def _get_current_value(key: str) -> int:
    current = bot_configuration_service.get_current_value(key)
    if current is None:
        original = bot_configuration_service.get_original_value(key)
        return int(original or 0)
    return int(current)


def _get_period_label(texts, days: int) -> str:
    return texts.t("ADMIN_PRICING_DAYS_LABEL", "{days} дн.").format(days=days)


def _get_traffic_label(texts, amount: int) -> str:
    if amount == 0:
        return texts.t("ADMIN_PRICING_UNLIMITED_LABEL", "Безлимит")
    return texts.t("ADMIN_PRICING_TRAFFIC_GB", "{gb} ГБ").format(gb=amount)


def _build_main_summary(texts) -> str:
    period_lines = [
        texts.t(
            f"PERIOD_{days}_DAYS",
            f"📅 {days} дней - {_format_price(_get_current_value(key))}",
        )
        for days, key in PERIOD_SETTINGS
    ]

    traffic_lines = []
    for amount, key in TRAFFIC_SETTINGS:
        traffic_key = "UNLIMITED" if amount == 0 else f"{amount}GB"
        default_label = (
            f"📊 {_get_traffic_label(texts, amount)} - {_format_price(_get_current_value(key))}"
        )
        traffic_lines.append(
            texts.t(f"TRAFFIC_{traffic_key}", default_label)
        )

    device_line = texts.t(
        "ADMIN_PRICING_DEVICE_LABEL",
        "📱 Дополнительное устройство — {price}",
    ).format(price=_format_price(_get_current_value("PRICE_PER_DEVICE")))

    parts = [
        texts.t("ADMIN_PRICING_TITLE", "💰 <b>Управление ценами</b>"),
        "",
        texts.t(
            "ADMIN_PRICING_DESCRIPTION",
            "Настройте стоимость подписок, пакетов трафика и дополнительных опций.",
        ),
        "",
        texts.t("ADMIN_PRICING_SECTION_PERIODS", "<b>📅 Периоды подписки</b>"),
        "\n".join(period_lines),
        "",
        texts.t("ADMIN_PRICING_SECTION_TRAFFIC", "<b>📦 Пакеты трафика</b>"),
        "\n".join(traffic_lines),
        "",
        texts.t("ADMIN_PRICING_SECTION_EXTRAS", "<b>🔧 Дополнительные опции</b>"),
        device_line,
    ]

    return "\n".join(part for part in parts if part)


def _period_keyboard(texts) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=texts.t("ADMIN_PRICING_EDIT_PERIOD", "✏️ {label}").format(
                    label=_get_period_label(texts, days)
                ),
                callback_data=f"admin_pricing_edit_period_{days}",
            )
        ]
        for days, _ in PERIOD_SETTINGS
    ]
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data="admin_pricing")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _traffic_keyboard(texts) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=texts.t("ADMIN_PRICING_EDIT_TRAFFIC", "✏️ {label}").format(
                    label=_get_traffic_label(texts, amount)
                ),
                callback_data=(
                    f"admin_pricing_edit_traffic_{'unlimited' if amount == 0 else amount}"
                ),
            )
        ]
        for amount, _ in TRAFFIC_SETTINGS
    ]
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data="admin_pricing")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _extras_keyboard(texts) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.t("ADMIN_PRICING_EDIT_DEVICE", "✏️ Цена за устройство"),
                callback_data="admin_pricing_edit_extra_device",
            )
        ],
        [InlineKeyboardButton(text=texts.BACK, callback_data="admin_pricing")],
    ])


def _pricing_main_keyboard(texts) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.t("ADMIN_PRICING_PERIODS", "📅 Периоды подписки"), callback_data="admin_pricing_periods")],
        [InlineKeyboardButton(text=texts.t("ADMIN_PRICING_TRAFFIC", "📦 Трафик-пакеты"), callback_data="admin_pricing_traffic")],
        [InlineKeyboardButton(text=texts.t("ADMIN_PRICING_EXTRAS", "🔧 Доп. опции"), callback_data="admin_pricing_extras")],
        [InlineKeyboardButton(text=texts.BACK, callback_data="admin_panel")],
    ])


def _find_period_setting(days: int) -> Optional[str]:
    for item_days, key in PERIOD_SETTINGS:
        if item_days == days:
            return key
    return None


def _find_traffic_setting(identifier: str) -> Optional[Tuple[int, str]]:
    if identifier == "unlimited":
        identifier_value = 0
    else:
        try:
            identifier_value = int(identifier)
        except ValueError:
            return None
    for amount, key in TRAFFIC_SETTINGS:
        if amount == identifier_value:
            return amount, key
    return None


def _find_extra_setting(slug: str) -> Optional[str]:
    for extra_slug, key in EXTRA_SETTINGS:
        if extra_slug == slug:
            return key
    return None


async def _prompt_price_input(
    callback: types.CallbackQuery,
    state: FSMContext,
    texts,
    label: str,
    current_price: str,
    setting_key: str,
    return_callback: str,
) -> None:
    await state.set_state(AdminPricingStates.waiting_for_price)
    await state.update_data(
        pricing_key=setting_key,
        pricing_label=label,
        return_callback=return_callback,
    )

    await callback.message.edit_text(
        texts.t(
            "ADMIN_PRICING_PROMPT",
            "Введите новую цену для {label}.\nТекущая цена: {current}.\n\nПришлите значение в рублях (пример: 990 или 349.99).",
        ).format(label=label, current=current_price),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.BACK, callback_data=return_callback)]
        ]),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_pricing_dashboard(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        _build_main_summary(texts),
        reply_markup=_pricing_main_keyboard(texts),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_pricing_periods(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    period_lines = [
        texts.t(
            f"PERIOD_{days}_DAYS",
            f"📅 {days} дней - {_format_price(_get_current_value(key))}",
        )
        for days, key in PERIOD_SETTINGS
    ]

    text = "\n".join(
        part
        for part in (
            texts.t("ADMIN_PRICING_TITLE", "💰 <b>Управление ценами</b>"),
            "",
            texts.t("ADMIN_PRICING_SECTION_PERIODS", "<b>📅 Периоды подписки</b>"),
            "\n".join(period_lines),
            "",
            texts.t(
                "ADMIN_PRICING_HINT_PERIODS",
                "Выберите период, чтобы изменить его стоимость.",
            ),
        )
        if part
    )

    await callback.message.edit_text(
        text,
        reply_markup=_period_keyboard(texts),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_pricing_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    traffic_lines = []
    for amount, key in TRAFFIC_SETTINGS:
        traffic_key = "UNLIMITED" if amount == 0 else f"{amount}GB"
        default_label = (
            f"📊 {_get_traffic_label(texts, amount)} - {_format_price(_get_current_value(key))}"
        )
        traffic_lines.append(texts.t(f"TRAFFIC_{traffic_key}", default_label))

    text = "\n".join(
        part
        for part in (
            texts.t("ADMIN_PRICING_TITLE", "💰 <b>Управление ценами</b>"),
            "",
            texts.t("ADMIN_PRICING_SECTION_TRAFFIC", "<b>📦 Пакеты трафика</b>"),
            "\n".join(traffic_lines),
            "",
            texts.t(
                "ADMIN_PRICING_HINT_TRAFFIC",
                "Выберите пакет, чтобы обновить его цену.",
            ),
        )
        if part
    )

    await callback.message.edit_text(
        text,
        reply_markup=_traffic_keyboard(texts),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_pricing_extras(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    device_price = _format_price(_get_current_value("PRICE_PER_DEVICE"))

    text = "\n".join(
        part
        for part in (
            texts.t("ADMIN_PRICING_TITLE", "💰 <b>Управление ценами</b>"),
            "",
            texts.t("ADMIN_PRICING_SECTION_EXTRAS", "<b>🔧 Дополнительные опции</b>"),
            texts.t("ADMIN_PRICING_DEVICE_LABEL", "📱 Дополнительное устройство — {price}").format(
                price=device_price
            ),
            "",
            texts.t(
                "ADMIN_PRICING_HINT_EXTRAS",
                "Обновите стоимость дополнительных опций.",
            ),
        )
        if part
    )

    await callback.message.edit_text(
        text,
        reply_markup=_extras_keyboard(texts),
    )
    await callback.answer()


@admin_required
@error_handler
async def start_edit_period_price(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    try:
        days = int(callback.data.split("_")[-1])
    except ValueError:
        await callback.answer("❌", show_alert=True)
        return

    setting_key = _find_period_setting(days)
    if not setting_key:
        await callback.answer("❌", show_alert=True)
        return

    label = texts.t("ADMIN_PRICING_PERIOD_LABEL", "Тариф на {label}").format(
        label=_get_period_label(texts, days)
    )
    current_price = _format_price(_get_current_value(setting_key))

    await _prompt_price_input(
        callback,
        state,
        texts,
        label,
        current_price,
        setting_key,
        "admin_pricing_periods",
    )


@admin_required
@error_handler
async def start_edit_traffic_price(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    identifier = callback.data.split("_")[-1]
    setting = _find_traffic_setting(identifier)

    if not setting:
        await callback.answer("❌", show_alert=True)
        return

    amount, setting_key = setting
    label = texts.t("ADMIN_PRICING_TRAFFIC_LABEL", "Пакет {label}").format(
        label=_get_traffic_label(texts, amount)
    )
    current_price = _format_price(_get_current_value(setting_key))

    await _prompt_price_input(
        callback,
        state,
        texts,
        label,
        current_price,
        setting_key,
        "admin_pricing_traffic",
    )


@admin_required
@error_handler
async def start_edit_extra_price(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    slug = callback.data.split("_")[-1]
    setting_key = _find_extra_setting(slug)

    if not setting_key:
        await callback.answer("❌", show_alert=True)
        return

    if slug == "device":
        label = texts.t("ADMIN_PRICING_DEVICE_EDIT_LABEL", "Цена за дополнительное устройство")
    else:
        label = slug

    current_price = _format_price(_get_current_value(setting_key))

    await _prompt_price_input(
        callback,
        state,
        texts,
        label,
        current_price,
        setting_key,
        "admin_pricing_extras",
    )


def _parse_price(text: str) -> Decimal:
    cleaned = (text or "").strip().replace(" ", "")
    if not cleaned:
        raise InvalidOperation
    normalized = cleaned.replace(",", ".")
    value = Decimal(normalized)
    return value.quantize(Decimal("0.01"))


@admin_required
@error_handler
async def process_pricing_value(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    data = await state.get_data()
    setting_key = data.get("pricing_key")
    label = data.get("pricing_label")
    return_callback = data.get("return_callback", "admin_pricing")
    texts = get_texts(db_user.language)

    if not setting_key or not label:
        await message.answer(texts.t("ADMIN_PRICING_STATE_EXPIRED", "⚠️ Истекло состояние ввода. Попробуйте снова."))
        await state.clear()
        return

    try:
        price_rubles = _parse_price(message.text)
    except (InvalidOperation, ValueError):
        await message.answer(
            texts.t(
                "ADMIN_PRICING_INVALID_FORMAT",
                "❌ Неверный формат. Используйте числа, например 990 или 349.99.",
            )
        )
        return

    if price_rubles < 0:
        await message.answer(
            texts.t(
                "ADMIN_PRICING_NEGATIVE_VALUE",
                "❌ Цена не может быть отрицательной.",
            )
        )
        return

    if price_rubles > MAX_PRICE_RUBLES:
        await message.answer(
            texts.t(
                "ADMIN_PRICING_TOO_HIGH",
                "❌ Слишком высокая цена. Укажите значение до {limit}.",
            ).format(limit=_format_price(int(MAX_PRICE_RUBLES * 100))),
        )
        return

    price_kopeks = int((price_rubles * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    await bot_configuration_service.set_value(db, setting_key, price_kopeks)
    await state.clear()

    logger.info(
        "Админ %s обновил %s: %s коп.",
        db_user.telegram_id,
        setting_key,
        price_kopeks,
    )

    await message.answer(
        texts.t(
            "ADMIN_PRICING_SUCCESS",
            "✅ Цена для {label} обновлена: {value}",
        ).format(label=label, value=_format_price(price_kopeks)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.BACK, callback_data=return_callback)],
            [
                InlineKeyboardButton(
                    text=texts.t("ADMIN_PRICING_RETURN", "🏠 К управлению ценами"),
                    callback_data="admin_pricing",
                )
            ],
        ]),
    )


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(show_pricing_dashboard, F.data == "admin_pricing")
    dp.callback_query.register(show_pricing_periods, F.data == "admin_pricing_periods")
    dp.callback_query.register(show_pricing_traffic, F.data == "admin_pricing_traffic")
    dp.callback_query.register(show_pricing_extras, F.data == "admin_pricing_extras")
    dp.callback_query.register(
        start_edit_period_price,
        F.data.startswith("admin_pricing_edit_period_"),
    )
    dp.callback_query.register(
        start_edit_traffic_price,
        F.data.startswith("admin_pricing_edit_traffic_"),
    )
    dp.callback_query.register(
        start_edit_extra_price,
        F.data.startswith("admin_pricing_edit_extra_"),
    )
    dp.message.register(
        process_pricing_value,
        AdminPricingStates.waiting_for_price,
    )

