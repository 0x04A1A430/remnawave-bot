from __future__ import annotations

import html
import logging
import re
from typing import Dict, List, Optional, Sequence, Tuple

from aiogram import Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.discount_offer import upsert_discount_offer
from app.database.crud.server_squad import (
    get_all_server_squads,
    get_server_squad_by_id,
    get_server_squad_by_uuid,
)
from app.database.crud.promo_offer_template import (
    ensure_default_templates,
    get_promo_offer_template_by_id,
    list_promo_offer_templates,
    update_promo_offer_template,
)
from app.database.crud.promo_offer_log import list_promo_offer_logs
from app.database.crud.user import get_users_for_promo_segment
from app.database.models import PromoOfferLog, PromoOfferTemplate, User
from app.keyboards.inline import get_happ_download_button_row
from app.localization.texts import get_texts
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler
from app.utils.subscription_utils import get_display_subscription_link

logger = logging.getLogger(__name__)


SQUADS_PAGE_LIMIT = 10
PROMO_OFFER_LOGS_PAGE_LIMIT = 10


async def _safe_delete_message(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest as exc:
        if "message to delete not found" not in str(exc).lower():
            logger.debug("Не удалось удалить сообщение администратора: %s", exc)
    except TelegramForbiddenError:
        logger.debug("Недостаточно прав для удаления сообщения администратора")


ACTION_LABEL_KEYS = {
    "claimed": "ADMIN_PROMO_OFFER_LOGS_ACTION_CLAIMED",
    "consumed": "ADMIN_PROMO_OFFER_LOGS_ACTION_CONSUMED",
    "disabled": "ADMIN_PROMO_OFFER_LOGS_ACTION_DISABLED",
}


REASON_LABEL_KEYS = {
    "manual_charge": "ADMIN_PROMO_OFFER_LOGS_REASON_MANUAL",
    "autopay_consumed": "ADMIN_PROMO_OFFER_LOGS_REASON_AUTOPAY",
    "offer_expired": "ADMIN_PROMO_OFFER_LOGS_REASON_EXPIRED",
    "test_access_expired": "ADMIN_PROMO_OFFER_LOGS_REASON_TEST_EXPIRED",
}


OFFER_TYPE_CONFIG = {
    "test_access": {
        "icon": "🧪",
        "label_key": "ADMIN_PROMO_OFFER_TEST_ACCESS",
        "default_label": "Тестовые сервера",
        "allowed_segments": [
            ("paid_active", "🟢 Активные платные"),
            ("trial_active", "🎁 Активные триалы"),
        ],
        "effect_type": "test_access",
    },
    "extend_discount": {
        "icon": "💎",
        "label_key": "ADMIN_PROMO_OFFER_EXTEND",
        "default_label": "Скидка на продление",
        "allowed_segments": [
            ("paid_active", "🟢 Активные платные"),
        ],
        "effect_type": "percent_discount",
    },
    "purchase_discount": {
        "icon": "🎯",
        "label_key": "ADMIN_PROMO_OFFER_PURCHASE",
        "default_label": "Скидка на покупку",
        "allowed_segments": [
            ("paid_expired", "🔴 Истёкшие платные"),
            ("trial_expired", "🥶 Истёкшие триалы"),
            ("trial_active", "🎁 Активные триалы"),
        ],
        "effect_type": "percent_discount",
    },
}

def _render_template_text(
    template: PromoOfferTemplate,
    language: str,
    *,
    server_name: Optional[str] = None,
) -> str:
    replacements = {
        "discount_percent": template.discount_percent,
        "valid_hours": template.valid_hours,
        "test_duration_hours": template.test_duration_hours or 0,
        "active_discount_hours": template.active_discount_hours or template.valid_hours,
    }

    if server_name is not None:
        replacements.setdefault("server_name", server_name)
    else:
        # Prevent KeyError if template expects server_name
        replacements.setdefault("server_name", "???")
    try:
        return template.message_text.format(**replacements)
    except Exception:  # pragma: no cover - fallback for invalid placeholders
        logger.warning("Не удалось форматировать текст промо-предложения %s", template.id)
        return template.message_text


async def _resolve_template_squad(
    db: AsyncSession,
    template: PromoOfferTemplate,
) -> Tuple[Optional[str], Optional[str]]:
    if template.offer_type != "test_access":
        return None, None

    squads = template.test_squad_uuids or []
    if not squads:
        return None, None

    squad_uuid = str(squads[0])
    server = await get_server_squad_by_uuid(db, squad_uuid)
    server_name = server.display_name if server else None
    return squad_uuid, server_name


def _build_templates_keyboard(templates: Sequence[PromoOfferTemplate], language: str) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    rows: List[List[InlineKeyboardButton]] = []
    for template in templates:
        config = OFFER_TYPE_CONFIG.get(template.offer_type, {})
        icon = config.get("icon", "📨")
        label = texts.t(config.get("label_key", ""), config.get("default_label", template.offer_type))
        rows.append([
            InlineKeyboardButton(
                text=f"{icon} {label}",
                callback_data=f"promo_offer_{template.id}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text=texts.t("ADMIN_PROMO_OFFER_LOGS", "📜 Лог операций"),
            callback_data="promo_offer_logs_page_1",
        )
    ])
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data="admin_submenu_communications")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_offer_detail_keyboard(template: PromoOfferTemplate, language: str) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    config = OFFER_TYPE_CONFIG.get(template.offer_type, {})
    rows: List[List[InlineKeyboardButton]] = []

    rows.append([
        InlineKeyboardButton(text="✏️ Текст", callback_data=f"promo_offer_edit_message_{template.id}"),
        InlineKeyboardButton(text="🪄 Кнопка", callback_data=f"promo_offer_edit_button_{template.id}"),
    ])
    rows.append([
        InlineKeyboardButton(text="⏱️ Срок", callback_data=f"promo_offer_edit_valid_{template.id}"),
    ])

    if template.offer_type != "test_access":
        rows[-1].append(InlineKeyboardButton(text="📉 %", callback_data=f"promo_offer_edit_discount_{template.id}"))
        rows.append([
            InlineKeyboardButton(text="⌛ Активна", callback_data=f"promo_offer_edit_active_{template.id}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="⏳ Длительность", callback_data=f"promo_offer_edit_duration_{template.id}"),
            InlineKeyboardButton(text="🌍 Сквады", callback_data=f"promo_offer_edit_squads_{template.id}"),
        ])

    rows.append([
        InlineKeyboardButton(text="📬 Отправить", callback_data=f"promo_offer_send_menu_{template.id}"),
    ])
    rows.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="admin_promo_offers"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_promo_offer_log_entry(
    entry: PromoOfferLog,
    index: int,
    texts,
) -> str:
    timestamp = entry.created_at.strftime("%d.%m.%Y %H:%M") if entry.created_at else "-"
    action_key = ACTION_LABEL_KEYS.get(entry.action, "")
    action_label = texts.get(action_key, entry.action.title())
    lines = [f"{index}. <b>{timestamp}</b> — {action_label}"]

    user = entry.user
    if user:
        username = f"@{user.username}" if user.username else f"ID{user.telegram_id}"
        label = f"{username} (#{user.id})"
    elif entry.user_id:
        label = f"ID{entry.user_id}"
    else:
        label = texts.get("ADMIN_PROMO_OFFER_LOGS_UNKNOWN_USER", "Неизвестный пользователь")

    lines.append(texts.get("ADMIN_PROMO_OFFER_LOGS_USER", "👤 {user}").format(user=html.escape(label)))

    if entry.percent:
        lines.append(
            texts.get("ADMIN_PROMO_OFFER_LOGS_PERCENT", "📉 Скидка: {percent}%").format(
                percent=entry.percent
            )
        )

    effect_type = (entry.effect_type or "").lower()
    if effect_type:
        if effect_type == "test_access":
            effect_label = texts.get("ADMIN_PROMO_OFFER_LOGS_EFFECT_TEST", "🧪 Тестовый доступ")
        else:
            effect_label = texts.get("ADMIN_PROMO_OFFER_LOGS_EFFECT_DISCOUNT", "💸 Скидка")
        lines.append(effect_label)

    if entry.source:
        lines.append(
            texts.get("ADMIN_PROMO_OFFER_LOGS_SOURCE", "🏷 Источник: {source}").format(
                source=html.escape(entry.source)
            )
        )

    details: Dict[str, object] = entry.details if isinstance(entry.details, dict) else {}
    reason_key = details.get("reason")
    if reason_key:
        reason_label = texts.get(REASON_LABEL_KEYS.get(reason_key, ""), "")
        if not reason_label:
            reason_label = texts.get(
                "ADMIN_PROMO_OFFER_LOGS_REASON_GENERIC",
                "ℹ️ Действие: {reason}",
            ).format(reason=html.escape(str(reason_key)))
        lines.append(reason_label)

    description = details.get("description")
    if description:
        lines.append(
            texts.get("ADMIN_PROMO_OFFER_LOGS_DESCRIPTION", "📝 {description}").format(
                description=html.escape(str(description))
            )
        )

    amount = details.get("amount_kopeks")
    if isinstance(amount, int):
        lines.append(
            texts.get("ADMIN_PROMO_OFFER_LOGS_AMOUNT", "💰 Сумма: {amount}").format(
                amount=texts.format_price(amount)
            )
        )

    squad_uuid = details.get("squad_uuid")
    if squad_uuid:
        lines.append(
            texts.get("ADMIN_PROMO_OFFER_LOGS_SQUAD", "🌍 Сквад: {squad}").format(
                squad=html.escape(str(squad_uuid))
            )
        )

    new_squads = details.get("new_squads")
    if isinstance(new_squads, (list, tuple)):
        filtered = [html.escape(str(item)) for item in new_squads if item]
        if filtered:
            lines.append(
                texts.get("ADMIN_PROMO_OFFER_LOGS_NEW_SQUADS", "🌍 Новые сквады: {squads}").format(
                    squads=", ".join(filtered)
                )
            )

    return "\n".join(lines)


def _build_logs_keyboard(page: int, total_pages: int, language: str) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    rows: List[List[InlineKeyboardButton]] = []
    if total_pages > 1:
        nav_row: List[InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"promo_offer_logs_page_{page - 1}",
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=f"promo_offer_logs_page_{page}",
            )
        )
        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"promo_offer_logs_page_{page + 1}",
                )
            )
        rows.append(nav_row)

    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data="admin_promo_offers")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_send_keyboard(template: PromoOfferTemplate, language: str) -> InlineKeyboardMarkup:
    config = OFFER_TYPE_CONFIG.get(template.offer_type, {})
    segments = config.get("allowed_segments", [])
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"promo_offer_send_{template.id}_{segment}",
            )
        ]
        for segment, label in segments
    ]
    texts = get_texts(language)
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data=f"promo_offer_{template.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _describe_offer(
    template: PromoOfferTemplate,
    language: str,
    *,
    server_name: Optional[str] = None,
    server_uuid: Optional[str] = None,
) -> str:
    texts = get_texts(language)
    config = OFFER_TYPE_CONFIG.get(template.offer_type, {})
    label = texts.t(config.get("label_key", ""), config.get("default_label", template.offer_type))
    icon = config.get("icon", "📨")

    lines = [f"{icon} <b>{template.name}</b>", ""]
    lines.append(texts.t("ADMIN_PROMO_OFFER_TYPE", "Тип: {label}").format(label=label))
    lines.append(texts.t("ADMIN_PROMO_OFFER_VALID", "Срок действия: {hours} ч").format(hours=template.valid_hours))

    if template.offer_type != "test_access":
        lines.append(
            texts.t(
                "ADMIN_PROMO_OFFER_DISCOUNT",
                "Доп. скидка: {percent}% (суммируется с промогруппой)",
            ).format(percent=template.discount_percent)
        )
        stack_note = texts.t(
            "ADMIN_PROMO_OFFER_STACKABLE_NOTE",
            "Скидка применяется один раз и добавляется к промогруппе.",
        )
        if stack_note:
            lines.append(stack_note)
        active_hours = template.active_discount_hours or 0
        if active_hours > 0:
            lines.append(
                texts.t(
                    "ADMIN_PROMO_OFFER_ACTIVE_DURATION",
                    "Скидка после активации действует {hours} ч.",
                ).format(hours=active_hours)
            )
    else:
        duration = template.test_duration_hours or 0
        lines.append(texts.t("ADMIN_PROMO_OFFER_TEST_DURATION", "Доступ: {hours} ч").format(hours=duration))
        squads = template.test_squad_uuids or []
        if server_name:
            lines.append(
                texts.t("ADMIN_PROMO_OFFER_TEST_SQUAD_NAME", "Сервер: {name}").format(name=server_name)
            )
        elif squads:
            lines.append(
                texts.t("ADMIN_PROMO_OFFER_TEST_SQUADS", "Сквады: {squads}").format(
                    squads=", ".join(str(item) for item in squads)
                )
            )
        elif server_uuid:
            lines.append(
                texts.t("ADMIN_PROMO_OFFER_TEST_SQUADS", "Сквады: {squads}").format(squads=server_uuid)
            )
        else:
            lines.append(texts.t("ADMIN_PROMO_OFFER_TEST_SQUADS_EMPTY", "Сквады: не указаны"))

    allowed_segments = config.get("allowed_segments", [])
    if allowed_segments:
        segment_labels = [label for _, label in allowed_segments]
        lines.append("")
        lines.append(texts.t("ADMIN_PROMO_OFFER_ALLOWED", "Доступные категории:") )
        lines.extend(segment_labels)

    lines.append("")
    lines.append(texts.t("ADMIN_PROMO_OFFER_PREVIEW", "Предпросмотр:"))
    lines.append(
        _render_template_text(
            template,
            language,
            server_name=server_name,
        )
    )

    return "\n".join(lines)


