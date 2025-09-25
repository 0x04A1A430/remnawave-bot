import math
from typing import Tuple

from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.system_settings_service import bot_configuration_service
from app.states import BotConfigStates
from app.utils.decorators import admin_required, error_handler


CATEGORY_PAGE_SIZE = 10
SETTINGS_PAGE_SIZE = 8


def _parse_category_payload(payload: str) -> Tuple[str, int]:
    parts = payload.split(":")
    if len(parts) == 3:
        _, category_key, page_raw = parts
        try:
            return category_key, max(1, int(page_raw))
        except ValueError:
            return category_key, 1
    if len(parts) == 2:
        _, category_key = parts
        return category_key, 1
    return "", 1


def _build_categories_keyboard(language: str, page: int = 1) -> types.InlineKeyboardMarkup:
    categories = bot_configuration_service.get_categories()
    total_pages = max(1, math.ceil(len(categories) / CATEGORY_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * CATEGORY_PAGE_SIZE
    end = start + CATEGORY_PAGE_SIZE
    sliced = categories[start:end]

    rows: list[list[types.InlineKeyboardButton]] = []
    for category_key, label, count in sliced:
        button_text = f"{label} ({count})"
        rows.append([
            types.InlineKeyboardButton(
                text=button_text,
                callback_data=f"botcfg_cat:{category_key}:1",
            )
        ])

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="⬅️", callback_data=f"botcfg_categories:{page - 1}"
                )
            )
        nav_row.append(
            types.InlineKeyboardButton(
                text=f"{page}/{total_pages}", callback_data="botcfg_categories:noop"
            )
        )
        if page < total_pages:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="➡️", callback_data=f"botcfg_categories:{page + 1}"
                )
            )
        rows.append(nav_row)

    rows.append([
        types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_settings")
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_settings_keyboard(
    category_key: str,
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

    for definition in sliced:
        value_preview = bot_configuration_service.format_value_for_list(definition.key)
        button_text = f"{definition.key} = {value_preview}"
        rows.append([
            types.InlineKeyboardButton(
                text=button_text,
                callback_data=f"botcfg_setting:{definition.key}",
            )
        ])

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"botcfg_cat:{category_key}:{page - 1}",
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
                    callback_data=f"botcfg_cat:{category_key}:{page + 1}",
                )
            )
        rows.append(nav_row)

    rows.append([
        types.InlineKeyboardButton(
            text="⬅️ К категориям",
            callback_data="admin_bot_config",
        )
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_setting_keyboard(key: str) -> types.InlineKeyboardMarkup:
    definition = bot_configuration_service.get_definition(key)
    rows: list[list[types.InlineKeyboardButton]] = []

    if definition.python_type is bool:
        rows.append([
            types.InlineKeyboardButton(
                text="🔁 Переключить",
                callback_data=f"botcfg_toggle:{key}",
            )
        ])

    rows.append([
        types.InlineKeyboardButton(
            text="✏️ Изменить",
            callback_data=f"botcfg_edit:{key}",
        )
    ])

    if bot_configuration_service.has_override(key):
        rows.append([
            types.InlineKeyboardButton(
                text="♻️ Сбросить",
                callback_data=f"botcfg_reset:{key}",
            )
        ])

    rows.append([
        types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"botcfg_cat:{definition.category_key}:1",
        )
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _render_setting_text(key: str) -> str:
    summary = bot_configuration_service.get_setting_summary(key)

    lines = [
        "🧩 <b>Настройка</b>",
        f"<b>Ключ:</b> <code>{summary['key']}</code>",
        f"<b>Тип:</b> {summary['type']}",
        f"<b>Текущее значение:</b> {summary['current']}",
        f"<b>Значение по умолчанию:</b> {summary['original']}",
        f"<b>Переопределено в БД:</b> {'✅ Да' if summary['has_override'] else '❌ Нет'}",
    ]

    return "\n".join(lines)


@admin_required
@error_handler
async def show_bot_config_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    keyboard = _build_categories_keyboard(db_user.language)
    await callback.message.edit_text(
        "🧩 <b>Конфигурация бота</b>\n\nВыберите категорию настроек:",
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def show_bot_config_categories_page(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split(":")
    try:
        page = int(parts[1])
    except (IndexError, ValueError):
        page = 1

    keyboard = _build_categories_keyboard(db_user.language, page)
    await callback.message.edit_text(
        "🧩 <b>Конфигурация бота</b>\n\nВыберите категорию настроек:",
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def show_bot_config_category(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    category_key, page = _parse_category_payload(callback.data)
    definitions = bot_configuration_service.get_settings_for_category(category_key)

    if not definitions:
        await callback.answer("В этой категории пока нет настроек", show_alert=True)
        return

    category_label = definitions[0].category_label
    keyboard = _build_settings_keyboard(category_key, db_user.language, page)
    await callback.message.edit_text(
        f"🧩 <b>{category_label}</b>\n\nВыберите настройку для просмотра:",
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def show_bot_config_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    key = callback.data.split(":", 1)[1]
    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def start_edit_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    key = callback.data.split(":", 1)[1]
    definition = bot_configuration_service.get_definition(key)

    summary = bot_configuration_service.get_setting_summary(key)
    texts = get_texts(db_user.language)

    instructions = [
        "✏️ <b>Редактирование настройки</b>",
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
                        text=texts.BACK, callback_data=f"botcfg_setting:{key}"
                    )
                ]
            ]
        ),
    )

    await state.update_data(setting_key=key)
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

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key)
    await message.answer("✅ Настройка обновлена")
    await message.answer(text, reply_markup=keyboard)
    await state.clear()


@admin_required
@error_handler
async def reset_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    key = callback.data.split(":", 1)[1]
    await bot_configuration_service.reset_value(db, key)
    await db.commit()

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Сброшено к значению по умолчанию")


@admin_required
@error_handler
async def toggle_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    key = callback.data.split(":", 1)[1]
    current = bot_configuration_service.get_current_value(key)
    new_value = not bool(current)
    await bot_configuration_service.set_value(db, key, new_value)
    await db.commit()

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Обновлено")


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        show_bot_config_menu,
        F.data == "admin_bot_config",
    )
    dp.callback_query.register(
        show_bot_config_categories_page,
        F.data.startswith("botcfg_categories:")
        & (~F.data.endswith(":noop")),
    )
    dp.callback_query.register(
        show_bot_config_category,
        F.data.startswith("botcfg_cat:"),
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
    dp.message.register(
        handle_edit_setting,
        BotConfigStates.waiting_for_value,
    )

