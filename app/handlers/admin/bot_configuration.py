import html
import io
import math
import time
from datetime import datetime
from textwrap import dedent
from typing import Dict, Iterable, List, Tuple

from aiogram import Dispatcher, F, types
from aiogram.filters import BaseFilter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SystemSettingChange, User
from app.localization.texts import get_texts
from app.config import settings
from app.services.remnawave_service import RemnaWaveService
from app.services.payment_service import PaymentService
from app.services.tribute_service import TributeService
from app.services.system_settings_service import (
    SettingDefinition,
    bot_configuration_service,
)
from app.states import BotConfigStates
from app.utils.decorators import admin_required, error_handler
from app.utils.currency_converter import currency_converter
from app.external.telegram_stars import TelegramStarsService


CATEGORY_PAGE_SIZE = 5
SETTINGS_PAGE_SIZE = 6
MAX_SEARCH_RESULTS = 15
IMPORT_DIFF_PREVIEW_LIMIT = 20
BREADCRUMB_SEPARATOR = " → "
DEFAULT_DASHBOARD_KEY = bot_configuration_service.DASHBOARD_CATEGORIES[0].key


def _collect_dashboard_structure() -> List[Dict[str, object]]:
    categories_map: Dict[str, List[SettingDefinition]] = {}
    for category_key, _, _ in bot_configuration_service.get_categories():
        categories_map[category_key] = bot_configuration_service.get_settings_for_category(
            category_key
        )

    assigned_service_categories: set[str] = set()
    structure: List[Dict[str, object]] = []

    for dashboard_category, _ in bot_configuration_service.get_dashboard_items():
        if dashboard_category.key == "other":
            continue

        service_nodes: List[Dict[str, object]] = []
        collected_definitions: List[SettingDefinition] = []

        for service_category in dashboard_category.service_categories:
            service_definitions = categories_map.get(service_category)
            if not service_definitions:
                continue

            assigned_service_categories.add(service_category)
            collected_definitions.extend(service_definitions)
            summary = bot_configuration_service.summarize_definitions(service_definitions)
            service_nodes.append(
                {
                    "key": service_category,
                    "label": service_definitions[0].category_label,
                    "definitions": service_definitions,
                    "summary": summary,
                }
            )

        if collected_definitions:
            summary = bot_configuration_service.summarize_definitions(collected_definitions)
            structure.append(
                {
                    "dashboard": dashboard_category,
                    "service_nodes": service_nodes,
                    "definitions": collected_definitions,
                    "summary": summary,
                }
            )

    remaining: List[str] = [
        key for key in categories_map if key not in assigned_service_categories
    ]
    if remaining:
        remaining_definitions: List[SettingDefinition] = []
        service_nodes: List[Dict[str, object]] = []
        for service_category in remaining:
            service_definitions = categories_map.get(service_category)
            if not service_definitions:
                continue
            remaining_definitions.extend(service_definitions)
            summary = bot_configuration_service.summarize_definitions(service_definitions)
            service_nodes.append(
                {
                    "key": service_category,
                    "label": service_definitions[0].category_label,
                    "definitions": service_definitions,
                    "summary": summary,
                }
            )

        if remaining_definitions:
            other_category = bot_configuration_service.get_dashboard_category("other")
            summary = bot_configuration_service.summarize_definitions(remaining_definitions)
            structure.append(
                {
                    "dashboard": other_category,
                    "service_nodes": service_nodes,
                    "definitions": remaining_definitions,
                    "summary": summary,
                }
            )

    return structure


def _build_main_menu_keyboard(
    structure: List[Dict[str, object]]
) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for item in structure:
        dashboard = item["dashboard"]
        summary: Dict[str, int] = item["summary"]  # type: ignore[assignment]
        total = summary.get("total", 0)
        attention = summary.get("disabled", 0) + summary.get("empty", 0)
        badge = "🟢" if attention == 0 else ("🟡" if attention < total else "🔴")
        button_text = f"{badge} {dashboard.title} · {total}"
        builder.button(
            text=button_text,
            callback_data=f"botcfg_group:{dashboard.key}:1",
        )

    builder.adjust(2)

    builder.row(
        types.InlineKeyboardButton(
            text="🔍 Найти настройку",
            callback_data="botcfg_search",
        )
    )
    builder.row(
        types.InlineKeyboardButton(
            text="🎚 Пресеты",
            callback_data="botcfg_presets",
        ),
        types.InlineKeyboardButton(
            text="📤 Экспорт .env",
            callback_data="botcfg_export",
        ),
        types.InlineKeyboardButton(
            text="📥 Импорт",
            callback_data="botcfg_import",
        ),
    )
    builder.row(
        types.InlineKeyboardButton(
            text="🕑 История изменений",
            callback_data="botcfg_history",
        )
    )
    builder.row(
        types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="admin_submenu_settings",
        )
    )

    return builder.as_markup()


def _render_main_menu_text(structure: List[Dict[str, object]]) -> str:
    all_definitions: List[SettingDefinition] = []
    for item in structure:
        all_definitions.extend(item.get("definitions", []))

    overall = bot_configuration_service.summarize_definitions(all_definitions)
    lines = [
        "⚙️ <b>Панель управления ботом</b>",
        "Управляйте настройками бота в один клик.",
        "",
        (
            f"🟢 Настроено: {overall.get('active', 0)}"
            f" · 🟡 Требует внимания: {overall.get('disabled', 0)}"
            f" · ⚪ Не заполнено: {overall.get('empty', 0)}"
        ),
        "",
        "Выберите раздел:",
    ]

    for item in structure:
        dashboard = item["dashboard"]
        summary: Dict[str, int] = item["summary"]  # type: ignore[assignment]
        attention = summary.get("disabled", 0) + summary.get("empty", 0)
        status = "🟢" if attention == 0 else ("🟡" if attention < summary.get("total", 0) else "🔴")
        lines.append(
            f"{status} <b>{dashboard.title}</b> — {summary.get('total', 0)} параметров"
        )

    lines.append("")
    lines.append("Дополнительно: поиск, пресеты, экспорт и журнал изменений доступны ниже.")

    return "\n".join(lines)


