import logging
from typing import Iterable, List, Tuple

from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.admin import get_admin_pricing_keyboard
from app.localization.texts import get_texts
from app.services.system_settings_service import bot_configuration_service
from app.states import PricingStates
from app.utils.decorators import admin_required, error_handler


logger = logging.getLogger(__name__)

SUBSCRIPTION_PRICE_ENTRIES: List[Tuple[str, str, str]] = [
    ("base", "BASE_SUBSCRIPTION_PRICE", "Базовая цена"),
    ("14", "PRICE_14_DAYS", "14 дней"),
    ("30", "PRICE_30_DAYS", "30 дней"),
    ("60", "PRICE_60_DAYS", "60 дней"),
    ("90", "PRICE_90_DAYS", "90 дней"),
    ("180", "PRICE_180_DAYS", "180 дней"),
    ("360", "PRICE_360_DAYS", "360 дней"),
]

TRAFFIC_PRICE_ENTRIES: List[Tuple[str, str, str]] = [
    ("5", "PRICE_TRAFFIC_5GB", "5 ГБ"),
    ("10", "PRICE_TRAFFIC_10GB", "10 ГБ"),
    ("25", "PRICE_TRAFFIC_25GB", "25 ГБ"),
    ("50", "PRICE_TRAFFIC_50GB", "50 ГБ"),
    ("100", "PRICE_TRAFFIC_100GB", "100 ГБ"),
    ("250", "PRICE_TRAFFIC_250GB", "250 ГБ"),
    ("500", "PRICE_TRAFFIC_500GB", "500 ГБ"),
    ("1000", "PRICE_TRAFFIC_1000GB", "1000 ГБ"),
    ("unlimited", "PRICE_TRAFFIC_UNLIMITED", "Безлимит"),
]

DEVICE_PRICE_ENTRY: Tuple[str, str, str] = (
    "devices",
    "PRICE_PER_DEVICE",
    "Дополнительное устройство",
)

MAX_PRICE_RUBLES = 1_000_000


def _format_price(value: int) -> str:
    return settings.format_price(int(value))


def _build_price_buttons(
    entries: Iterable[Tuple[str, str, str]],
    prefix: str,
) -> List[List[types.InlineKeyboardButton]]:
    buttons: List[List[types.InlineKeyboardButton]] = []
    row: List[types.InlineKeyboardButton] = []

    for token, key, label in entries:
        current_value = bot_configuration_service.get_current_value(key)
        button = types.InlineKeyboardButton(
            text=f"{label} — {_format_price(current_value)}",
            callback_data=f"admin_pricing_edit_{prefix}_{token}",
        )
        row.append(button)
        if len(row) == 2:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    return buttons


