import logging
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.referral_contest import (
    create_referral_contest,
    get_contest_events_count,
    get_contest_leaderboard,
    get_referral_contest,
    get_referral_contests_count,
    list_referral_contests,
    toggle_referral_contest,
)
from app.keyboards.admin import (
    get_admin_contests_keyboard,
    get_admin_contests_root_keyboard,
    get_admin_pagination_keyboard,
    get_contest_mode_keyboard,
    get_referral_contest_manage_keyboard,
)
from app.localization.texts import get_texts
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


def _ensure_timezone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось загрузить TZ %s, используем UTC", tz_name)
        return ZoneInfo("UTC")


def _format_contest_summary(contest, texts, tz: ZoneInfo) -> str:
    start_local = contest.start_at if contest.start_at.tzinfo else contest.start_at.replace(tzinfo=timezone.utc)
    end_local = contest.end_at if contest.end_at.tzinfo else contest.end_at.replace(tzinfo=timezone.utc)
    start_local = start_local.astimezone(tz)
    end_local = end_local.astimezone(tz)

    status = texts.t("ADMIN_CONTEST_STATUS_ACTIVE", "🟢 Активен") if contest.is_active else texts.t(
        "ADMIN_CONTEST_STATUS_INACTIVE", "⚪️ Выключен"
    )

    period = (
        f"{start_local.strftime('%d.%m %H:%M')} — "
        f"{end_local.strftime('%d.%m %H:%M')} ({tz.key})"
    )

    summary_time = contest.daily_summary_time.strftime("%H:%M") if contest.daily_summary_time else "12:00"
    parts = [
        f"{status}",
        f"Период: <b>{period}</b>",
        f"Дневная сводка: <b>{summary_time}</b>",
    ]
    if contest.prize_text:
        parts.append(texts.t("ADMIN_CONTEST_PRIZE", "Приз: {prize}").format(prize=contest.prize_text))
    if contest.last_daily_summary_date:
        parts.append(
            texts.t("ADMIN_CONTEST_LAST_DAILY", "Последняя сводка: {date}").format(
                date=contest.last_daily_summary_date.strftime("%d.%m")
            )
        )
    return "\n".join(parts)