def _render_search_prompt_text() -> str:
    return dedent(
        """
        🔍 <b>Поиск по настройкам</b>

        Введите часть названия, описания или ключа параметра.
        Можно искать по категориям: «платежи», «уведомления», «рефералы» и т.д.

        Отправьте сообщение с запросом или напишите <code>cancel</code>, чтобы выйти.
        """
    ).strip()


def _render_search_results_text(
    query: str,
    results: List[SettingDefinition],
    limited: List[SettingDefinition],
) -> str:
    safe_query = html.escape(query)
    lines = [
        "🔍 <b>Результаты поиска</b>",
        f"Запрос: <code>{safe_query}</code>",
        "",
    ]

    if not limited:
        lines.append(
            "😕 Ничего не найдено. Попробуйте уточнить запрос или использовать другое слово."
        )
    else:
        for definition in limited:
            status = bot_configuration_service.get_status_emoji(definition.key)
            icon = bot_configuration_service.get_setting_icon(definition.key)
            preview = bot_configuration_service.format_value_display(
                definition.key, short=True
            )
            lines.append(
                f"{status} {icon} <b>{html.escape(definition.display_name)}</b> — {html.escape(preview)}"
            )

        if len(results) > len(limited):
            lines.append("")
            lines.append(
                f"Показаны первые {len(limited)} из {len(results)} совпадений. Уточните запрос, чтобы сократить список."
            )

    lines.append("")
    lines.append("Нажмите на параметр, чтобы перейти к карточке настройки.")
    return "\n".join(lines)


def _build_search_results_keyboard(
    results: List[SettingDefinition],
) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for definition in results:
        dashboard_key, service_key, service_page, settings_page = _locate_setting(
            definition
        )
        token = bot_configuration_service.get_callback_token(definition.key)
        preview = bot_configuration_service.format_value_display(
            definition.key, short=True
        )
        preview = preview.replace("\n", " ")
        label = f"{definition.display_name} · {preview}".strip()
        if len(label) > 64:
            label = label[:63] + "…"
        builder.button(
            text=label,
            callback_data=(
                f"botcfg_setting:{dashboard_key}:{service_page}:{settings_page}:{token}"
            ),
        )

    builder.adjust(1)
    builder.row(
        types.InlineKeyboardButton(text="🔍 Новый поиск", callback_data="botcfg_search")
    )
    builder.row(
        types.InlineKeyboardButton(
            text="🏠 Главное меню", callback_data="admin_bot_config"
        )
    )
    return builder.as_markup()


def _render_presets_overview_text() -> str:
    lines = [
        "🎚 <b>Готовые пресеты настроек</b>",
        "Выберите подходящий набор параметров и примените его одним нажатием.",
        "",
    ]

    for preset in bot_configuration_service.PRESETS:
        lines.append(f"✨ <b>{preset.label}</b>")
        lines.append(f"   {preset.summary}")
        lines.append("")

    if not bot_configuration_service.PRESETS:
        lines.append("Пока нет доступных пресетов. Позже они появятся в обновлениях.")

    lines.append("Нажмите на пресет, чтобы увидеть подробности и список изменяемых настроек.")
    return "\n".join(lines)


def _build_presets_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for preset in bot_configuration_service.PRESETS:
        builder.button(
            text=f"🎚 {preset.label}",
            callback_data=f"botcfg_preset:{preset.key}",
        )

    builder.adjust(1)
    builder.row(
        types.InlineKeyboardButton(
            text="🏠 Главное меню", callback_data="admin_bot_config"
        )
    )
    return builder.as_markup()


def _format_change_value(key: str, value: object) -> str:
    if value is None:
        return "—"
    try:
        return bot_configuration_service.format_value_display(key, value)
    except Exception:
        return str(value)


def _render_preset_detail_text(preset, *, applied: bool = False) -> str:
    lines = [
        "🎚 <b>Пресет настроек</b>",
        f"Название: <b>{preset.label}</b>",
        f"Описание: {preset.description}",
        "",
        preset.summary,
        "",
        "Изменяемые параметры:",
    ]

    if not preset.changes:
        lines.append("⚪ Этот пресет не изменяет параметры.")
    else:
        for key, value in preset.changes.items():
            try:
                definition = bot_configuration_service.get_definition(key)
            except KeyError:
                continue
            icon = bot_configuration_service.get_setting_icon(key)
            current_display = bot_configuration_service.format_value_display(key)
            new_display = _format_change_value(key, value)
            lines.append(
                f"{icon} <b>{definition.display_name}</b>\n   Текущее: <code>{current_display}</code>\n   После пресета: <code>{new_display}</code>"
            )

    if applied:
        lines.append("")
        lines.append("✅ Пресет применён. Настройки обновлены.")

    return "\n".join(lines)


def _build_preset_detail_keyboard(preset_key: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(
            text="✅ Применить", callback_data=f"botcfg_preset_apply:{preset_key}"
        )
    )
    builder.row(
        types.InlineKeyboardButton(
            text="⬅️ К списку", callback_data="botcfg_presets"
        ),
        types.InlineKeyboardButton(
            text="🏠 Главное меню", callback_data="admin_bot_config"
        ),
    )
    return builder.as_markup()


