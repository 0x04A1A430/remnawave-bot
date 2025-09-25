import html
import logging
from typing import Optional

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.configuration_service import (
    ConfigurationValidationError,
    configuration_service,
)
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler


logger = logging.getLogger(__name__)
router = Router()

PER_PAGE = 6


async def _build_config_page(language: str, page: int) -> tuple[str, types.InlineKeyboardMarkup, int]:
    data = await configuration_service.get_paginated_items(page, PER_PAGE)
    items = data["items"]
    total = data["total"]
    total_pages = data["total_pages"]
    current_page = data["page"]

    texts = get_texts(language)
    header = texts.t("ADMIN_BOT_CONFIG_TITLE", "🧩 <b>Конфигурация бота</b>")
    hint = texts.t(
        "ADMIN_BOT_CONFIG_HINT",
        "Выберите параметр для изменения. Текущие значения указаны ниже.",
    )
    summary_template = texts.t("ADMIN_BOT_CONFIG_SUMMARY", "Всего параметров: {total}")
    page_template = texts.t("ADMIN_BOT_CONFIG_PAGE_INFO", "Страница {current}/{total}")
    empty_text = texts.t("ADMIN_BOT_CONFIG_EMPTY", "Доступных настроек не найдено.")

    lines = [header, ""]
    if total:
        lines.append(summary_template.format(total=total))
        lines.append(page_template.format(current=current_page, total=total_pages))
        lines.append("")
        lines.append(hint)
        lines.append("")
    else:
        lines.append(empty_text)

    keyboard_rows: list[list[types.InlineKeyboardButton]] = []

    for item in items:
        key = item["key"]
        value_display = html.escape(item["display"])
        type_display = html.escape(item["type"])
        lines.append(f"• <code>{html.escape(key)}</code> = <b>{value_display}</b> <i>({type_display})</i>")

        short_value = item["short_display"]
        button_text = key
        if short_value:
            button_text = f"{key} • {short_value}"
        if len(button_text) > 64:
            button_text = button_text[:61] + "…"

        keyboard_rows.append([
            types.InlineKeyboardButton(
                text=button_text,
                callback_data=f"admin_bot_config_edit|{current_page}|{key}",
            )
        ])

    if total and total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if current_page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="⬅️", callback_data=f"admin_bot_config_page_{current_page - 1}"
                )
            )
        nav_row.append(
            types.InlineKeyboardButton(
                text=f"{current_page}/{total_pages}", callback_data="admin_bot_config_page_current"
            )
        )
        if current_page < total_pages:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="➡️", callback_data=f"admin_bot_config_page_{current_page + 1}"
                )
            )
        keyboard_rows.append(nav_row)

    keyboard_rows.append([
        types.InlineKeyboardButton(
            text=texts.BACK, callback_data="admin_submenu_settings"
        )
    ])

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    text = "\n".join(lines)
    return text, keyboard, current_page