def _parse_local_datetime(value: str, tz: ZoneInfo) -> datetime | None:
    try:
        dt = datetime.strptime(value.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        return None
    return dt.replace(tzinfo=tz)


def _parse_time(value: str):
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        return None


@admin_required
@error_handler
async def show_contests_menu(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    if not settings.is_contests_enabled():
        await callback.message.edit_text(
            texts.t(
                "ADMIN_CONTESTS_DISABLED",
                "Конкурсы отключены через переменную окружения CONTESTS_ENABLED.",
            ),
            reply_markup=get_admin_contests_root_keyboard(db_user.language),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        texts.t("ADMIN_CONTESTS_TITLE", "🏆 <b>Конкурсы</b>\n\nВыберите действие:"),
        reply_markup=get_admin_contests_root_keyboard(db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_referral_contests_menu(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        texts.t("ADMIN_CONTESTS_TITLE", "🏆 <b>Конкурсы</b>\n\nВыберите действие:"),
        reply_markup=get_admin_contests_keyboard(db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def list_contests(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    if not settings.is_contests_enabled():
        await callback.answer(
            get_texts(db_user.language).t(
                "ADMIN_CONTESTS_DISABLED",
                "Конкурсы отключены через переменную окружения CONTESTS_ENABLED.",
            ),
            show_alert=True,
        )
        return

    page = 1
    if callback.data.startswith("admin_contests_list_page_"):
        try:
            page = int(callback.data.split("_")[-1])
        except Exception:
            page = 1

    total = await get_referral_contests_count(db)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    offset = (page - 1) * PAGE_SIZE

    contests = await list_referral_contests(db, limit=PAGE_SIZE, offset=offset)
    texts = get_texts(db_user.language)

    lines = [texts.t("ADMIN_CONTESTS_LIST_HEADER", "🏆 <b>Конкурсы</b>\n")]

    if not contests:
        lines.append(texts.t("ADMIN_CONTESTS_EMPTY", "Пока нет созданных конкурсов."))
    else:
        for contest in contests:
            lines.append(f"• <b>{contest.title}</b> (#{contest.id})")
            contest_tz = _ensure_timezone(contest.timezone or settings.TIMEZONE)
            lines.append(_format_contest_summary(contest, texts, contest_tz))
            lines.append("")

    keyboard_rows: list[list[types.InlineKeyboardButton]] = []
    for contest in contests:
        title = contest.title if len(contest.title) <= 25 else contest.title[:22] + "..."
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"🔎 {title}",
                    callback_data=f"admin_contest_view_{contest.id}",
                )
            ]
        )

    pagination = get_admin_pagination_keyboard(
        page,
        total_pages,
        "admin_contests_list",
        back_callback="admin_contests",
        language=db_user.language,
    )
    keyboard_rows.extend(pagination.inline_keyboard)

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_contest_details(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    if not settings.is_contests_enabled():
        await callback.answer(
            get_texts(db_user.language).t("ADMIN_CONTESTS_DISABLED", "Конкурсы отключены."),
            show_alert=True,
        )
        return

    contest_id = int(callback.data.split("_")[-1])
    contest = await get_referral_contest(db, contest_id)
    texts = get_texts(db_user.language)

    if not contest:
        await callback.answer(texts.t("ADMIN_CONTEST_NOT_FOUND", "Конкурс не найден."), show_alert=True)
        return

    tz = _ensure_timezone(contest.timezone or settings.TIMEZONE)
    leaderboard = await get_contest_leaderboard(db, contest.id, limit=5)
    total_events = await get_contest_events_count(db, contest.id)

    lines = [
        f"🏆 <b>{contest.title}</b>",
        _format_contest_summary(contest, texts, tz),
        texts.t("ADMIN_CONTEST_TOTAL_EVENTS", "Зачётов: <b>{count}</b>").format(count=total_events),
    ]

    if contest.description:
        lines.append("")
        lines.append(contest.description)

    if leaderboard:
        lines.append("")
        lines.append(texts.t("ADMIN_CONTEST_LEADERBOARD_TITLE", "📊 Топ участников:"))
        for idx, (user, score, _) in enumerate(leaderboard, start=1):
            lines.append(f"{idx}. {user.full_name} — {score}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=get_referral_contest_manage_keyboard(
            contest.id, is_active=contest.is_active, language=db_user.language
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_contest(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    if not settings.is_contests_enabled():
        await callback.answer(
            get_texts(db_user.language).t("ADMIN_CONTESTS_DISABLED", "Конкурсы отключены."),
            show_alert=True,
        )
        return

    contest_id = int(callback.data.split("_")[-1])
    contest = await get_referral_contest(db, contest_id)

    if not contest:
        await callback.answer("Конкурс не найден", show_alert=True)
        return

    await toggle_referral_contest(db, contest, not contest.is_active)
    await show_contest_details(callback, db_user, db)


@admin_required
@error_handler
async def show_leaderboard(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    if not settings.is_contests_enabled():
        await callback.answer(
            get_texts(db_user.language).t("ADMIN_CONTESTS_DISABLED", "Конкурсы отключены."),
            show_alert=True,
        )
        return

    contest_id = int(callback.data.split("_")[-1])
    contest = await get_referral_contest(db, contest_id)
    texts = get_texts(db_user.language)

    if not contest:
        await callback.answer(texts.t("ADMIN_CONTEST_NOT_FOUND", "Конкурс не найден."), show_alert=True)
        return

    leaderboard = await get_contest_leaderboard(db, contest_id, limit=10)
    if not leaderboard:
        await callback.answer(texts.t("ADMIN_CONTEST_EMPTY_LEADERBOARD", "Пока нет участников."), show_alert=True)
        return

    lines = [
        texts.t("ADMIN_CONTEST_LEADERBOARD_TITLE", "📊 Топ участников:"),
    ]
    for idx, (user, score, _) in enumerate(leaderboard, start=1):
        lines.append(f"{idx}. {user.full_name} — {score}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=get_referral_contest_manage_keyboard(
            contest_id, is_active=contest.is_active, language=db_user.language
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def start_contest_creation(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    if not settings.is_contests_enabled():
        await callback.answer(
            texts.t("ADMIN_CONTESTS_DISABLED", "Конкурсы отключены."),
            show_alert=True,
        )
        return

    await state.clear()
    await state.set_state(AdminStates.creating_referral_contest_mode)
    await callback.message.edit_text(
        texts.t(
            "ADMIN_CONTEST_MODE_PROMPT",
            "Выберите условие зачёта: реферал должен купить подписку или достаточно регистрации.",
        ),
        reply_markup=get_contest_mode_keyboard(db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def select_contest_mode(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    mode = "referral_paid" if callback.data == "admin_contest_mode_paid" else "referral_registered"
    await state.update_data(contest_type=mode)
    await state.set_state(AdminStates.creating_referral_contest_title)
    await callback.message.edit_text(
        texts.t("ADMIN_CONTEST_ENTER_TITLE", "Введите название конкурса:"),
        reply_markup=None,
    )
    await callback.answer()


@admin_required
@error_handler
async def process_title(message: types.Message, state: FSMContext, db_user, db: AsyncSession):
    title = message.text.strip()
    texts = get_texts(db_user.language)

    await state.update_data(title=title)
    await state.set_state(AdminStates.creating_referral_contest_description)
    await message.answer(
        texts.t("ADMIN_CONTEST_ENTER_DESCRIPTION", "Опишите конкурс (или отправьте '-' чтобы пропустить):")
    )


@admin_required
@error_handler
async def process_description(message: types.Message, state: FSMContext, db_user, db: AsyncSession):
    description = message.text.strip()
    if description in {"-", "skip", "пропустить"}:
        description = None

    await state.update_data(description=description)
    await state.set_state(AdminStates.creating_referral_contest_prize)
    texts = get_texts(db_user.language)
    await message.answer(
        texts.t("ADMIN_CONTEST_ENTER_PRIZE", "Укажите призы/выгоды конкурса (или '-' чтобы пропустить):")
    )


@admin_required
@error_handler
async def process_prize(message: types.Message, state: FSMContext, db_user, db: AsyncSession):
    prize = message.text.strip()
    if prize in {"-", "skip", "пропустить"}:
        prize = None

    await state.update_data(prize=prize)
    await state.set_state(AdminStates.creating_referral_contest_start)
    texts = get_texts(db_user.language)
    await message.answer(
        texts.t(
            "ADMIN_CONTEST_ENTER_START",
            "Введите дату и время старта (дд.мм.гггг чч:мм) по вашему часовому поясу:",
        )
    )


@admin_required
@error_handler
async def process_start_date(message: types.Message, state: FSMContext, db_user, db: AsyncSession):
    tz = _ensure_timezone(settings.TIMEZONE)
    start_dt = _parse_local_datetime(message.text, tz)
    texts = get_texts(db_user.language)

    if not start_dt:
        await message.answer(
            texts.t("ADMIN_CONTEST_INVALID_DATE", "Не удалось распознать дату. Формат: 01.06.2024 12:00")
        )
        return

    await state.update_data(start_at=start_dt.isoformat())
    await state.set_state(AdminStates.creating_referral_contest_end)
    await message.answer(
        texts.t(
            "ADMIN_CONTEST_ENTER_END",
            "Введите дату и время окончания (дд.мм.гггг чч:мм) по вашему часовому поясу:",
        )
    )


@admin_required
@error_handler
async def process_end_date(message: types.Message, state: FSMContext, db_user, db: AsyncSession):
    tz = _ensure_timezone(settings.TIMEZONE)
    end_dt = _parse_local_datetime(message.text, tz)
    texts = get_texts(db_user.language)

    if not end_dt:
        await message.answer(
            texts.t("ADMIN_CONTEST_INVALID_DATE", "Не удалось распознать дату. Формат: 01.06.2024 12:00")
        )
        return

    data = await state.get_data()
    start_raw = data.get("start_at")
    start_dt = datetime.fromisoformat(start_raw) if start_raw else None
    if start_dt and end_dt <= start_dt:
        await message.answer(
            texts.t(
                "ADMIN_CONTEST_END_BEFORE_START",
                "Дата окончания должна быть позже даты начала.",
            )
        )
        return

    await state.update_data(end_at=end_dt.isoformat())
    await state.set_state(AdminStates.creating_referral_contest_time)
    await message.answer(
        texts.t(
            "ADMIN_CONTEST_ENTER_DAILY_TIME",
            "Во сколько отправлять ежедневные итоги? Укажите время в формате ЧЧ:ММ (например, 12:00).",
        )
    )


@admin_required
@error_handler
async def finalize_contest_creation(message: types.Message, state: FSMContext, db_user, db: AsyncSession):
    summary_time = _parse_time(message.text)
    texts = get_texts(db_user.language)

    if not summary_time:
        await message.answer(
            texts.t("ADMIN_CONTEST_INVALID_TIME", "Не удалось распознать время. Формат: 12:00")
        )
        return

    data = await state.get_data()
    tz = _ensure_timezone(settings.TIMEZONE)

    start_at_raw = data.get("start_at")
    end_at_raw = data.get("end_at")
    if not start_at_raw or not end_at_raw:
        await message.answer(texts.t("ADMIN_CONTEST_INVALID_DATE", "Не удалось распознать дату."))
        return

    start_at = (
        datetime.fromisoformat(start_at_raw)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    end_at = (
        datetime.fromisoformat(end_at_raw)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )

    contest_type = data.get("contest_type") or "referral_paid"

    contest = await create_referral_contest(
        db,
        title=data.get("title"),
        description=data.get("description"),
        prize_text=data.get("prize"),
        contest_type=contest_type,
        start_at=start_at,
        end_at=end_at,
        daily_summary_time=summary_time,
        timezone_name=tz.key,
        created_by=db_user.id,
    )

    await state.clear()

    await message.answer(
        texts.t("ADMIN_CONTEST_CREATED", "Конкурс создан!"),
        reply_markup=get_referral_contest_manage_keyboard(
            contest.id,
            is_active=contest.is_active,
            language=db_user.language,
        ),
    )


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_contests_menu, F.data == "admin_contests")
    dp.callback_query.register(show_referral_contests_menu, F.data == "admin_contests_referral")
    dp.callback_query.register(list_contests, F.data == "admin_contests_list")
    dp.callback_query.register(list_contests, F.data.startswith("admin_contests_list_page_"))
    dp.callback_query.register(show_contest_details, F.data.startswith("admin_contest_view_"))
    dp.callback_query.register(toggle_contest, F.data.startswith("admin_contest_toggle_"))
    dp.callback_query.register(show_leaderboard, F.data.startswith("admin_contest_leaderboard_"))
    dp.callback_query.register(start_contest_creation, F.data == "admin_contests_create")
    dp.callback_query.register(select_contest_mode, F.data.in_(["admin_contest_mode_paid", "admin_contest_mode_registered"]))

    dp.message.register(process_title, AdminStates.creating_referral_contest_title)
    dp.message.register(process_description, AdminStates.creating_referral_contest_description)
    dp.message.register(process_prize, AdminStates.creating_referral_contest_prize)
    dp.message.register(process_start_date, AdminStates.creating_referral_contest_start)
    dp.message.register(process_end_date, AdminStates.creating_referral_contest_end)
    dp.message.register(finalize_contest_creation, AdminStates.creating_referral_contest_time)