def _render_import_instructions_text() -> str:
    return dedent(
        """
        📥 <b>Импорт настроек</b>

        Пришлите .env файл или вставьте содержимое сообщением. Бот сравнит значения с текущими и покажет изменения перед применением.

        Формат строк: <code>ПАРАМЕТР=значение</code>. Пустое значение или слово <code>none</code> сбросит параметр к дефолту.

        Для отмены напишите <code>cancel</code> или вернитесь в главное меню.
        """
    ).strip()


def _render_import_diff_text(diff: List[Dict[str, object]]) -> str:
    lines = [
        "📥 <b>Импорт настроек</b>",
        f"Будут обновлены {len(diff)} параметров:",
        "",
    ]

    preview = diff[:IMPORT_DIFF_PREVIEW_LIMIT]

    for item in preview:
        key = item["key"]
        try:
            definition = bot_configuration_service.get_definition(key)
        except KeyError:
            continue
        icon = bot_configuration_service.get_setting_icon(key)
        current_display = _format_change_value(key, item.get("old_value"))
        new_raw = item.get("new_value")
        new_display = (
            "— (сброс)" if new_raw is None else _format_change_value(key, new_raw)
        )
        lines.append(
            f"{icon} <b>{definition.display_name}</b>\n   Было: <code>{current_display}</code>\n   Станет: <code>{new_display}</code>"
        )

    if len(diff) > len(preview):
        lines.append("")
        lines.append(
            f"Показаны первые {len(preview)} строк. Всего изменений: {len(diff)}."
        )

    lines.append("")
    lines.append("Проверьте список и подтвердите импорт, чтобы применить значения.")
    return "\n".join(lines)


def _build_import_confirmation_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(
            text="✅ Применить", callback_data="botcfg_import_confirm"
        ),
        types.InlineKeyboardButton(
            text="❌ Отменить", callback_data="botcfg_import_cancel"
        ),
    )
    builder.row(
        types.InlineKeyboardButton(
            text="🏠 Главное меню", callback_data="admin_bot_config"
        )
    )
    return builder.as_markup()


def _render_history_text(changes: Iterable[SystemSettingChange]) -> str:
    lines = [
        "🕑 <b>История изменений настроек</b>",
        "Последние действия администраторов и сервисов.",
        "",
    ]

    has_records = False
    for change in changes:
        has_records = True
        timestamp = change.created_at.strftime("%d.%m %H:%M") if change.created_at else "—"
        key = change.key
        icon = bot_configuration_service.get_setting_icon(key)
        try:
            old_value = bot_configuration_service.deserialize_value(key, change.old_value)
        except Exception:
            old_value = change.old_value
        try:
            new_value = bot_configuration_service.deserialize_value(key, change.new_value)
        except Exception:
            new_value = change.new_value
        old_display = _format_change_value(key, old_value)
        new_display = _format_change_value(key, new_value)
        author = change.changed_by_username or (
            f"ID {change.changed_by}" if change.changed_by else "—"
        )
        lines.append(
            f"{timestamp} · {icon} <code>{key}</code>\n   {old_display} → {new_display}\n   Источник: {change.source or '—'} · Автор: {author}"
        )

    if not has_records:
        lines.append("Журнал изменений пока пуст.")

    lines.append("")
    lines.append("Здесь же можно быстро перейти к пресетам, экспорту и поиску.")
    return "\n".join(lines)


def _build_history_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🏠 Главное меню", callback_data="admin_bot_config"
                )
            ]
        ]
    )


async def _extract_import_content(message: types.Message) -> str | None:
    if message.document:
        buffer = io.BytesIO()
        try:
            await message.document.download(destination=buffer)
        except Exception:
            return None
        try:
            return buffer.getvalue().decode("utf-8")
        except UnicodeDecodeError:
            return None
    if message.text:
        return message.text
    return None


def _parse_group_payload(payload: str) -> Tuple[str, int]:
    parts = payload.split(":")
    group_key = parts[1] if len(parts) > 1 and parts[1] else DEFAULT_DASHBOARD_KEY
    try:
        page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        page = 1
    return group_key, page


def _parse_category_payload(payload: str) -> Tuple[str, str, int, int]:
    parts = payload.split(":")
    group_key = parts[1] if len(parts) > 1 and parts[1] else DEFAULT_DASHBOARD_KEY
    category_key = parts[2] if len(parts) > 2 else ""

    def _safe(value: str, default: int = 1) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    category_page = _safe(parts[3]) if len(parts) > 3 else 1
    settings_page = _safe(parts[4]) if len(parts) > 4 else 1
    return group_key, category_key, category_page, settings_page


def _build_service_categories_keyboard(
    dashboard_key: str,
    service_nodes: List[Dict[str, object]],
    page: int = 1,
) -> types.InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(service_nodes) / CATEGORY_PAGE_SIZE))
    page = max(1, min(page, total_pages))
    start = (page - 1) * CATEGORY_PAGE_SIZE
    end = start + CATEGORY_PAGE_SIZE
    sliced = service_nodes[start:end]

    builder = InlineKeyboardBuilder()
    for node in sliced:
        summary: Dict[str, int] = node["summary"]  # type: ignore[assignment]
        attention = summary.get("disabled", 0) + summary.get("empty", 0)
        status = "🟢" if attention == 0 else ("🟡" if attention < summary.get("total", 0) else "🔴")
        label = node["label"]
        button_text = f"{status} {label} · {summary.get('total', 0)}"
        builder.button(
            text=button_text,
            callback_data=f"botcfg_cat:{dashboard_key}:{node['key']}:{page}:1",
        )

    builder.adjust(1)

    if total_pages > 1:
        nav_builder = InlineKeyboardBuilder()
        if page > 1:
            nav_builder.button(
                text="⬅️",
                callback_data=f"botcfg_group:{dashboard_key}:{page - 1}",
            )
        nav_builder.button(text=f"{page}/{total_pages}", callback_data="botcfg_group:noop")
        if page < total_pages:
            nav_builder.button(
                text="➡️",
                callback_data=f"botcfg_group:{dashboard_key}:{page + 1}",
            )
        builder.row(*nav_builder.buttons)

    builder.row(
        types.InlineKeyboardButton(
            text="🏠 В главное меню",
            callback_data="admin_bot_config",
        )
    )

    return builder.as_markup()


