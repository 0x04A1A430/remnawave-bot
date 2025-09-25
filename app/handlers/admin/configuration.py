import html
import logging
import math
from typing import Dict, List, Tuple

from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.admin import (
    get_bot_config_categories_keyboard,
    get_bot_config_settings_keyboard,
)
from app.services.app_settings_service import AppSettingsService
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)

CONFIGS_PER_PAGE = 8
CANCEL_COMMANDS = {"отмена", "cancel", "/cancel"}


@admin_required
@error_handler
async def show_bot_config_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    categories = AppSettingsService.get_categories()
    keyboard = get_bot_config_categories_keyboard(categories)

    intro_lines = [
        "🧩 <b>Конфигурация бота</b>",
        "",
        "Изменения применяются сразу.",
        "Отправьте <code>reset</code> для возврата значения по умолчанию.",
        "Некоторые изменения могут потребовать перезапуска бота.",
    ]

    if not categories:
        intro_lines.append("\nНастройки недоступны для редактирования.")

    text = "\n".join(intro_lines)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await state.update_data(
        bot_config_message_id=callback.message.message_id,
        bot_config_category_key=None,
        bot_config_category_page=1,
    )
    await callback.answer()


@admin_required
@error_handler
async def show_bot_config_category(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = callback.data
    if data == "admin_bot_config_cat:noop":
        await callback.answer()
        return

    parts = data.split(":")
    if len(parts) < 2:
        await callback.answer()
        return

    category_key = parts[1].upper()
    try:
        page = int(parts[2]) if len(parts) > 2 else 1
    except ValueError:
        page = 1

    await state.clear()
    await _render_category_view(callback.message, state, category_key, page)
    await callback.answer()


@admin_required
@error_handler
async def prompt_bot_config_edit(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    key = callback.data.split(":", 1)[-1]
    try:
        field_info = AppSettingsService.get_field_info(key)
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return

    state_data = await state.get_data()
    category_key = state_data.get("bot_config_category_key") or field_info["category_key"]
    page = state_data.get("bot_config_category_page", 1)
    message_id = state_data.get("bot_config_message_id", callback.message.message_id)

    default_display = settings.format_value_for_display(key, field_info["default_value"])
    current_display = field_info["display_value"] or "—"

    text = (
        "✏️ <b>Изменение настройки</b>\n\n"
        f"<b>Ключ:</b> <code>{key}</code>\n"
        f"<b>Название:</b> {html.escape(field_info['label'])}\n"
        f"<b>Тип:</b> {field_info['type']}\n"
        f"<b>Текущее значение:</b> <code>{html.escape(str(current_display))}</code>\n"
        f"<b>По умолчанию:</b> <code>{html.escape(str(default_display))}</code>\n\n"
        "Отправьте новое значение одним сообщением.\n"
        "Для возврата по умолчанию отправьте <code>reset</code>.\n"
        "Для отмены отправьте <code>отмена</code> или <code>/cancel</code>."
    )

    back_callback = f"admin_bot_config_cat:{category_key}:{page}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)]
        ]
    )

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

    await state.update_data(
        bot_config_edit_key=key,
        bot_config_category_key=category_key,
        bot_config_category_page=page,
        bot_config_message_id=message_id,
    )
    await state.set_state(AdminStates.editing_bot_config_value)
    await callback.answer()


@admin_required
@error_handler
async def process_bot_config_edit(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    state_data = await state.get_data()
    key = state_data.get("bot_config_edit_key")
    category_key = state_data.get("bot_config_category_key")
    page = state_data.get("bot_config_category_page", 1)
    message_id = state_data.get("bot_config_message_id")

    if not key:
        await message.answer("⚠️ Не удалось определить настройку. Выберите её снова из меню.")
        await state.clear()
        return

    text = (message.text or "").strip()

    if text.lower() in CANCEL_COMMANDS:
        await message.answer("❌ Изменение отменено.")
        await state.clear()
        if category_key and message_id:
            await _edit_category_message(message.bot, message.chat.id, message_id, category_key, page, state)
        else:
            await message.answer("ℹ️ Вернитесь к списку настроек через меню администратора.")
        return

    try:
        result = await AppSettingsService.update_setting(key, text)
    except ValueError as error:
        await message.answer(f"❌ {error}")
        return

    display_value = html.escape(str(result["display"])) if result.get("display") is not None else "—"
    await message.answer(
        f"✅ Настройка <code>{key}</code> обновлена до значения <code>{display_value}</code>",
        parse_mode="HTML"
    )

    await state.clear()

    if category_key and message_id:
        await _edit_category_message(message.bot, message.chat.id, message_id, category_key, page, state)
    else:
        await message.answer("ℹ️ Откройте категорию настроек заново через меню администратора.")


async def _render_category_view(
    message: types.Message,
    state: FSMContext,
    category_key: str,
    page: int,
) -> None:
    text, keyboard, actual_page = _build_category_view(category_key, page)
    try:
        await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as error:
        logger.debug("Не удалось обновить сообщение конфигурации: %s", error)
    await state.update_data(
        bot_config_category_key=category_key,
        bot_config_category_page=actual_page,
        bot_config_message_id=message.message_id,
    )


async def _edit_category_message(
    bot: Bot,
    chat_id: int,
    message_id: int,
    category_key: str,
    page: int,
    state: FSMContext,
) -> None:
    text, keyboard, actual_page = _build_category_view(category_key, page)
    try:
        await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except TelegramBadRequest as error:
        logger.debug("Не удалось обновить сообщение конфигурации: %s", error)
        return

    await state.update_data(
        bot_config_category_key=category_key,
        bot_config_category_page=actual_page,
        bot_config_message_id=message_id,
    )


def _build_category_view(
    category_key: str,
    page: int,
) -> Tuple[str, InlineKeyboardMarkup, int]:
    fields = AppSettingsService.get_category_fields(category_key)
    total = len(fields)
    total_pages = max(1, math.ceil(total / CONFIGS_PER_PAGE))
    page = max(1, min(page, total_pages))
    start_index = (page - 1) * CONFIGS_PER_PAGE
    end_index = start_index + CONFIGS_PER_PAGE
    page_fields = fields[start_index:end_index]

    category_label = (
        fields[0]["category_label"]
        if fields
        else settings.get_field_category_label(category_key)
    )

    lines = [
        f"🧩 <b>{category_label}</b>",
        "",
        "Выберите настройку для редактирования.",
        "Отправьте <code>reset</code> для возврата по умолчанию.",
        "",
    ]

    if not page_fields:
        lines.append("Настройки отсутствуют.")
    else:
        for field in page_fields:
            indicator = "⭐" if field.get("is_overridden") else "•"
            value_display = field.get("display_value")
            value_str = html.escape(str(value_display)) if value_display is not None else "—"
            value_str = value_str.replace("\n", " ")
            if len(value_str) > 120:
                value_str = value_str[:117] + "…"
            lines.append(f"{indicator} <code>{field['key']}</code> → <code>{value_str}</code>")

    text = "\n".join(lines)
    keyboard = get_bot_config_settings_keyboard(category_key, fields, page, CONFIGS_PER_PAGE, total_pages)
    return text, keyboard, page


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(
        show_bot_config_menu,
        F.data == "admin_bot_config"
    )
    dp.callback_query.register(
        show_bot_config_category,
        F.data.startswith("admin_bot_config_cat:")
    )
    dp.callback_query.register(
        prompt_bot_config_edit,
        F.data.startswith("admin_bot_config_edit:")
    )
    dp.message.register(
        process_bot_config_edit,
        AdminStates.editing_bot_config_value
    )