@admin_required
@error_handler
async def show_promo_offers_menu(callback: CallbackQuery, db_user: User, db: AsyncSession):
    await ensure_default_templates(db, created_by=db_user.id)
    templates = await list_promo_offer_templates(db)
    texts = get_texts(db_user.language)
    header = texts.t("ADMIN_PROMO_OFFERS_TITLE", "🎯 <b>Промо-предложения</b>\n\nВыберите предложение для настройки:")
    await callback.message.edit_text(
        header,
        reply_markup=_build_templates_keyboard(templates, db_user.language),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def show_promo_offer_details(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    try:
        template_id = int(callback.data.split("_")[-1])
    except (ValueError, AttributeError):
        await callback.answer("❌ Неверный идентификатор", show_alert=True)
        return

    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        await callback.answer("❌ Предложение не найдено", show_alert=True)
        return

    await state.update_data(selected_promo_offer=template.id)
    squad_uuid, squad_name = await _resolve_template_squad(db, template)
    description = _describe_offer(
        template,
        db_user.language,
        server_name=squad_name,
        server_uuid=squad_uuid,
    )
    await callback.message.edit_text(
        description,
        reply_markup=_build_offer_detail_keyboard(template, db_user.language),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def show_promo_offer_logs(callback: CallbackQuery, db_user: User, db: AsyncSession):
    try:
        if "_page_" in callback.data:
            page = int(callback.data.split("_page_")[-1])
        else:
            page = 1
    except (ValueError, AttributeError):
        page = 1

    if page < 1:
        page = 1

    limit = PROMO_OFFER_LOGS_PAGE_LIMIT
    offset = (page - 1) * limit
    logs, total = await list_promo_offer_logs(db, offset=offset, limit=limit)
    total_pages = max(1, (total + limit - 1) // limit)

    if page > total_pages and total > 0:
        page = total_pages
        offset = (page - 1) * limit
        logs, _ = await list_promo_offer_logs(db, offset=offset, limit=limit)

    texts = get_texts(db_user.language)
    header = texts.t(
        "ADMIN_PROMO_OFFER_LOGS_TITLE",
        "📜 <b>Лог операций промо-предложений</b>",
    )

    if logs:
        message_lines = [
            header,
            texts.get(
                "ADMIN_PROMO_OFFER_LOGS_PAGINATION",
                "Страница {page}/{total}",
            ).format(page=page, total=total_pages),
            "",
        ]
        for index, entry in enumerate(logs, start=offset + 1):
            message_lines.append(_format_promo_offer_log_entry(entry, index, texts))
            message_lines.append("")
        message_text = "\n".join(message_lines).rstrip()
    else:
        message_text = "\n".join(
            [
                header,
                "",
                texts.get(
                    "ADMIN_PROMO_OFFER_LOGS_EMPTY_BODY",
                    "Записей пока нет.",
                ),
            ]
        )

    keyboard = _build_logs_keyboard(page, total_pages, db_user.language)
    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


async def _prompt_edit(callback: CallbackQuery, state: FSMContext, template_id: int, prompt: str, new_state):
    await state.update_data(
        selected_promo_offer=template_id,
        promo_edit_message_id=callback.message.message_id,
        promo_edit_chat_id=callback.message.chat.id,
    )
    await callback.message.edit_text(prompt)
    await state.set_state(new_state)
    await callback.answer()


@admin_required
@error_handler
async def prompt_edit_message(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t("ADMIN_PROMO_OFFER_PROMPT_MESSAGE", "Введите новый текст предложения:")
    await _prompt_edit(callback, state, template_id, prompt, AdminStates.editing_promo_offer_message)


@admin_required
@error_handler
async def prompt_edit_button(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t("ADMIN_PROMO_OFFER_PROMPT_BUTTON", "Введите новый текст кнопки:")
    await _prompt_edit(callback, state, template_id, prompt, AdminStates.editing_promo_offer_button)


@admin_required
@error_handler
async def prompt_edit_valid(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t("ADMIN_PROMO_OFFER_PROMPT_VALID", "Укажите срок действия (в часах):")
    await _prompt_edit(callback, state, template_id, prompt, AdminStates.editing_promo_offer_valid_hours)


@admin_required
@error_handler
async def prompt_edit_discount(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t("ADMIN_PROMO_OFFER_PROMPT_DISCOUNT", "Введите размер скидки в процентах:")
    await _prompt_edit(callback, state, template_id, prompt, AdminStates.editing_promo_offer_discount)


@admin_required
@error_handler
async def prompt_edit_active_duration(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t(
        "ADMIN_PROMO_OFFER_PROMPT_ACTIVE_DURATION",
        "Введите срок действия активированной скидки (в часах):",
    )
    await _prompt_edit(
        callback,
        state,
        template_id,
        prompt,
        AdminStates.editing_promo_offer_active_duration,
    )


@admin_required
@error_handler
async def prompt_edit_duration(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t("ADMIN_PROMO_OFFER_PROMPT_DURATION", "Введите длительность тестового доступа (в часах):")
    await _prompt_edit(callback, state, template_id, prompt, AdminStates.editing_promo_offer_test_duration)


@admin_required
@error_handler
async def prompt_edit_squads(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        await callback.answer("❌ Предложение не найдено", show_alert=True)
        return

    await state.update_data(
        selected_promo_offer=template.id,
        promo_edit_message_id=callback.message.message_id,
        promo_edit_chat_id=callback.message.chat.id,
    )

    await _render_squad_selection(callback, template, db, db_user.language)
    await callback.answer()


async def _render_squad_selection(
    callback: CallbackQuery,
    template: PromoOfferTemplate,
    db: AsyncSession,
    language: str,
    page: int = 1,
):
    texts = get_texts(language)

    squads, total_count = await get_all_server_squads(
        db,
        available_only=False,
        page=page,
        limit=SQUADS_PAGE_LIMIT,
    )

    if total_count == 0:
        await callback.message.edit_text(
            texts.t("ADMIN_PROMO_OFFER_NO_SQUADS_AVAILABLE", "❌ Доступные серверы не найдены."),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=texts.BACK, callback_data=f"promo_offer_squad_back_{template.id}")]]
            ),
        )
        return

    selected_uuid = None
    if template.test_squad_uuids:
        selected_uuid = str(template.test_squad_uuids[0])

    selected_server_name = None
    if selected_uuid:
        selected_server = next((srv for srv in squads if srv.squad_uuid == selected_uuid), None)
        if not selected_server:
            selected_server = await get_server_squad_by_uuid(db, selected_uuid)
        if selected_server:
            selected_server_name = selected_server.display_name

    header = texts.t("ADMIN_PROMO_OFFER_SELECT_SQUAD_TITLE", "🌍 <b>Выберите сквад</b>")
    if selected_server_name:
        current = texts.t(
            "ADMIN_PROMO_OFFER_SELECTED_SQUAD",
            "Текущий сквад: {name}",
        ).format(name=selected_server_name)
    elif selected_uuid:
        current = texts.t(
            "ADMIN_PROMO_OFFER_SELECTED_SQUAD_UUID",
            "Текущий сквад: {uuid}",
        ).format(uuid=selected_uuid)
    else:
        current = texts.t(
            "ADMIN_PROMO_OFFER_SELECTED_SQUAD_EMPTY",
            "Текущий сквад: не выбран",
        )

    hint = texts.t(
        "ADMIN_PROMO_OFFER_SELECT_SQUAD_HINT",
        "Выберите сервер для тестового доступа из списка ниже.",
    )

    total_pages = (total_count + SQUADS_PAGE_LIMIT - 1) // SQUADS_PAGE_LIMIT or 1
    page = max(1, min(page, total_pages))

    lines = [header, "", current, "", hint]
    if total_pages > 1:
        lines.append(
            texts.t(
                "ADMIN_PROMO_OFFER_SELECT_SQUAD_PAGE",
                "Страница {page}/{total}",
            ).format(page=page, total=total_pages)
        )

    text = "\n".join(lines)

    keyboard_rows: List[List[InlineKeyboardButton]] = []
    for server in squads:
        emoji = "✅" if server.squad_uuid == selected_uuid else ("⚪" if server.is_available else "🔒")
        label = f"{emoji} {server.display_name}"
        keyboard_rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"promo_offer_select_squad_{template.id}_{server.id}_{page}",
            )
        ])

    if total_pages > 1:
        nav_row: List[InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"promo_offer_squad_page_{template.id}_{page - 1}",
                )
            )
        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"promo_offer_squad_page_{template.id}_{page + 1}",
                )
            )
        if nav_row:
            keyboard_rows.append(nav_row)

    action_row = [
        InlineKeyboardButton(
            text=texts.t("ADMIN_PROMO_OFFER_SELECT_SQUAD_CLEAR", "🗑 Очистить"),
            callback_data=f"promo_offer_clear_squad_{template.id}_{page}",
        ),
        InlineKeyboardButton(
            text=texts.t("ADMIN_PROMO_OFFER_SELECT_SQUAD_BACK", "↩️ Назад"),
            callback_data=f"promo_offer_squad_back_{template.id}",
        ),
    ]
    keyboard_rows.append(action_row)

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        parse_mode="HTML",
    )