def _render_dashboard_category_text(
    dashboard,
    all_nodes: List[Dict[str, object]],
    page_nodes: List[Dict[str, object]],
) -> str:
    summary: Dict[str, int] = bot_configuration_service.summarize_definitions(
        [definition for node in all_nodes for definition in node.get("definitions", [])]
    )
    lines = [
        f"🏠 <b>Главная</b>{BREADCRUMB_SEPARATOR}{dashboard.title}",
        dashboard.description,
        "",
        (
            f"🟢 Настроено: {summary.get('active', 0)}"
            f" · 🟡 Требует внимания: {summary.get('disabled', 0)}"
            f" · ⚪ Не заполнено: {summary.get('empty', 0)}"
        ),
        "",
        "Доступные группы настроек:",
    ]

    for node in page_nodes:
        node_summary: Dict[str, int] = node["summary"]  # type: ignore[assignment]
        attention = node_summary.get("disabled", 0) + node_summary.get("empty", 0)
        status = "🟢" if attention == 0 else ("🟡" if attention < node_summary.get("total", 0) else "🔴")
        lines.append(
            f"{status} <b>{node['label']}</b> — {node_summary.get('total', 0)} параметров"
        )

    lines.append("")
    lines.append("Выберите группу, чтобы увидеть все параметры и подробные подсказки.")
    return "\n".join(lines)


def _format_setting_list_item(definition: SettingDefinition) -> str:
    icon = bot_configuration_service.get_setting_icon(definition.key)
    status = bot_configuration_service.get_status_emoji(definition.key)
    value = bot_configuration_service.format_value_display(definition.key, short=True)
    override_flag = (
        " (переопределено)" if bot_configuration_service.has_override(definition.key) else ""
    )
    return (
        f"{status} {icon} <b>{definition.display_name}</b>{override_flag}\n"
        f"   Текущее: <code>{value}</code>"
    )


def _locate_setting(definition: SettingDefinition) -> Tuple[str, str, int, int]:
    structure = _collect_dashboard_structure()
    for item in structure:
        dashboard = item["dashboard"]
        service_nodes: List[Dict[str, object]] = item.get("service_nodes", [])  # type: ignore[assignment]
        for index, node in enumerate(service_nodes):
            node_definitions: List[SettingDefinition] = node.get("definitions", [])  # type: ignore[assignment]
            for def_index, current in enumerate(node_definitions):
                if current.key == definition.key:
                    service_page = index // CATEGORY_PAGE_SIZE + 1
                    settings_page = def_index // SETTINGS_PAGE_SIZE + 1
                    return dashboard.key, node["key"], service_page, settings_page
    return DEFAULT_DASHBOARD_KEY, definition.category_key, 1, 1


def _render_service_category_text(
    dashboard,
    service_key: str,
    service_label: str,
    definitions: List[SettingDefinition],
    page_definitions: List[SettingDefinition],
    page: int,
    total_pages: int,
) -> str:
    summary = bot_configuration_service.summarize_definitions(definitions)
    description = bot_configuration_service.get_category_description(service_key)
    lines = [
        f"🏠 <b>Главная</b>{BREADCRUMB_SEPARATOR}{dashboard.title}{BREADCRUMB_SEPARATOR}{service_label}",
        description,
        "",
        (
            f"🟢 Настроено: {summary.get('active', 0)}"
            f" · 🟡 Требует внимания: {summary.get('disabled', 0)}"
            f" · ⚪ Не заполнено: {summary.get('empty', 0)}"
        ),
        "",
        "Настройки:",
    ]

    if not page_definitions:
        lines.append("⚪ В этой группе пока нет параметров")
    else:
        for definition in page_definitions:
            lines.append(_format_setting_list_item(definition))

    if total_pages > 1:
        lines.append("")
        lines.append(f"Страница {page}/{total_pages}")

    lines.append("")
    lines.append("Нажмите на параметр, чтобы открыть подробную карточку и изменить значение.")
    return "\n".join(lines)


