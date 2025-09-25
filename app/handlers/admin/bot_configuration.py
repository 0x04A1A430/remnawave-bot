import math
import time
from typing import Iterable, List, Optional, Tuple

from aiogram import Dispatcher, F, types
from aiogram.filters import BaseFilter, StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.system_settings_service import bot_configuration_service
from app.states import BotConfigStates
from app.utils.decorators import admin_required, error_handler


CATEGORY_PAGE_SIZE = 10
SETTINGS_PAGE_SIZE = 8


CATEGORY_GROUP_DEFINITIONS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    (
        "core",
        "⚙️ Основные настройки",
        (
            "DEFAULT",
            "ADMIN",
            "SUPPORT",
            "REMNAWAVE",
            "VERSION",
            "DEBUG",
            "LOG",
            "BACKUP",
        ),
    ),
    (
        "infrastructure",
        "🗄️ Инфраструктура",
        ("DATABASE", "POSTGRES", "SQLITE", "REDIS", "WEBHOOK", "SERVER", "MONITORING", "MAINTENANCE"),
    ),
    (
        "billing",
        "💳 Оплаты и тарифы",
        ("PRICE", "PAYMENT", "YOOKASSA", "CRYPTOBOT", "MULENPAY", "PAL24", "AUTOPAY", "TRIAL"),
    ),
    (
        "communication",
        "📡 Подключение и коммуникации",
        ("CONNECT", "CHANNEL", "TELEGRAM", "HAPP"),
    ),
    (
        "loyalty",
        "🎁 Рефералы и лояльность",
        ("REFERRAL", "TRIBUTE"),
    ),
)

CATEGORY_FALLBACK_KEY = "other"
CATEGORY_FALLBACK_TITLE = "📦 Прочие настройки"


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


def _parse_category_payload(payload: str) -> Tuple[str, str, int, int]:
    parts = payload.split(":")
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    category_key = parts[2] if len(parts) > 2 else ""

    def _safe_int(value: str, default: int = 1) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    category_page = _safe_int(parts[3]) if len(parts) > 3 else 1
    settings_page = _safe_int(parts[4]) if len(parts) > 4 else 1
    return group_key, category_key, category_page, settings_page


def _parse_group_payload(payload: str) -> Tuple[str, int]:
    parts = payload.split(":")
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        page = max(1, int(parts[2]))
    except (IndexError, ValueError):
        page = 1
    return group_key, page


async def _resolve_key_from_token(
    callback: types.CallbackQuery, token: str
) -> Optional[str]:
    key = bot_configuration_service.resolve_key_from_token(token)
    if key:
        return key

    await callback.answer("Эта настройка больше недоступна", show_alert=True)
    return None


def _get_grouped_categories() -> List[Tuple[str, str, List[Tuple[str, str, int]]]]:
    categories = bot_configuration_service.get_categories()
    categories_map = {key: (label, count) for key, label, count in categories}
    used: set[str] = set()
    grouped: List[Tuple[str, str, List[Tuple[str, str, int]]]] = []

    for group_key, title, category_keys in CATEGORY_GROUP_DEFINITIONS:
        items: List[Tuple[str, str, int]] = []
        for category_key in category_keys:
            if category_key in categories_map:
                label, count = categories_map[category_key]
                items.append((category_key, label, count))
                used.add(category_key)
        if items:
            grouped.append((group_key, title, items))

    remaining = [
        (key, label, count)
        for key, (label, count) in categories_map.items()
        if key not in used
    ]

    if remaining:
        remaining.sort(key=lambda item: item[1])
        grouped.append((CATEGORY_FALLBACK_KEY, CATEGORY_FALLBACK_TITLE, remaining))

    return grouped


