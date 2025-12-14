import json
import logging
from datetime import datetime, timedelta
from typing import Dict

from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.contest import (
    get_template_by_id,
    list_templates,
    update_template_fields,
    create_round,
)
from app.database.models import ContestTemplate
from app.keyboards.admin import (
    get_admin_contests_keyboard,
    get_admin_contests_root_keyboard,
    get_daily_contest_manage_keyboard,
)
from app.localization.texts import get_texts
from app.services.contest_rotation_service import contest_rotation_service
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)

EDITABLE_FIELDS: Dict[str, Dict] = {
    "prize_days": {"type": int, "min": 1, "label": "приз (дни)"},
    "max_winners": {"type": int, "min": 1, "label": "макс. победителей"},
    "attempts_per_user": {"type": int, "min": 1, "label": "попыток на пользователя"},
    "times_per_day": {"type": int, "min": 1, "label": "раундов в день"},
    "schedule_times": {"type": str, "label": "расписание HH:MM через запятую"},
    "cooldown_hours": {"type": int, "min": 1, "label": "длительность раунда (часы)"},
}


async def _get_template(db: AsyncSession, template_id: int) -> ContestTemplate | None:
    return await get_template_by_id(db, template_id)


@admin_required
@error_handler
async def show_daily_contests(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    templates = await list_templates(db, enabled_only=False)

    lines = [texts.t("ADMIN_DAILY_CONTESTS_TITLE", "📆 Ежедневные конкурсы")]
    if not templates:
        lines.append(texts.t("ADMIN_CONTESTS_EMPTY", "Пока нет созданных конкурсов."))
    else:
        for tpl in templates:
            status = "🟢" if tpl.is_enabled else "⚪️"
            lines.append(f"{status} <b>{tpl.name}</b> (slug: {tpl.slug}) — приз {tpl.prize_days}д, макс {tpl.max_winners}")

    keyboard_rows = []
    for tpl in templates:
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"⚙️ {tpl.name}",
                    callback_data=f"admin_daily_contest_{tpl.id}",
                )
            ]
        )
    keyboard_rows.append([types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_contests")])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_daily_contest(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    try:
        template_id = int(callback.data.split("_")[-1])
    except Exception:
        await callback.answer("Некорректный id", show_alert=True)
        return

    tpl = await _get_template(db, template_id)
    if not tpl:
        await callback.answer(texts.t("ADMIN_CONTEST_NOT_FOUND", "Конкурс не найден."), show_alert=True)
        return

    lines = [
        f"🏷 <b>{tpl.name}</b> (slug: {tpl.slug})",
        f"{texts.t('ADMIN_CONTEST_STATUS_ACTIVE','🟢 Активен') if tpl.is_enabled else texts.t('ADMIN_CONTEST_STATUS_INACTIVE','⚪️ Выключен')}",
        f"Приз: {tpl.prize_days} дн. | Макс победителей: {tpl.max_winners}",
        f"Попыток/польз: {tpl.attempts_per_user}",
        f"Раундов в день: {tpl.times_per_day}",
        f"Расписание: {tpl.schedule_times or '-'}",
        f"Длительность раунда: {tpl.cooldown_hours} ч.",
    ]
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=get_daily_contest_manage_keyboard(tpl.id, tpl.is_enabled, db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_daily_contest(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    template_id = int(callback.data.split("_")[-1])
    tpl = await _get_template(db, template_id)
    if not tpl:
        await callback.answer(texts.t("ADMIN_CONTEST_NOT_FOUND", "Конкурс не найден."), show_alert=True)
        return
    tpl.is_enabled = not tpl.is_enabled
    await db.commit()
    await callback.answer(texts.t("ADMIN_UPDATED", "Обновлено"))
    await show_daily_contest(callback, db_user, db)


@admin_required
@error_handler
async def start_round_now(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    template_id = int(callback.data.split("_")[-1])
    tpl = await _get_template(db, template_id)
    if not tpl:
        await callback.answer(texts.t("ADMIN_CONTEST_NOT_FOUND", "Конкурс не найден."), show_alert=True)
        return

    payload = contest_rotation_service._build_payload_for_template(tpl)  # type: ignore[attr-defined]
    now = datetime.utcnow()
    ends = now + timedelta(hours=tpl.cooldown_hours)
    round_obj = await create_round(
        db,
        template=tpl,
        starts_at=now,
        ends_at=ends,
        payload=payload,
    )
    await callback.answer(texts.t("ADMIN_ROUND_STARTED", "Раунд запущен"), show_alert=True)
    await show_daily_contest(callback, db_user, db)


@admin_required
@error_handler
async def prompt_edit_field(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    parts = callback.data.split("_")
    template_id = int(parts[3])
    field = parts[4]

    tpl = await _get_template(db, template_id)
    if not tpl or field not in EDITABLE_FIELDS:
        await callback.answer(texts.t("ADMIN_CONTEST_NOT_FOUND", "Конкурс не найден."), show_alert=True)
        return

    meta = EDITABLE_FIELDS[field]
    await state.set_state(AdminStates.editing_daily_contest_field)
    await state.update_data(template_id=template_id, field=field)
    await callback.message.edit_text(
        texts.t(
            "ADMIN_CONTEST_FIELD_PROMPT",
            "Введите новое значение для {label}:",
        ).format(label=meta.get("label", field)),
        reply_markup=None,
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_field(
    message: types.Message,
    state: FSMContext,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    data = await state.get_data()
    template_id = data.get("template_id")
    field = data.get("field")
    if not template_id or not field or field not in EDITABLE_FIELDS:
        await message.answer(texts.ERROR)
        await state.clear()
        return

    tpl = await _get_template(db, template_id)
    if not tpl:
        await message.answer(texts.t("ADMIN_CONTEST_NOT_FOUND", "Конкурс не найден."))
        await state.clear()
        return

    meta = EDITABLE_FIELDS[field]
    raw = message.text or ""
    try:
        if meta["type"] is int:
            value = int(raw)
            if meta.get("min") is not None and value < meta["min"]:
                raise ValueError("min")
        else:
            value = raw.strip()
    except Exception:
        await message.answer(texts.t("ADMIN_INVALID_NUMBER", "Некорректное число"))
        await state.clear()
        return

    await update_template_fields(db, tpl, **{field: value})
    await message.answer(texts.t("ADMIN_UPDATED", "Обновлено"), reply_markup=None)
    await state.clear()


@admin_required
@error_handler
async def edit_payload(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    template_id = int(callback.data.split("_")[-1])
    tpl = await _get_template(db, template_id)
    if not tpl:
        await callback.answer(texts.t("ADMIN_CONTEST_NOT_FOUND", "Конкурс не найден."), show_alert=True)
        return

    await state.set_state(AdminStates.editing_daily_contest_value)
    await state.update_data(template_id=template_id, field="payload")
    payload_json = json.dumps(tpl.payload or {}, ensure_ascii=False, indent=2)
    await callback.message.edit_text(
        texts.t("ADMIN_CONTEST_PAYLOAD_PROMPT", "Отправьте JSON payload для игры (словарь настроек):\n") + f"<code>{payload_json}</code>",
        reply_markup=None,
    )
    await callback.answer()


@admin_required
@error_handler
async def process_payload(
    message: types.Message,
    state: FSMContext,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    data = await state.get_data()
    template_id = data.get("template_id")
    if not template_id:
        await message.answer(texts.ERROR)
        await state.clear()
        return

    try:
        payload = json.loads(message.text or "{}")
        if not isinstance(payload, dict):
            raise ValueError
    except Exception:
        await message.answer(texts.t("ADMIN_INVALID_JSON", "Некорректный JSON"))
        await state.clear()
        return

    tpl = await _get_template(db, template_id)
    if not tpl:
        await message.answer(texts.t("ADMIN_CONTEST_NOT_FOUND", "Конкурс не найден."))
        await state.clear()
        return

    await update_template_fields(db, tpl, payload=payload)
    await message.answer(texts.t("ADMIN_UPDATED", "Обновлено"))
    await state.clear()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_daily_contests, F.data == "admin_contests_daily")
    dp.callback_query.register(show_daily_contest, F.data.startswith("admin_daily_contest_"))
    dp.callback_query.register(toggle_daily_contest, F.data.startswith("admin_daily_toggle_"))
    dp.callback_query.register(start_round_now, F.data.startswith("admin_daily_start_"))
    dp.callback_query.register(prompt_edit_field, F.data.startswith("admin_daily_edit_"))
    dp.callback_query.register(edit_payload, F.data.startswith("admin_daily_payload_"))

    dp.message.register(process_edit_field, AdminStates.editing_daily_contest_field)
    dp.message.register(process_payload, AdminStates.editing_daily_contest_value)