async def _edit_config_message(
    bot,
    chat_id: int,
    message_id: int,
    language: str,
    page: int,
    *,
    business_connection_id: Optional[str] = None,
) -> None:
    text, keyboard, _ = await _build_config_page(language, page)
    edit_kwargs = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "reply_markup": keyboard,
        "parse_mode": "HTML",
    }
    if business_connection_id:
        edit_kwargs["business_connection_id"] = business_connection_id

    try:
        await bot.edit_message_text(**edit_kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        logger.error("Не удалось обновить сообщение конфигурации: %s", exc)


def _map_validation_error(error: ConfigurationValidationError, language: str) -> str:
    texts = get_texts(language)

    if error.code == "invalid_bool":
        return texts.t(
            "ADMIN_BOT_CONFIG_ERROR_BOOL",
            "❌ Значение должно быть true/false (доступно: true, false, 1, 0, yes, no).",
        )
    if error.code == "invalid_int":
        return texts.t("ADMIN_BOT_CONFIG_ERROR_INT", "❌ Значение должно быть целым числом.")
    if error.code == "invalid_float":
        return texts.t(
            "ADMIN_BOT_CONFIG_ERROR_FLOAT",
            "❌ Значение должно быть числом. Используйте точку в качестве разделителя.",
        )
    if error.code == "invalid_value" or error.code == "not_optional":
        return texts.t(
            "ADMIN_BOT_CONFIG_ERROR_REQUIRED",
            "❌ Это обязательный параметр, пустое значение недопустимо.",
        )
    if error.code == "unknown_setting":
        return texts.t(
            "ADMIN_BOT_CONFIG_ERROR_UNKNOWN",
            "❌ Параметр не найден или недоступен.",
        )
    if error.code == "db_error":
        return texts.t(
            "ADMIN_BOT_CONFIG_ERROR_GENERIC",
            "❌ Не удалось обновить параметр. Попробуйте ещё раз.",
        )

    return texts.ERROR


@router.callback_query(F.data == "admin_bot_config")
@admin_required
@error_handler
async def show_bot_config(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    text, keyboard, _ = await _build_config_page(db_user.language, 1)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_bot_config_page_"))
@admin_required
@error_handler
async def paginate_bot_config(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = callback.data.split("_")
    try:
        page = int(data[-1])
    except ValueError:
        page = 1

    await state.clear()
    text, keyboard, _ = await _build_config_page(db_user.language, page)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_bot_config_page_current")
@admin_required
@error_handler
async def current_page_callback(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    await callback.answer()


@router.callback_query(F.data.startswith("admin_bot_config_edit|"))
@admin_required
@error_handler
async def start_edit_config(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    _, page_str, key = callback.data.split("|", 2)
    try:
        page = int(page_str)
    except ValueError:
        page = 1

    try:
        item = await configuration_service.get_item(key)
    except ConfigurationValidationError as error:
        await callback.answer(
            _map_validation_error(error, db_user.language),
            show_alert=True,
        )
        return
    texts = get_texts(db_user.language)

    header = texts.t("ADMIN_BOT_CONFIG_EDIT_TITLE", "✏️ <b>Изменение параметра</b>")
    key_label = texts.t("ADMIN_BOT_CONFIG_KEY_LABEL", "Ключ")
    type_label = texts.t("ADMIN_BOT_CONFIG_TYPE_LABEL", "Тип")
    current_label = texts.t("ADMIN_BOT_CONFIG_CURRENT_LABEL", "Текущее значение")
    instructions = texts.t(
        "ADMIN_BOT_CONFIG_EDIT_INSTRUCTIONS",
        "Отправьте новое значение одним сообщением.",
    )

    optional_note = (
        texts.t(
            "ADMIN_BOT_CONFIG_OPTIONAL_RESET",
            "Чтобы очистить значение, отправьте <code>null</code>.",
        )
        if item["optional"]
        else texts.t(
            "ADMIN_BOT_CONFIG_REQUIRED_NOTE",
            "Этот параметр обязательный — пустое значение недоступно.",
        )
    )

    message_lines = [
        header,
        "",
        f"{key_label}: <code>{html.escape(key)}</code>",
        f"{type_label}: <code>{html.escape(item['type'])}</code>",
        f"{current_label}: <code>{html.escape(item['display'])}</code>",
        "",
        instructions,
        optional_note,
    ]

    back_keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.BACK, callback_data=f"admin_bot_config_page_{page}"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        "\n".join(message_lines),
        reply_markup=back_keyboard,
        parse_mode="HTML",
    )

    await state.update_data(
        bot_config_key=key,
        bot_config_page=page,
        bot_config_message_chat=callback.message.chat.id,
        bot_config_message_id=callback.message.message_id,
        bot_config_language=db_user.language,
        bot_config_business_connection_id=(
            str(getattr(callback.message, "business_connection_id", None))
            if getattr(callback.message, "business_connection_id", None) is not None
            else None
        ),
    )
    await state.set_state(AdminStates.editing_bot_config_value)
    await callback.answer()


@router.message(AdminStates.editing_bot_config_value)
@admin_required
@error_handler
async def handle_config_update(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    if not message.text:
        error_text = get_texts(db_user.language).t(
            "ADMIN_BOT_CONFIG_ERROR_TEXT_REQUIRED",
            "❌ Отправьте текстовое значение.",
        )
        await message.answer(error_text)
        return

    data = await state.get_data()
    key = data.get("bot_config_key")
    if not key:
        await state.clear()
        await message.answer(
            get_texts(db_user.language).t(
                "ADMIN_BOT_CONFIG_ERROR_UNKNOWN",
                "❌ Параметр не найден или недоступен.",
            )
        )
        return

    try:
        _, display = await configuration_service.update_setting(key, message.text)
    except ConfigurationValidationError as error:
        await message.answer(_map_validation_error(error, db_user.language))
        return

    success_text = get_texts(db_user.language).t(
        "ADMIN_BOT_CONFIG_UPDATED",
        "✅ Параметр <code>{key}</code> обновлён: <b>{value}</b>",
    ).format(key=html.escape(key), value=html.escape(display))

    await message.answer(success_text, parse_mode="HTML")

    chat_id = data.get("bot_config_message_chat")
    message_id = data.get("bot_config_message_id")
    language = data.get("bot_config_language", db_user.language)
    page = data.get("bot_config_page", 1)
    business_connection_id = data.get("bot_config_business_connection_id")

    if chat_id and message_id:
        await _edit_config_message(
            message.bot,
            chat_id,
            message_id,
            language,
            page,
            business_connection_id=business_connection_id,
        )

    await state.clear()


def register_handlers(dp):
    dp.include_router(router)