async def _store_setting_context(
    state: FSMContext,
    *,
    key: str,
    group_key: str,
    category_page: int,
    settings_page: int,
    service_key: str | None = None,
) -> None:
    await state.update_data(
        setting_key=key,
        setting_group_key=group_key,
        setting_category_page=category_page,
        setting_settings_page=settings_page,
        setting_service_key=service_key,
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


def _build_settings_keyboard(
    dashboard_key: str,
    service_key: str,
    service_page: int,
    definitions: List[SettingDefinition],
    language: str,
    page: int = 1,
) -> types.InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(definitions) / SETTINGS_PAGE_SIZE))
    page = max(1, min(page, total_pages))
    start = (page - 1) * SETTINGS_PAGE_SIZE
    end = start + SETTINGS_PAGE_SIZE
    sliced = definitions[start:end]

    builder = InlineKeyboardBuilder()
    texts = get_texts(language)

    if service_key == "REMNAWAVE":
        builder.row(
            types.InlineKeyboardButton(
                text="🔌 Проверить подключение",
                callback_data=(
                    f"botcfg_test_remnawave:{dashboard_key}:{service_key}:{service_page}:{page}"
                ),
            )
        )

    def _test_button(text: str, method: str) -> types.InlineKeyboardButton:
        return types.InlineKeyboardButton(
            text=text,
            callback_data=(
                f"botcfg_test_payment:{method}:{dashboard_key}:{service_key}:{service_page}:{page}"
            ),
        )

    if service_key == "YOOKASSA":
        label = texts.t("PAYMENT_CARD_YOOKASSA", "💳 Банковская карта (YooKassa)")
        builder.row(_test_button(f"{label} · тест", "yookassa"))
    elif service_key == "TRIBUTE":
        label = texts.t("PAYMENT_CARD_TRIBUTE", "💳 Банковская карта (Tribute)")
        builder.row(_test_button(f"{label} · тест", "tribute"))
    elif service_key == "MULENPAY":
        label = texts.t("PAYMENT_CARD_MULENPAY", "💳 Банковская карта (Mulen Pay)")
        builder.row(_test_button(f"{label} · тест", "mulenpay"))
    elif service_key == "PAL24":
        label = texts.t("PAYMENT_CARD_PAL24", "💳 Банковская карта (PayPalych)")
        builder.row(_test_button(f"{label} · тест", "pal24"))
    elif service_key == "TELEGRAM":
        label = texts.t("PAYMENT_TELEGRAM_STARS", "⭐ Telegram Stars")
        builder.row(_test_button(f"{label} · тест", "stars"))
    elif service_key == "CRYPTOBOT":
        label = texts.t("PAYMENT_CRYPTOBOT", "🪙 Криптовалюта (CryptoBot)")
        builder.row(_test_button(f"{label} · тест", "cryptobot"))

    for definition in sliced:
        icon = bot_configuration_service.get_setting_icon(definition.key)
        status = bot_configuration_service.get_status_emoji(definition.key)
        value_preview = bot_configuration_service.format_value_display(
            definition.key, short=True
        )
        button_text = f"{status} {icon} {definition.display_name} · {value_preview}".strip()
        if len(button_text) > 64:
            button_text = button_text[:63] + "…"
        callback_token = bot_configuration_service.get_callback_token(definition.key)
        builder.row(
            types.InlineKeyboardButton(
                text=button_text,
                callback_data=(
                    f"botcfg_setting:{dashboard_key}:{service_page}:{page}:{callback_token}"
                ),
            )
        )

    if total_pages > 1:
        nav_builder = InlineKeyboardBuilder()
        if page > 1:
            nav_builder.button(
                text="⬅️",
                callback_data=(
                    f"botcfg_cat:{dashboard_key}:{service_key}:{service_page}:{page - 1}"
                ),
            )
        nav_builder.button(
            text=f"{page}/{total_pages}", callback_data="botcfg_cat_page:noop"
        )
        if page < total_pages:
            nav_builder.button(
                text="➡️",
                callback_data=(
                    f"botcfg_cat:{dashboard_key}:{service_key}:{service_page}:{page + 1}"
                ),
            )
        builder.row(*nav_builder.buttons)

    builder.row(
        types.InlineKeyboardButton(
            text="⬅️ К разделу",
            callback_data=f"botcfg_group:{dashboard_key}:{service_page}",
        ),
        types.InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data="admin_bot_config",
        ),
    )

    return builder.as_markup()


def _build_setting_keyboard(
    key: str,
    dashboard_key: str,
    service_key: str,
    category_page: int,
    settings_page: int,
) -> types.InlineKeyboardMarkup:
    definition = bot_configuration_service.get_definition(key)
    callback_token = bot_configuration_service.get_callback_token(key)
    builder = InlineKeyboardBuilder()
    input_type = bot_configuration_service.get_input_type(key)

    choice_options = bot_configuration_service.get_choice_options(key)
    if choice_options:
        current_value = bot_configuration_service.get_current_value(key)
        for option in choice_options:
            choice_token = bot_configuration_service.get_choice_token(key, option.value)
            if choice_token is None:
                continue
            is_current = current_value == option.value
            button_text = option.label
            if is_current and not button_text.startswith("✅"):
                button_text = f"✅ {button_text}"
            builder.button(
                text=button_text,
                callback_data=(
                    f"botcfg_choice:{dashboard_key}:{category_page}:{settings_page}:{callback_token}:{choice_token}"
                ),
            )
        builder.adjust(2)

    if input_type == SettingInputType.TOGGLE:
        builder.row(
            types.InlineKeyboardButton(
                text="✅ Включить",
                callback_data=(
                    f"botcfg_toggle:{dashboard_key}:{category_page}:{settings_page}:{callback_token}:1"
                ),
            ),
            types.InlineKeyboardButton(
                text="❌ Выключить",
                callback_data=(
                    f"botcfg_toggle:{dashboard_key}:{category_page}:{settings_page}:{callback_token}:0"
                ),
            ),
        )

    edit_label = "✏️ Изменить"
    if input_type == SettingInputType.PRICE:
        edit_label = "💵 Изменить цену"
    elif input_type == SettingInputType.TIME:
        edit_label = "⏱️ Указать время"
    elif input_type == SettingInputType.LIST:
        edit_label = "📝 Задать список"

    builder.row(
        types.InlineKeyboardButton(
            text=edit_label,
            callback_data=(
                f"botcfg_edit:{dashboard_key}:{category_page}:{settings_page}:{callback_token}"
            ),
        )
    )

    if bot_configuration_service.has_override(key):
        builder.row(
            types.InlineKeyboardButton(
                text="♻️ Сбросить",
                callback_data=(
                    f"botcfg_reset:{dashboard_key}:{category_page}:{settings_page}:{callback_token}"
                ),
            )
        )

    builder.row(
        types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=(
                f"botcfg_cat:{dashboard_key}:{service_key}:{category_page}:{settings_page}"
            ),
        )
    )

    return builder.as_markup()