def _build_groups_keyboard() -> types.InlineKeyboardMarkup:
    grouped = _get_grouped_categories()
    rows: list[list[types.InlineKeyboardButton]] = []

    for group_key, title, items in grouped:
        total = sum(count for _, _, count in items)
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"{title} ({total})",
                    callback_data=f"botcfg_group:{group_key}:1",
                )
            ]
        )

    rows.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="admin_submenu_settings",
            )
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_categories_keyboard(
    group_key: str,
    group_title: str,
    categories: List[Tuple[str, str, int]],
    page: int = 1,
) -> types.InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(categories) / CATEGORY_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * CATEGORY_PAGE_SIZE
    end = start + CATEGORY_PAGE_SIZE
    sliced = categories[start:end]

    rows: list[list[types.InlineKeyboardButton]] = []
    rows.append(
        [
            types.InlineKeyboardButton(
                text=f"— {group_title} —",
                callback_data="botcfg_group:noop",
            )
        ]
    )

    buttons: List[types.InlineKeyboardButton] = []
    for category_key, label, count in sliced:
        button_text = f"{label} ({count})"
        buttons.append(
            types.InlineKeyboardButton(
                text=button_text,
                callback_data=f"botcfg_cat:{group_key}:{category_key}:{page}:1",
            )
        )

    for chunk in _chunk(buttons, 2):
        rows.append(list(chunk))

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"botcfg_group:{group_key}:{page - 1}",
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
                    callback_data=f"botcfg_group:{group_key}:{page + 1}",
                )
            )
        rows.append(nav_row)

    rows.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ К разделам",
                callback_data="admin_bot_config",
            )
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_settings_keyboard(
    category_key: str,
    group_key: str,
    category_page: int,
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
        button_text = f"{definition.display_name} · {value_preview}"
        if len(button_text) > 64:
            button_text = button_text[:63] + "…"
        token = bot_configuration_service.get_callback_token(definition.key)
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=button_text,
                    callback_data=(
                        f"botcfg_setting:{group_key}:{category_page}:{page}:{token}"
                    ),
                )
            ]
        )

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="⬅️",
                    callback_data=(
                        f"botcfg_cat:{group_key}:{category_key}:{category_page}:{page - 1}"
                    ),
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
                    callback_data=(
                        f"botcfg_cat:{group_key}:{category_key}:{category_page}:{page + 1}"
                    ),
                )
            )
        rows.append(nav_row)

    rows.append([
        types.InlineKeyboardButton(
            text="⬅️ К категориям",
            callback_data=f"botcfg_group:{group_key}:{category_page}",
        )
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_setting_keyboard(
    key: str,
    group_key: str,
    category_page: int,
    settings_page: int,
) -> types.InlineKeyboardMarkup:
    definition = bot_configuration_service.get_definition(key)
    rows: list[list[types.InlineKeyboardButton]] = []
    token = bot_configuration_service.get_callback_token(key)

    if definition.python_type is bool:
        rows.append([
            types.InlineKeyboardButton(
                text="🔁 Переключить",
                callback_data=(
                    f"botcfg_toggle:{group_key}:{category_page}:{settings_page}:{token}"
                ),
            )
        ])

    if definition.has_choices:
        rows.append([
            types.InlineKeyboardButton(
                text="📋 Выбрать",
                callback_data=(
                    f"botcfg_choices:{group_key}:{category_page}:{settings_page}:{token}"
                ),
            )
        ])

    rows.append([
        types.InlineKeyboardButton(
            text="✏️ Изменить",
            callback_data=(
                f"botcfg_edit:{group_key}:{category_page}:{settings_page}:{token}"
            ),
        )
    ])

    if bot_configuration_service.has_override(key):
        rows.append([
            types.InlineKeyboardButton(
                text="♻️ Сбросить",
                callback_data=(
                    f"botcfg_reset:{group_key}:{category_page}:{settings_page}:{token}"
                ),
            )
        ])

    rows.append([
        types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=(
                f"botcfg_cat:{group_key}:{definition.category_key}:{category_page}:{settings_page}"
            ),
        )
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_choices_keyboard(
    key: str,
    group_key: str,
    category_page: int,
    settings_page: int,
) -> types.InlineKeyboardMarkup:
    token = bot_configuration_service.get_callback_token(key)
    choices = bot_configuration_service.get_choices(key)
    current_value = bot_configuration_service.get_current_value(key)

    rows: list[list[types.InlineKeyboardButton]] = []

    for index, (value, label) in enumerate(choices, start=1):
        if isinstance(value, str) and isinstance(current_value, str):
            is_selected = value.lower() == current_value.lower()
        else:
            is_selected = value == current_value
        prefix = "✅ " if is_selected else ""
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"{prefix}{label}",
                    callback_data=(
                        f"botcfg_choice_set:{group_key}:{category_page}:{settings_page}:{token}:{index}"
                    ),
                )
            ]
        )

    rows.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=(
                    f"botcfg_setting:{group_key}:{category_page}:{settings_page}:{token}"
                ),
            )
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _render_setting_text(key: str) -> str:
    summary = bot_configuration_service.get_setting_summary(key)

    lines = [
        "🧩 <b>Настройка</b>",
        f"<b>Название:</b> {summary['name']}",
        f"<b>Ключ:</b> <code>{summary['key']}</code>",
        f"<b>Категория:</b> {summary['category_label']}",
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
    keyboard = _build_groups_keyboard()
    await callback.message.edit_text(
        "🧩 <b>Конфигурация бота</b>\n\nВыберите раздел настроек:",
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def show_bot_config_group(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    group_key, page = _parse_group_payload(callback.data)
    grouped = _get_grouped_categories()
    group_lookup = {key: (title, items) for key, title, items in grouped}

    if group_key not in group_lookup:
        await callback.answer("Эта группа больше недоступна", show_alert=True)
        return

    group_title, items = group_lookup[group_key]
    keyboard = _build_categories_keyboard(group_key, group_title, items, page)
    await callback.message.edit_text(
        f"🧩 <b>{group_title}</b>\n\nВыберите категорию настроек:",
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
    group_key, category_key, category_page, settings_page = _parse_category_payload(
        callback.data
    )
    definitions = bot_configuration_service.get_settings_for_category(category_key)

    if not definitions:
        await callback.answer("В этой категории пока нет настроек", show_alert=True)
        return

    category_label = definitions[0].category_label
    keyboard = _build_settings_keyboard(
        category_key,
        group_key,
        category_page,
        db_user.language,
        settings_page,
    )
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
    state: FSMContext,
):
    parts = callback.data.split(":", 4)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    key = await _resolve_key_from_token(callback, token)
    if not key:
        return
    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
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
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    key = await _resolve_key_from_token(callback, token)
    if not key:
        return
    definition = bot_configuration_service.get_definition(key)

    summary = bot_configuration_service.get_setting_summary(key)
    texts = get_texts(db_user.language)

    instructions = [
        "✏️ <b>Редактирование настройки</b>",
        f"Название: {summary['name']}",
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
                        text=texts.BACK,
                        callback_data=(
                            f"botcfg_setting:{group_key}:{category_page}:{settings_page}:{token}"
                        ),
                    )
                ]
            ]
        ),
    )

    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
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
    group_key = data.get("setting_group_key", CATEGORY_FALLBACK_KEY)
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

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await message.answer("✅ Настройка обновлена")
    await message.answer(text, reply_markup=keyboard)
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
    group_key = data.get("setting_group_key", CATEGORY_FALLBACK_KEY)
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

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await message.answer("✅ Настройка обновлена")
    await message.answer(text, reply_markup=keyboard)

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
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    key = await _resolve_key_from_token(callback, token)
    if not key:
        return
    await bot_configuration_service.reset_value(db, key)
    await db.commit()

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard)
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
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    key = await _resolve_key_from_token(callback, token)
    if not key:
        return
    current = bot_configuration_service.get_current_value(key)
    new_value = not bool(current)
    await bot_configuration_service.set_value(db, key, new_value)
    await db.commit()

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard)
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
async def show_setting_choices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 5)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    key = await _resolve_key_from_token(callback, token)
    if not key:
        return

    choices = bot_configuration_service.get_choices(key)
    if not choices:
        await callback.answer("Для этой настройки недоступен выбор", show_alert=True)
        return

    summary = bot_configuration_service.get_setting_summary(key)
    keyboard = _build_choices_keyboard(key, group_key, category_page, settings_page)

    text = "\n".join(
        [
            "📋 <b>Выбор значения</b>",
            f"Название: {summary['name']}",
            f"Текущий вариант: {summary['current']}",
            "",
            "Выберите значение из списка ниже.",
        ]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer()


@admin_required
@error_handler
async def apply_setting_choice(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 6)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    key = await _resolve_key_from_token(callback, token)
    if not key:
        return

    choices = bot_configuration_service.get_choices(key)
    if not choices:
        await callback.answer("Варианты для этой настройки недоступны", show_alert=True)
        return

    try:
        index = max(1, int(parts[5])) if len(parts) > 5 else 1
    except ValueError:
        await callback.answer("Некорректный вариант", show_alert=True)
        return

    try:
        selected_value, label = choices[index - 1]
    except IndexError:
        await callback.answer("Некорректный вариант", show_alert=True)
        return

    cast_value = bot_configuration_service.cast_choice_value(key, selected_value)
    await bot_configuration_service.set_value(db, key, cast_value)
    await db.commit()

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer(f"Установлено: {label}")


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
        show_bot_config_category,
        F.data.startswith("botcfg_cat:"),
    )
    dp.callback_query.register(
        show_bot_config_setting,
        F.data.startswith("botcfg_setting:"),
    )
    dp.callback_query.register(
        show_setting_choices,
        F.data.startswith("botcfg_choices:"),
    )
    dp.callback_query.register(
        apply_setting_choice,
        F.data.startswith("botcfg_choice_set:"),
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
        handle_direct_setting_input,
        StateFilter(None),
        F.text,
        BotConfigInputFilter(),
    )
    dp.message.register(
        handle_edit_setting,
        BotConfigStates.waiting_for_value,
    )