async def _render_offer_details(
    callback: CallbackQuery,
    template: PromoOfferTemplate,
    language: str,
    db: AsyncSession,
):
    squad_uuid, squad_name = await _resolve_template_squad(db, template)
    description = _describe_offer(
        template,
        language,
        server_name=squad_name,
        server_uuid=squad_uuid,
    )
    await callback.message.edit_text(
        description,
        reply_markup=_build_offer_detail_keyboard(template, language),
        parse_mode="HTML",
    )


async def _handle_edit_field(
    message: Message,
    state: FSMContext,
    db: AsyncSession,
    db_user: User,
    field: str,
):
    data = await state.get_data()
    template_id = data.get("selected_promo_offer")
    if not template_id:
        await _safe_delete_message(message)
        await message.answer("❌ Не удалось определить предложение. Повторите действие.")
        await state.clear()
        return

    template = await get_promo_offer_template_by_id(db, int(template_id))
    if not template:
        await _safe_delete_message(message)
        await message.answer("❌ Предложение не найдено.")
        await state.clear()
        return

    value = message.text.strip()
    try:
        if field == "message_text":
            await update_promo_offer_template(db, template, message_text=value)
        elif field == "button_text":
            await update_promo_offer_template(db, template, button_text=value)
        elif field == "valid_hours":
            hours = max(1, int(value))
            await update_promo_offer_template(db, template, valid_hours=hours)
        elif field == "discount_percent":
            percent = max(0, min(100, int(value)))
            await update_promo_offer_template(db, template, discount_percent=percent)
        elif field == "active_discount_hours":
            hours = max(1, int(value))
            await update_promo_offer_template(db, template, active_discount_hours=hours)
        elif field == "test_duration_hours":
            hours = max(1, int(value))
            await update_promo_offer_template(db, template, test_duration_hours=hours)
        elif field == "test_squad_uuids":
            if value.lower() in {"clear", "очистить"}:
                squads: List[str] = []
            else:
                squads = [item for item in re.split(r"[\s,]+", value) if item]
            await update_promo_offer_template(db, template, test_squad_uuids=squads)
        else:
            raise ValueError("Unsupported field")
    except ValueError:
        await _safe_delete_message(message)
        await message.answer("❌ Некорректное значение. Попробуйте снова.")
        return

    edit_message_id = data.get("promo_edit_message_id")
    edit_chat_id = data.get("promo_edit_chat_id", message.chat.id)

    await state.clear()
    updated_template = await get_promo_offer_template_by_id(db, template.id)
    if not updated_template:
        await _safe_delete_message(message)
        await message.answer("❌ Предложение не найдено после обновления.")
        return

    squad_uuid, squad_name = await _resolve_template_squad(db, updated_template)
    description = _describe_offer(
        updated_template,
        db_user.language,
        server_name=squad_name,
        server_uuid=squad_uuid,
    )
    reply_markup = _build_offer_detail_keyboard(updated_template, db_user.language)

    if edit_message_id:
        try:
            await message.bot.edit_message_text(
                description,
                chat_id=edit_chat_id,
                message_id=edit_message_id,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except TelegramBadRequest as exc:
            error_text = str(exc).lower()
            if "there is no text in the message to edit" in error_text:
                logger.debug("Сообщение промо без текста, пересылаем обновлённую версию")
                try:
                    await message.bot.delete_message(chat_id=edit_chat_id, message_id=edit_message_id)
                except TelegramBadRequest:
                    logger.debug("Не удалось удалить сообщение промо без текста")
            else:
                logger.warning("Не удалось обновить сообщение редактирования промо: %s", exc)
            await message.answer(description, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await message.answer(description, reply_markup=reply_markup, parse_mode="HTML")

    await _safe_delete_message(message)


@admin_required
@error_handler
async def show_send_segments(callback: CallbackQuery, db_user: User, db: AsyncSession):
    template_id = int(callback.data.split("_")[-1])
    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        await callback.answer("❌ Предложение не найдено", show_alert=True)
        return

    await callback.message.edit_reply_markup(
        reply_markup=_build_send_keyboard(template, db_user.language)
    )
    await callback.answer()


def _build_connect_button_rows(user: User, texts) -> List[List[InlineKeyboardButton]]:
    subscription = getattr(user, "subscription", None)
    if not subscription:
        return []

    button_text = texts.t("CONNECT_BUTTON", "🔗 Подключиться")
    subscription_link = get_display_subscription_link(subscription)
    connect_mode = settings.CONNECT_BUTTON_MODE

    def _fallback_button() -> InlineKeyboardButton:
        return InlineKeyboardButton(text=button_text, callback_data="subscription_connect")

    rows: List[List[InlineKeyboardButton]] = []

    if connect_mode == "miniapp_subscription":
        if subscription_link:
            rows.append([
                InlineKeyboardButton(
                    text=button_text,
                    web_app=types.WebAppInfo(url=subscription_link),
                )
            ])
        else:
            rows.append([_fallback_button()])
    elif connect_mode == "miniapp_custom":
        if settings.MINIAPP_CUSTOM_URL:
            rows.append([
                InlineKeyboardButton(
                    text=button_text,
                    web_app=types.WebAppInfo(url=settings.MINIAPP_CUSTOM_URL),
                )
            ])
        else:
            rows.append([_fallback_button()])
    elif connect_mode == "link":
        if subscription_link:
            rows.append([
                InlineKeyboardButton(text=button_text, url=subscription_link)
            ])
        else:
            rows.append([_fallback_button()])
    elif connect_mode == "happ_cryptolink":
        if subscription_link:
            rows.append([
                InlineKeyboardButton(text=button_text, callback_data="open_subscription_link")
            ])
        else:
            rows.append([_fallback_button()])
    else:
        rows.append([_fallback_button()])

    happ_row = get_happ_download_button_row(texts)
    if happ_row:
        rows.append(happ_row)

    return rows


@admin_required
@error_handler
async def send_offer_to_segment(callback: CallbackQuery, db_user: User, db: AsyncSession):
    try:
        prefix = "promo_offer_send_"
        if not callback.data.startswith(prefix):
            raise ValueError("invalid prefix")
        data = callback.data[len(prefix):]
        template_id_str, segment = data.split("_", 1)
        template_id = int(template_id_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Некорректные данные", show_alert=True)
        return

    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        await callback.answer("❌ Предложение не найдено", show_alert=True)
        return

    config = OFFER_TYPE_CONFIG.get(template.offer_type, {})
    squad_uuid, squad_name = await _resolve_template_squad(db, template)
    allowed_segments = {seg for seg, _ in config.get("allowed_segments", [])}
    if segment not in allowed_segments:
        await callback.answer("⚠️ Нельзя отправить это предложение выбранной категории", show_alert=True)
        return

    texts = get_texts(db_user.language)
    await callback.answer(texts.t("ADMIN_PROMO_OFFER_SENDING", "Начинаем рассылку..."), show_alert=True)

    users = await get_users_for_promo_segment(db, segment)
    initial_count = len(users)

    if template.offer_type == "test_access" and squad_uuid:
        filtered_users: List[User] = []
        for user in users:
            subscription = getattr(user, "subscription", None)
            connected = set(subscription.connected_squads or []) if subscription else set()
            if squad_uuid in connected:
                continue
            filtered_users.append(user)
        users = filtered_users

    if not users:
        await callback.message.answer(texts.t("ADMIN_PROMO_OFFER_NO_USERS", "Подходящих пользователей не найдено."))
        return

    sent = 0
    failed = 0
    skipped = initial_count - len(users)
    effect_type = config.get("effect_type", "percent_discount")

    for user in users:
        try:
            offer_record = await upsert_discount_offer(
                db,
                user_id=user.id,
                subscription_id=user.subscription.id if user.subscription else None,
                notification_type=f"promo_template_{template.id}",
                discount_percent=template.discount_percent,
                bonus_amount_kopeks=0,
                valid_hours=template.valid_hours,
                effect_type=effect_type,
                extra_data={
                    "template_id": template.id,
                    "offer_type": template.offer_type,
                    "test_duration_hours": template.test_duration_hours,
                    "test_squad_uuids": template.test_squad_uuids,
                    "active_discount_hours": template.active_discount_hours,
                },
            )

            user_texts = get_texts(user.language or db_user.language)
            keyboard_rows: List[List[InlineKeyboardButton]] = [
                [InlineKeyboardButton(text=template.button_text, callback_data=f"claim_discount_{offer_record.id}")]
            ]

            keyboard_rows.append([
                InlineKeyboardButton(
                    text=user_texts.t("PROMO_OFFER_CLOSE", "❌ Закрыть"),
                    callback_data="promo_offer_close",
                )
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

            message_text = _render_template_text(
                template,
                user.language or db_user.language,
                server_name=squad_name,
            )
            await callback.bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            sent += 1
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.warning("Не удалось отправить предложение пользователю %s: %s", user.telegram_id, exc)
            failed += 1
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Ошибка рассылки промо предложения пользователю %s: %s", user.telegram_id, exc)
            failed += 1

    summary = texts.t(
        "ADMIN_PROMO_OFFER_RESULT",
        "📬 Рассылка завершена\nОтправлено: {sent}\nОшибок: {failed}",
    ).format(sent=sent, failed=failed)
    if skipped > 0:
        summary += "\n" + texts.t(
            "ADMIN_PROMO_OFFER_SKIPPED",
            "Пропущено: {skipped} (уже есть доступ)",
        ).format(skipped=skipped)
    refreshed = await get_promo_offer_template_by_id(db, template.id)
    result_keyboard_rows: List[List[InlineKeyboardButton]] = []

    if refreshed:
        result_keyboard_rows.append([
            InlineKeyboardButton(
                text=texts.t("ADMIN_PROMO_OFFER_BACK_TO_TEMPLATE", "↩️ К предложению"),
                callback_data=f"promo_offer_{refreshed.id}",
            )
        ])

    result_keyboard_rows.append([
        InlineKeyboardButton(
            text=texts.t("ADMIN_PROMO_OFFER_BACK_TO_LIST", "⬅️ К промопредложениям"),
            callback_data="admin_promo_offers",
        )
    ])

    await callback.message.edit_text(
        summary,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=result_keyboard_rows),
        parse_mode="HTML",
    )


async def process_edit_message_text(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "message_text")


async def process_edit_button_text(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "button_text")


async def process_edit_valid_hours(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "valid_hours")


async def process_edit_active_duration_hours(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "active_discount_hours")


async def process_edit_discount_percent(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "discount_percent")


async def process_edit_test_duration(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "test_duration_hours")


@admin_required
@error_handler
async def paginate_squad_selection(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    try:
        prefix = "promo_offer_squad_page_"
        if not callback.data.startswith(prefix):
            raise ValueError("invalid prefix")
        payload = callback.data[len(prefix):]
        template_id_str, page_str = payload.split("_", 1)
        template_id = int(template_id_str)
        page = int(page_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Некорректные данные", show_alert=True)
        return

    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        await callback.answer("❌ Предложение не найдено", show_alert=True)
        return

    await state.update_data(selected_promo_offer=template.id)
    await _render_squad_selection(callback, template, db, db_user.language, page=page)
    await callback.answer()


@admin_required
@error_handler
async def select_squad_for_template(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    try:
        prefix = "promo_offer_select_squad_"
        if not callback.data.startswith(prefix):
            raise ValueError("invalid prefix")
        payload = callback.data[len(prefix):]
        template_id_str, server_id_str, page_str = payload.split("_", 2)
        template_id = int(template_id_str)
        server_id = int(server_id_str)
        page = int(page_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Некорректные данные", show_alert=True)
        return

    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        await callback.answer("❌ Предложение не найдено", show_alert=True)
        return

    server = await get_server_squad_by_id(db, server_id)
    if not server:
        await callback.answer(
            get_texts(db_user.language).t(
                "ADMIN_PROMO_OFFER_SELECT_SQUAD_NOT_FOUND",
                "❌ Сервер не найден",
            ),
            show_alert=True,
        )
        return

    await update_promo_offer_template(db, template, test_squad_uuids=[server.squad_uuid])
    updated = await get_promo_offer_template_by_id(db, template.id)
    if updated:
        await state.update_data(selected_promo_offer=updated.id)

    texts = get_texts(db_user.language)
    await callback.answer(texts.t("ADMIN_PROMO_OFFER_SELECT_SQUAD_UPDATED", "✅ Сквад обновлён"))

    if updated:
        await _render_offer_details(callback, updated, db_user.language, db)
    else:
        await _render_squad_selection(callback, template, db, db_user.language, page=page)


@admin_required
@error_handler
async def clear_squad_for_template(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    try:
        prefix = "promo_offer_clear_squad_"
        if not callback.data.startswith(prefix):
            raise ValueError("invalid prefix")
        payload = callback.data[len(prefix):]
        template_id_str, page_str = payload.split("_", 1)
        template_id = int(template_id_str)
        page = int(page_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Некорректные данные", show_alert=True)
        return

    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        await callback.answer("❌ Предложение не найдено", show_alert=True)
        return

    await update_promo_offer_template(db, template, test_squad_uuids=[])
    updated = await get_promo_offer_template_by_id(db, template.id)
    if updated:
        await state.update_data(selected_promo_offer=updated.id)

    texts = get_texts(db_user.language)
    await callback.answer(texts.t("ADMIN_PROMO_OFFER_SELECT_SQUAD_CLEARED", "✅ Сквад очищен"))

    if updated:
        await _render_squad_selection(callback, updated, db, db_user.language, page=page)
    else:
        await _render_squad_selection(callback, template, db, db_user.language, page=page)


@admin_required
@error_handler
async def back_to_offer_from_squads(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    try:
        template_id = int(callback.data.split("_")[-1])
    except (ValueError, AttributeError):
        await callback.answer("❌ Некорректные данные", show_alert=True)
        return

    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        await callback.answer("❌ Предложение не найдено", show_alert=True)
        return

    await state.update_data(selected_promo_offer=template.id)
    await _render_offer_details(callback, template, db_user.language, db)
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_promo_offers_menu, F.data == "admin_promo_offers")
    dp.callback_query.register(prompt_edit_message, F.data.startswith("promo_offer_edit_message_"))
    dp.callback_query.register(prompt_edit_button, F.data.startswith("promo_offer_edit_button_"))
    dp.callback_query.register(prompt_edit_valid, F.data.startswith("promo_offer_edit_valid_"))
    dp.callback_query.register(prompt_edit_discount, F.data.startswith("promo_offer_edit_discount_"))
    dp.callback_query.register(prompt_edit_active_duration, F.data.startswith("promo_offer_edit_active_"))
    dp.callback_query.register(prompt_edit_duration, F.data.startswith("promo_offer_edit_duration_"))
    dp.callback_query.register(prompt_edit_squads, F.data.startswith("promo_offer_edit_squads_"))
    dp.callback_query.register(paginate_squad_selection, F.data.startswith("promo_offer_squad_page_"))
    dp.callback_query.register(select_squad_for_template, F.data.startswith("promo_offer_select_squad_"))
    dp.callback_query.register(clear_squad_for_template, F.data.startswith("promo_offer_clear_squad_"))
    dp.callback_query.register(back_to_offer_from_squads, F.data.startswith("promo_offer_squad_back_"))
    dp.callback_query.register(show_send_segments, F.data.startswith("promo_offer_send_menu_"))
    dp.callback_query.register(send_offer_to_segment, F.data.startswith("promo_offer_send_"))
    dp.callback_query.register(show_promo_offer_logs, F.data.regexp(r"^promo_offer_logs_page_\d+$"))
    dp.callback_query.register(show_promo_offer_details, F.data.startswith("promo_offer_"))

    dp.message.register(process_edit_message_text, AdminStates.editing_promo_offer_message)
    dp.message.register(process_edit_button_text, AdminStates.editing_promo_offer_button)
    dp.message.register(process_edit_valid_hours, AdminStates.editing_promo_offer_valid_hours)
    dp.message.register(process_edit_active_duration_hours, AdminStates.editing_promo_offer_active_duration)
    dp.message.register(process_edit_discount_percent, AdminStates.editing_promo_offer_discount)
    dp.message.register(process_edit_test_duration, AdminStates.editing_promo_offer_test_duration)