def _render_setting_text(key: str) -> str:
    summary = bot_configuration_service.get_setting_summary(key)
    meta = bot_configuration_service.get_setting_meta(key)
    status = bot_configuration_service.get_status_emoji(key)
    icon = meta.icon or bot_configuration_service.get_setting_icon(key)
    input_type = bot_configuration_service.get_input_type(key)

    lines = [
        f"{status} {icon} <b>{summary['name']}</b>",
        f"Категория: <b>{summary['category_label']}</b>",
        f"Ключ: <code>{summary['key']}</code>",
        f"Тип ввода: <code>{input_type.value}</code>",
        f"Текущее значение: <code>{summary['current']}</code>",
        f"Значение по умолчанию: <code>{summary['original']}</code>",
        f"Переопределено в БД: {'✅ Да' if summary['has_override'] else '⚪ Нет'}",
    ]

    if meta.description:
        lines.extend(["", f"ℹ️ {meta.description}"])

    if meta.format_hint:
        lines.append(f"📝 Формат: {meta.format_hint}")

    if meta.example:
        example_value = meta.example
        if meta.unit:
            example_value = f"{example_value} {meta.unit}"
        lines.append(f"📌 Пример: <code>{example_value}</code>")

    if meta.recommended:
        lines.append(f"✅ Рекомендуемое значение: {meta.recommended}")

    if meta.warning:
        lines.append(f"⚠️ {meta.warning}")

    if meta.dependencies:
        deps = ", ".join(f"<code>{dep}</code>" for dep in meta.dependencies)
        lines.append(f"🔗 Связанные параметры: {deps}")

    choices = bot_configuration_service.get_choice_options(key)
    if choices:
        current_raw = bot_configuration_service.get_current_value(key)
        lines.append("")
        lines.append("<b>Доступные значения:</b>")
        for option in choices:
            marker = "✅" if current_raw == option.value else "•"
            value_display = bot_configuration_service.format_value(option.value)
            description = option.description or ""
            if description:
                lines.append(
                    f"{marker} {option.label} — <code>{value_display}</code>\n   {description}"
                )
            else:
                lines.append(f"{marker} {option.label} — <code>{value_display}</code>")

    lines.append("")
    lines.append("Используйте кнопки ниже, чтобы изменить значение, сбросить или получить помощь.")

    return "\n".join(lines)


@admin_required
@error_handler
async def show_bot_config_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    structure = _collect_dashboard_structure()
    keyboard = _build_main_menu_keyboard(structure)
    text = _render_main_menu_text(structure)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def start_search_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    await state.set_state(BotConfigStates.waiting_for_search_query)
    await callback.message.edit_text(
        _render_search_prompt_text(),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="🏠 Главное меню", callback_data="admin_bot_config"
                    )
                ]
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


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

    if query.lower() in {"cancel", "отмена"}:
        await state.clear()
        await message.answer("Поиск отменён. Используйте меню, чтобы продолжить.")
        return

    results = bot_configuration_service.search_settings(query)
    limited = results[:MAX_SEARCH_RESULTS]
    text = _render_search_results_text(query, results, limited)
    keyboard = _build_search_results_keyboard(limited)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@admin_required
@error_handler
async def show_presets(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    text = _render_presets_overview_text()
    keyboard = _build_presets_keyboard()
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def show_preset_detail(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split(":", 1)
    preset_key = parts[1] if len(parts) > 1 else ""
    preset = next(
        (item for item in bot_configuration_service.PRESETS if item.key == preset_key),
        None,
    )
    if preset is None:
        await callback.answer("Этот пресет недоступен", show_alert=True)
        return

    text = _render_preset_detail_text(preset)
    keyboard = _build_preset_detail_keyboard(preset.key)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def apply_preset_changes(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split(":", 1)
    preset_key = parts[1] if len(parts) > 1 else ""
    try:
        preset = await bot_configuration_service.apply_preset(
            db,
            preset_key,
            changed_by=db_user.id,
            changed_by_username=getattr(db_user, "username", None),
        )
    except KeyError:
        await callback.answer("Не удалось применить пресет", show_alert=True)
        return

    await db.commit()
    text = _render_preset_detail_text(preset, applied=True)
    keyboard = _build_preset_detail_keyboard(preset.key)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer("Пресет применён")


@admin_required
@error_handler
async def export_settings_snapshot(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    content = bot_configuration_service.generate_env_snapshot()
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    filename = f"remnawave-settings-{timestamp}.env"
    file = types.BufferedInputFile(content.encode("utf-8"), filename)
    await callback.message.answer_document(
        file,
        caption="📤 Экспорт настроек: сохраните файл как резервную копию.",
    )
    await callback.answer("Файл сформирован")


@admin_required
@error_handler
async def start_import_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    await state.set_state(BotConfigStates.waiting_for_import_content)
    await callback.message.edit_text(
        _render_import_instructions_text(),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="🏠 Главное меню", callback_data="admin_bot_config"
                    )
                ]
            ]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_import_message(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    if message.text and message.text.strip().lower() in {"cancel", "отмена"}:
        await state.clear()
        await message.answer("Импорт отменён.")
        return

    content = await _extract_import_content(message)
    if content is None:
        await message.answer(
            "Не удалось прочитать файл. Отправьте .env текстом или файлом в кодировке UTF-8."
        )
        return

    parsed = bot_configuration_service.parse_env_content(content)
    if not parsed:
        await message.answer("Не найдено корректных строк формата KEY=VALUE.")
        return

    diff = bot_configuration_service.build_import_diff(parsed)
    if not diff:
        await message.answer("Все значения уже совпадают. Изменений нет.")
        await state.clear()
        return

    await state.update_data(import_data=parsed)
    text = _render_import_diff_text(diff)
    keyboard = _build_import_confirmation_keyboard()
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@admin_required
@error_handler
async def confirm_import_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()
    payload = data.get("import_data")
    if not payload:
        await callback.answer("Нет подготовленных данных для импорта", show_alert=True)
        return

    diff = bot_configuration_service.build_import_diff(payload)
    if not diff:
        await state.clear()
        await callback.answer("Изменений нет", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    await bot_configuration_service.apply_import_diff(
        db,
        diff,
        changed_by=db_user.id,
        changed_by_username=getattr(db_user, "username", None),
        source="import",
    )
    await db.commit()
    await state.clear()
    await callback.message.edit_text(
        "✅ Импорт завершён. Настройки обновлены.",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="🏠 Главное меню", callback_data="admin_bot_config"
                    )
                ]
            ]
        ),
    )
    await callback.answer("Изменения применены")