async def _prompt_price_input(
    callback: types.CallbackQuery,
    state: FSMContext,
    key: str,
    label: str,
    return_callback: str,
    language: str,
) -> None:
    texts = get_texts(language)
    current_value = bot_configuration_service.get_current_value(key)
    current_price = _format_price(current_value)

    await state.set_state(PricingStates.waiting_for_value)
    await state.update_data(
        target_key=key,
        target_label=label,
        return_callback=return_callback,
    )

    prompt_lines = [
        texts.t("ADMIN_PRICING_EDIT_TITLE", "💰 <b>Изменение цены</b>"),
        "",
        texts.t("ADMIN_PRICING_CURRENT_PRICE", "Текущая цена: {price}").format(
            price=current_price
        ),
        texts.t("ADMIN_PRICING_PROMPT", "Отправьте новую цену для {name}:").format(
            name=label
        ),
        texts.t(
            "ADMIN_PRICING_ENTER_PRICE",
            "Введите новую цену в рублях (можно использовать копейки через точку).",
        ),
        texts.t("ADMIN_PRICING_CANCEL_HINT", "Для отмены воспользуйтесь кнопкой ниже."),
    ]

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_PRICING_CANCEL", "❌ Отмена"),
                    callback_data=return_callback,
                )
            ]
        ]
    )

    await callback.message.edit_text(
        "\n".join(prompt_lines),
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def show_pricing_menu(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    await state.clear()
    texts = get_texts(db_user.language)

    summary_lines = [
        texts.t("ADMIN_PRICING_TITLE", "💰 <b>Управление ценами</b>"),
        "",
        texts.t(
            "ADMIN_PRICING_DESCRIPTION",
            "Выберите раздел для редактирования стоимости подписок, трафика и устройств.",
        ),
        "",
    ]

    summary_lines.append(texts.t("ADMIN_PRICING_OVERVIEW", "Ключевые значения:"))
    summary_lines.append(
        f"• 30 дней: {_format_price(settings.PRICE_30_DAYS)}"
    )
    summary_lines.append(
        f"• 90 дней: {_format_price(settings.PRICE_90_DAYS)}"
    )
    summary_lines.append(
        f"• {_format_price(settings.PRICE_PER_DEVICE)} — {texts.t('ADMIN_PRICING_DEVICE_SHORT', 'дополнительное устройство')}"
    )

    await callback.message.edit_text(
        "\n".join(summary_lines),
        reply_markup=get_admin_pricing_keyboard(db_user.language),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def show_subscription_prices(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    await state.clear()
    texts = get_texts(db_user.language)

    lines = [
        texts.t("ADMIN_PRICING_SUBSCRIPTIONS_TITLE", "📅 <b>Стоимость подписок</b>"),
        "",
        texts.t(
            "ADMIN_PRICING_SUBSCRIPTIONS_HELP",
            "Нажмите на период, чтобы изменить цену. Значения указаны в месяцах.",
        ),
        "",
    ]

    for _token, key, label in SUBSCRIPTION_PRICE_ENTRIES:
        lines.append(f"• {label}: {_format_price(bot_configuration_service.get_current_value(key))}")

    keyboard_rows = _build_price_buttons(SUBSCRIPTION_PRICE_ENTRIES, "subscription")
    keyboard_rows.append(
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_pricing")]
    )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def show_traffic_prices(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    await state.clear()
    texts = get_texts(db_user.language)

    lines = [
        texts.t("ADMIN_PRICING_TRAFFIC_TITLE", "📦 <b>Стоимость пакетов трафика</b>"),
        "",
        texts.t(
            "ADMIN_PRICING_TRAFFIC_HELP",
            "Нажмите на пакет, чтобы обновить цену. Цена применяется за выбранный объём трафика.",
        ),
        "",
    ]

    for _token, key, label in TRAFFIC_PRICE_ENTRIES:
        lines.append(f"• {label}: {_format_price(bot_configuration_service.get_current_value(key))}")

    keyboard_rows = _build_price_buttons(TRAFFIC_PRICE_ENTRIES, "traffic")
    keyboard_rows.append(
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_pricing")]
    )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def show_device_price(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    await state.clear()
    texts = get_texts(db_user.language)

    _token, key, label = DEVICE_PRICE_ENTRY
    current_value = bot_configuration_service.get_current_value(key)

    lines = [
        texts.t("ADMIN_PRICING_DEVICES_TITLE", "📱 <b>Стоимость дополнительных устройств</b>"),
        "",
        texts.t(
            "ADMIN_PRICING_DEVICES_HELP",
            "Цена применяется за каждое устройство сверх базового лимита подписки.",
        ),
        "",
        f"{label}: {_format_price(current_value)}",
    ]

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_PRICING_EDIT_BUTTON", "✏️ Изменить цену"),
                    callback_data="admin_pricing_edit_devices",
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_pricing")],
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
async def start_subscription_price_edit(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    token = callback.data.split("_")[-1]
    for entry_token, key, label in SUBSCRIPTION_PRICE_ENTRIES:
        if entry_token == token:
            await _prompt_price_input(
                callback,
                state,
                key,
                label,
                "admin_pricing_subscriptions",
                db_user.language,
            )
            return

    await callback.answer("❌ Значение недоступно", show_alert=True)


@admin_required
@error_handler
async def start_traffic_price_edit(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    token = callback.data.split("_")[-1]
    for entry_token, key, label in TRAFFIC_PRICE_ENTRIES:
        if entry_token == token:
            await _prompt_price_input(
                callback,
                state,
                key,
                label,
                "admin_pricing_traffic",
                db_user.language,
            )
            return

    await callback.answer("❌ Значение недоступно", show_alert=True)


@admin_required
@error_handler
async def start_device_price_edit(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    _token, key, label = DEVICE_PRICE_ENTRY
    await _prompt_price_input(
        callback,
        state,
        key,
        label,
        "admin_pricing_devices",
        db_user.language,
    )


@admin_required
@error_handler
async def process_price_input(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    data = await state.get_data()
    key = data.get("target_key")
    label = data.get("target_label", "")
    return_callback = data.get("return_callback", "admin_pricing")
    texts = get_texts(db_user.language)

    if not key:
        await message.answer("❌ Не удалось определить настройку цены. Попробуйте снова из меню цен.")
        await state.clear()
        return

    raw_text = (message.text or "").strip()
    if raw_text.lower() in {"cancel", "отмена"}:
        await state.clear()
        await message.answer(
            texts.t("ADMIN_PRICING_CANCELLED", "Отменено."),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text=texts.BACK,
                            callback_data=return_callback,
                        )
                    ]
                ]
            ),
        )
        return

    try:
        price_rubles = float(raw_text.replace(",", "."))
    except ValueError:
        await message.answer(
            texts.t(
                "ADMIN_PRICING_INVALID_PRICE",
                "❌ Неверный формат цены. Используйте число, например 199.90",
            )
        )
        return

    if price_rubles < 0:
        await message.answer(
            texts.t("ADMIN_PRICING_INVALID_PRICE", "❌ Неверный формат цены. Используйте число, например 199.90"),
        )
        return

    if price_rubles > MAX_PRICE_RUBLES:
        await message.answer(
            texts.t(
                "ADMIN_PRICING_TOO_HIGH",
                "❌ Слишком высокая цена. Укажите значение до 1 000 000 ₽.",
            )
        )
        return

    price_kopeks = int(round(price_rubles * 100))

    await bot_configuration_service.set_value(db, key, price_kopeks)
    await state.clear()

    logger.info("✅ Обновлена цена %s: %s ₽", key, price_rubles)

    confirmation = texts.t(
        "ADMIN_PRICING_PRICE_UPDATED",
        "✅ Цена обновлена: {name} — {price}",
    ).format(name=label or key, price=_format_price(price_kopeks))

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_PRICING_BACK_TO_SECTION", "⬅️ Назад к разделу"),
                    callback_data=return_callback,
                )
            ]
        ]
    )

    await message.answer(confirmation, reply_markup=keyboard, parse_mode="HTML")


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(show_pricing_menu, F.data == "admin_pricing")
    dp.callback_query.register(
        show_subscription_prices, F.data == "admin_pricing_subscriptions"
    )
    dp.callback_query.register(
        show_traffic_prices, F.data == "admin_pricing_traffic"
    )
    dp.callback_query.register(show_device_price, F.data == "admin_pricing_devices")
    dp.callback_query.register(
        start_subscription_price_edit,
        F.data.startswith("admin_pricing_edit_subscription_"),
    )
    dp.callback_query.register(
        start_traffic_price_edit, F.data.startswith("admin_pricing_edit_traffic_"),
    )
    dp.callback_query.register(
        start_device_price_edit, F.data == "admin_pricing_edit_devices"
    )
    dp.message.register(
        process_price_input,
        PricingStates.waiting_for_value,
    )