@admin_required
@error_handler
async def cancel_import_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    await callback.message.edit_text(
        "Импорт отменён.",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="🏠 Главное меню", callback_data="admin_bot_config"
                    )
                ]
            ]
        ),
    )
    await callback.answer("Отменено")


@admin_required
@error_handler
async def show_history_changes(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    changes = await bot_configuration_service.get_recent_changes(db)
    text = _render_history_text(changes)
    keyboard = _build_history_keyboard()
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def show_bot_config_group(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    group_key, page = _parse_group_payload(callback.data)
    structure = _collect_dashboard_structure()
    entry = next(
        (item for item in structure if item["dashboard"].key == group_key),
        None,
    )

    if entry is None:
        await callback.answer("Раздел недоступен", show_alert=True)
        return

    service_nodes: List[Dict[str, object]] = entry.get("service_nodes", [])  # type: ignore[assignment]
    if not service_nodes:
        await callback.answer("В этом разделе пока нет настроек", show_alert=True)
        return

    total_pages = max(1, math.ceil(len(service_nodes) / CATEGORY_PAGE_SIZE))
    page = max(1, min(page, total_pages))
    start = (page - 1) * CATEGORY_PAGE_SIZE
    end = start + CATEGORY_PAGE_SIZE
    page_nodes = service_nodes[start:end]

    keyboard = _build_service_categories_keyboard(group_key, service_nodes, page)
    text = _render_dashboard_category_text(entry["dashboard"], service_nodes, page_nodes)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def show_bot_config_category(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    dashboard_key, service_key, service_page, settings_page = _parse_category_payload(
        callback.data
    )
    definitions = bot_configuration_service.get_settings_for_category(service_key)

    if not definitions:
        await callback.answer("В этой группе пока нет настроек", show_alert=True)
        return

    dashboard = bot_configuration_service.get_dashboard_category(dashboard_key)
    service_label = definitions[0].category_label
    total_pages = max(1, math.ceil(len(definitions) / SETTINGS_PAGE_SIZE))
    settings_page = max(1, min(settings_page, total_pages))
    start = (settings_page - 1) * SETTINGS_PAGE_SIZE
    end = start + SETTINGS_PAGE_SIZE
    page_definitions = definitions[start:end]
    language = db_user.language or "ru"

    keyboard = _build_settings_keyboard(
        dashboard_key,
        service_key,
        service_page,
        definitions,
        language,
        settings_page,
    )
    text = _render_service_category_text(
        dashboard,
        service_key,
        service_label,
        definitions,
        page_definitions,
        settings_page,
        total_pages,
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def test_remnawave_connection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split(":", 5)
    dashboard_key = parts[1] if len(parts) > 1 else DEFAULT_DASHBOARD_KEY
    category_key = parts[2] if len(parts) > 2 else "REMNAWAVE"

    try:
        category_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        category_page = 1

    try:
        settings_page = max(1, int(parts[4])) if len(parts) > 4 else 1
    except ValueError:
        settings_page = 1

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

    definitions = bot_configuration_service.get_settings_for_category(category_key)
    if definitions:
        keyboard = _build_settings_keyboard(
            dashboard_key,
            category_key,
            category_page,
            definitions,
            db_user.language or "ru",
            settings_page,
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=keyboard)
        except Exception:
            # ignore inability to refresh markup, main result shown in alert
            pass

    await callback.answer(message, show_alert=True)


@admin_required
@error_handler
async def test_payment_provider(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split(":", 6)
    method = parts[1] if len(parts) > 1 else ""
    dashboard_key = parts[2] if len(parts) > 2 else DEFAULT_DASHBOARD_KEY
    category_key = parts[3] if len(parts) > 3 else "PAYMENT"

    try:
        category_page = max(1, int(parts[4])) if len(parts) > 4 else 1
    except ValueError:
        category_page = 1

    try:
        settings_page = max(1, int(parts[5])) if len(parts) > 5 else 1
    except ValueError:
        settings_page = 1

    language = db_user.language or "ru"
    texts = get_texts(language)
    payment_service = PaymentService(callback.bot)

    message_text: str

    async def _refresh_markup() -> None:
        definitions = bot_configuration_service.get_settings_for_category(category_key)
        if definitions:
            keyboard = _build_settings_keyboard(
                dashboard_key,
                category_key,
                category_page,
                definitions,
                language,
                settings_page,
            )
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
    group_key = parts[1] if len(parts) > 1 else DEFAULT_DASHBOARD_KEY
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
    service_key = definition.category_key
    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(
        key,
        group_key,
        service_key,
        category_page,
        settings_page,
    )
    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
        service_key=service_key,
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
    group_key = parts[1] if len(parts) > 1 else DEFAULT_DASHBOARD_KEY
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
    texts = get_texts(db_user.language or "ru")

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
        parse_mode="HTML",
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
    group_key = data.get("setting_group_key", DEFAULT_DASHBOARD_KEY)
    category_page = int(data.get("setting_category_page", 1) or 1)
    settings_page = int(data.get("setting_settings_page", 1) or 1)
    service_key = data.get("setting_service_key")

    if not key:
        await message.answer("Не удалось определить редактируемую настройку. Попробуйте снова.")
        await state.clear()
        return

    try:
        value = bot_configuration_service.parse_user_value(key, message.text or "")
    except ValueError as error:
        await message.answer(f"⚠️ {error}")
        return

    await bot_configuration_service.set_value(
        db,
        key,
        value,
        changed_by=db_user.id,
        changed_by_username=getattr(db_user, "username", None),
        source="bot_config",
        reason="manual_edit",
    )
    await db.commit()

    if not service_key:
        service_key = bot_configuration_service.get_definition(key).category_key
    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(
        key,
        group_key,
        service_key,
        category_page,
        settings_page,
    )
    await message.answer("✅ Настройка обновлена")
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await state.clear()
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
        service_key=service_key,
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
    group_key = data.get("setting_group_key", DEFAULT_DASHBOARD_KEY)
    category_page = int(data.get("setting_category_page", 1) or 1)
    settings_page = int(data.get("setting_settings_page", 1) or 1)
    service_key = data.get("setting_service_key")

    if not key:
        return

    try:
        value = bot_configuration_service.parse_user_value(key, message.text or "")
    except ValueError as error:
        await message.answer(f"⚠️ {error}")
        return

    await bot_configuration_service.set_value(
        db,
        key,
        value,
        changed_by=db_user.id,
        changed_by_username=getattr(db_user, "username", None),
        source="bot_config",
        reason="manual_edit",
    )
    await db.commit()

    if not service_key:
        service_key = bot_configuration_service.get_definition(key).category_key
    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(
        key,
        group_key,
        service_key,
        category_page,
        settings_page,
    )
    await message.answer("✅ Настройка обновлена")
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    await state.clear()
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
        service_key=service_key,
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
    dashboard_key = parts[1] if len(parts) > 1 else DEFAULT_DASHBOARD_KEY
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
    await bot_configuration_service.reset_value(
        db,
        key,
        changed_by=db_user.id,
        changed_by_username=getattr(db_user, "username", None),
        source="bot_config",
        reason="manual_reset",
    )
    await db.commit()

    service_key = definition.category_key
    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(
        key,
        dashboard_key,
        service_key,
        category_page,
        settings_page,
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await _store_setting_context(
        state,
        key=key,
        group_key=dashboard_key,
        category_page=category_page,
        settings_page=settings_page,
        service_key=service_key,
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
    parts = callback.data.split(":", 5)
    dashboard_key = parts[1] if len(parts) > 1 else DEFAULT_DASHBOARD_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    desired_raw = parts[5] if len(parts) > 5 else None
    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return
    current = bot_configuration_service.get_current_value(key)
    if desired_raw:
        lowered = desired_raw.lower()
        if lowered in {"1", "true", "on", "yes", "enable"}:
            new_value = True
        elif lowered in {"0", "false", "off", "no", "disable"}:
            new_value = False
        else:
            new_value = not bool(current)
    else:
        new_value = not bool(current)
    definition = bot_configuration_service.get_definition(key)
    await bot_configuration_service.set_value(
        db,
        key,
        new_value,
        changed_by=db_user.id,
        changed_by_username=getattr(db_user, "username", None),
        source="bot_config",
        reason="toggle",
    )
    await db.commit()

    service_key = definition.category_key
    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(
        key,
        dashboard_key,
        service_key,
        category_page,
        settings_page,
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await _store_setting_context(
        state,
        key=key,
        group_key=dashboard_key,
        category_page=category_page,
        settings_page=settings_page,
        service_key=service_key,
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
    dashboard_key = parts[1] if len(parts) > 1 else DEFAULT_DASHBOARD_KEY
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

    definition = bot_configuration_service.get_definition(key)
    await bot_configuration_service.set_value(
        db,
        key,
        value,
        changed_by=db_user.id,
        changed_by_username=getattr(db_user, "username", None),
        source="bot_config",
        reason="choice",
    )
    await db.commit()

    service_key = definition.category_key
    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(
        key,
        dashboard_key,
        service_key,
        category_page,
        settings_page,
    )
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await _store_setting_context(
        state,
        key=key,
        group_key=dashboard_key,
        category_page=category_page,
        settings_page=settings_page,
        service_key=service_key,
    )
    await callback.answer("Значение обновлено")


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        show_bot_config_menu,
        F.data == "admin_bot_config",
    )
    dp.callback_query.register(
        start_search_settings,
        F.data == "botcfg_search",
    )
    dp.message.register(
        handle_search_query,
        BotConfigStates.waiting_for_search_query,
        F.text,
    )
    dp.callback_query.register(
        apply_preset_changes,
        F.data.startswith("botcfg_preset_apply:"),
    )
    dp.callback_query.register(
        show_preset_detail,
        F.data.startswith("botcfg_preset:"),
    )
    dp.callback_query.register(
        show_presets,
        F.data == "botcfg_presets",
    )
    dp.callback_query.register(
        export_settings_snapshot,
        F.data == "botcfg_export",
    )
    dp.callback_query.register(
        start_import_settings,
        F.data == "botcfg_import",
    )
    dp.callback_query.register(
        confirm_import_settings,
        F.data == "botcfg_import_confirm",
    )
    dp.callback_query.register(
        cancel_import_settings,
        F.data == "botcfg_import_cancel",
    )
    dp.callback_query.register(
        show_history_changes,
        F.data == "botcfg_history",
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
    dp.message.register(
        handle_import_message,
        BotConfigStates.waiting_for_import_content,
        F.document,
    )
    dp.message.register(
        handle_import_message,
        BotConfigStates.waiting_for_import_content,
        F.text,
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

