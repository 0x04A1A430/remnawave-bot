import asyncio
import html
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List

from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    Poll,
    PollAnswer,
    PollOption,
    PollQuestion,
    PollResponse,
    PollRun,
    User,
)
from app.handlers.admin.messages import get_target_name, get_target_users
from app.localization.texts import get_texts
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


def _format_question_summary(index: int, question: PollQuestion) -> str:
    escaped_question = html.escape(question.text)
    lines = [f"{index}. {escaped_question}"]
    for opt_index, option in enumerate(sorted(question.options, key=lambda o: o.order), start=1):
        lines.append(f"   {opt_index}) {html.escape(option.text)}")
    return "\n".join(lines)


async def _get_poll(db: AsyncSession, poll_id: int) -> Poll | None:
    stmt = (
        select(Poll)
        .options(
            selectinload(Poll.questions).selectinload(PollQuestion.options),
            selectinload(Poll.runs),
        )
        .where(Poll.id == poll_id)
    )
    result = await db.execute(stmt)
    return result.unique().scalar_one_or_none()


def _get_state_questions(data: dict) -> List[dict]:
    return list(data.get("poll_questions", []))


def _ensure_questions_present(questions: List[dict]) -> None:
    if not questions:
        raise ValueError("poll_without_questions")


@admin_required
@error_handler
async def show_polls_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    stmt = (
        select(Poll)
        .options(selectinload(Poll.questions))
        .order_by(Poll.created_at.desc())
    )
    result = await db.execute(stmt)
    polls = result.unique().scalars().all()

    text = (
        texts.t("ADMIN_POLLS_TITLE", "📋 <b>Опросы</b>")
        + "\n\n"
        + texts.t(
            "ADMIN_POLLS_DESCRIPTION",
            "Создавайте опросы и отправляйте их пользователям по категориям рассылок.",
        )
    )

    keyboard: list[list[types.InlineKeyboardButton]] = []
    for poll in polls:
        question_count = len(poll.questions)
        reward_label = (
            texts.t("ADMIN_POLLS_REWARD_ENABLED", "🎁 награда есть")
            if poll.reward_enabled and poll.reward_amount_kopeks > 0
            else texts.t("ADMIN_POLLS_REWARD_DISABLED", "без награды")
        )
        button_text = f"📋 {poll.title} ({question_count}) — {reward_label}"
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"admin_poll_{poll.id}",
                )
            ]
        )

    keyboard.append(
        [
            types.InlineKeyboardButton(
                text=texts.t("ADMIN_POLLS_CREATE", "➕ Создать опрос"),
                callback_data="admin_poll_create",
            )
        ]
    )
    keyboard.append(
        [
            types.InlineKeyboardButton(
                text=texts.BACK,
                callback_data="admin_submenu_communications",
            )
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def start_poll_creation(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await state.set_state(AdminStates.creating_poll_title)
    await state.update_data(
        poll_questions=[],
        reward_enabled=False,
        reward_amount_kopeks=0,
    )

    await callback.message.edit_text(
        texts.t(
            "ADMIN_POLLS_ENTER_TITLE",
            "🆕 <b>Создание опроса</b>\n\nВведите заголовок опроса.",
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def process_poll_title(
    message: types.Message,
    db_user: User,
    state: FSMContext,
):
    title = (message.text or "").strip()
    texts = get_texts(db_user.language)

    if not title:
        await message.answer(
            texts.t("ADMIN_POLLS_ENTER_TITLE_RETRY", "❗️ Укажите непустой заголовок."),
        )
        return

    await state.update_data(poll_title=title)
    await state.set_state(AdminStates.creating_poll_description)
    await message.answer(
        texts.t(
            "ADMIN_POLLS_ENTER_DESCRIPTION",
            "✍️ Введите описание опроса. HTML-разметка поддерживается.",
        )
    )


@admin_required
@error_handler
async def process_poll_description(
    message: types.Message,
    db_user: User,
    state: FSMContext,
):
    description = message.html_text or message.text or ""
    description = description.strip()
    texts = get_texts(db_user.language)

    if not description:
        await message.answer(
            texts.t("ADMIN_POLLS_ENTER_DESCRIPTION_RETRY", "❗️ Описание не может быть пустым."),
        )
        return

    await state.update_data(poll_description=description)
    await state.set_state(AdminStates.creating_poll_question_text)
    await message.answer(
        texts.t(
            "ADMIN_POLLS_ENTER_QUESTION",
            "❓ Отправьте текст первого вопроса опроса.",
        )
    )


@admin_required
@error_handler
async def process_poll_question_text(
    message: types.Message,
    db_user: User,
    state: FSMContext,
):
    question_text = (message.html_text or message.text or "").strip()
    texts = get_texts(db_user.language)

    if not question_text:
        await message.answer(
            texts.t(
                "ADMIN_POLLS_ENTER_QUESTION_RETRY",
                "❗️ Текст вопроса не может быть пустым. Отправьте вопрос ещё раз.",
            )
        )
        return

    await state.update_data(current_question_text=question_text)
    await state.set_state(AdminStates.creating_poll_question_options)
    await message.answer(
        texts.t(
            "ADMIN_POLLS_ENTER_OPTIONS",
            "🔢 Отправьте варианты ответов, каждый с новой строки (минимум 2, максимум 10).",
        )
    )


@admin_required
@error_handler
async def process_poll_question_options(
    message: types.Message,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    raw_options = (message.text or "").splitlines()
    options = [opt.strip() for opt in raw_options if opt.strip()]

    if len(options) < 2:
        await message.answer(
            texts.t(
                "ADMIN_POLLS_NEED_MORE_OPTIONS",
                "❗️ Укажите минимум два варианта ответа.",
            )
        )
        return

    if len(options) > 10:
        await message.answer(
            texts.t(
                "ADMIN_POLLS_TOO_MANY_OPTIONS",
                "❗️ Максимум 10 вариантов ответа. Отправьте список ещё раз.",
            )
        )
        return

    data = await state.get_data()
    question_text = data.get("current_question_text")
    if not question_text:
        await message.answer(
            texts.t(
                "ADMIN_POLLS_QUESTION_NOT_FOUND",
                "❌ Не удалось найти текст вопроса. Начните заново, выбрав создание вопроса.",
            )
        )
        await state.set_state(AdminStates.creating_poll_question_text)
        return

    questions = _get_state_questions(data)
    questions.append({"text": question_text, "options": options})
    await state.update_data(
        poll_questions=questions,
        current_question_text=None,
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_ADD_QUESTION", "➕ Добавить ещё вопрос"),
                    callback_data="admin_poll_add_question",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_CONFIGURE_REWARD", "🎁 Настроить награду"),
                    callback_data="admin_poll_reward_menu",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_CANCEL", "❌ Отмена"),
                    callback_data="admin_polls",
                )
            ],
        ]
    )

    await state.set_state(None)
    await message.answer(
        texts.t(
            "ADMIN_POLLS_QUESTION_ADDED",
            "✅ Вопрос добавлен. Выберите действие:",
        ),
        reply_markup=keyboard,
    )


@admin_required
@error_handler
async def add_another_question(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await state.set_state(AdminStates.creating_poll_question_text)
    await callback.message.edit_text(
        texts.t(
            "ADMIN_POLLS_ENTER_QUESTION_NEXT",
            "❓ Отправьте текст следующего вопроса.",
        )
    )
    await callback.answer()


def _build_reward_menu(texts, data: dict) -> tuple[str, types.InlineKeyboardMarkup]:
    reward_enabled = bool(data.get("reward_enabled"))
    reward_amount = int(data.get("reward_amount_kopeks") or 0)
    questions = _get_state_questions(data)

    questions_summary = "\n".join(
        f"{idx}. {html.escape(q['text'])}" for idx, q in enumerate(questions, start=1)
    ) or texts.t("ADMIN_POLLS_NO_QUESTIONS", "— вопросы не добавлены —")

    reward_text = (
        texts.t("ADMIN_POLLS_REWARD_ON", "Включена")
        if reward_enabled and reward_amount > 0
        else texts.t("ADMIN_POLLS_REWARD_OFF", "Отключена")
    )
    reward_amount_label = texts.format_price(reward_amount)

    text = (
        texts.t("ADMIN_POLLS_REWARD_TITLE", "🎁 <b>Награда за участие</b>")
        + "\n\n"
        + texts.t("ADMIN_POLLS_REWARD_STATUS", "Статус: <b>{status}</b>" ).format(status=reward_text)
        + "\n"
        + texts.t(
            "ADMIN_POLLS_REWARD_AMOUNT",
            "Сумма: <b>{amount}</b>",
        ).format(amount=reward_amount_label)
        + "\n\n"
        + texts.t("ADMIN_POLLS_REWARD_QUESTIONS", "Всего вопросов: {count}").format(count=len(questions))
        + "\n"
        + questions_summary
    )

    toggle_text = (
        texts.t("ADMIN_POLLS_REWARD_DISABLE", "🚫 Отключить награду")
        if reward_enabled
        else texts.t("ADMIN_POLLS_REWARD_ENABLE", "🔔 Включить награду")
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=toggle_text,
                    callback_data="admin_poll_toggle_reward",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_REWARD_SET_AMOUNT", "💰 Изменить сумму"),
                    callback_data="admin_poll_reward_amount",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_SAVE", "✅ Сохранить опрос"),
                    callback_data="admin_poll_save",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_ADD_MORE", "➕ Добавить ещё вопрос"),
                    callback_data="admin_poll_add_question",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_CANCEL", "❌ Отмена"),
                    callback_data="admin_polls",
                )
            ],
        ]
    )

    return text, keyboard


@admin_required
@error_handler
async def show_reward_menu(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    data = await state.get_data()
    texts = get_texts(db_user.language)
    try:
        _ensure_questions_present(_get_state_questions(data))
    except ValueError:
        await callback.answer(
            texts.t(
                "ADMIN_POLLS_NEED_QUESTION_FIRST",
                "Добавьте хотя бы один вопрос перед настройкой награды.",
            ),
            show_alert=True,
        )
        return

    text, keyboard = _build_reward_menu(texts, data)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def toggle_reward(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    data = await state.get_data()
    reward_enabled = bool(data.get("reward_enabled"))
    reward_amount = int(data.get("reward_amount_kopeks") or 0)

    reward_enabled = not reward_enabled
    if reward_enabled and reward_amount <= 0:
        reward_amount = 1000

    await state.update_data(
        reward_enabled=reward_enabled,
        reward_amount_kopeks=reward_amount,
    )

    texts = get_texts(db_user.language)
    text, keyboard = _build_reward_menu(texts, await state.get_data())
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def request_reward_amount(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await state.set_state(AdminStates.creating_poll_reward_amount)
    await callback.message.edit_text(
        texts.t(
            "ADMIN_POLLS_REWARD_AMOUNT_PROMPT",
            "💰 Введите сумму награды в рублях (можно с копейками).",
        )
    )
    await callback.answer()


@admin_required
@error_handler
async def process_reward_amount(
    message: types.Message,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    raw_value = (message.text or "").replace(",", ".").strip()

    try:
        value_decimal = Decimal(raw_value)
    except (InvalidOperation, ValueError):
        await message.answer(
            texts.t(
                "ADMIN_POLLS_REWARD_AMOUNT_INVALID",
                "❗️ Не удалось распознать число. Введите сумму в формате 10 или 12.5",
            )
        )
        return

    if value_decimal < 0:
        await message.answer(
            texts.t(
                "ADMIN_POLLS_REWARD_AMOUNT_NEGATIVE",
                "❗️ Сумма не может быть отрицательной.",
            )
        )
        return

    amount_kopeks = int((value_decimal * 100).to_integral_value())
    await state.update_data(
        reward_amount_kopeks=amount_kopeks,
        reward_enabled=amount_kopeks > 0,
    )
    await state.set_state(None)

    data = await state.get_data()
    text, keyboard = _build_reward_menu(texts, data)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@admin_required
@error_handler
async def save_poll(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    texts = get_texts(db_user.language)

    try:
        _ensure_questions_present(_get_state_questions(data))
    except ValueError:
        await callback.answer(
            texts.t(
                "ADMIN_POLLS_NEED_QUESTION_FIRST",
                "Добавьте хотя бы один вопрос перед сохранением.",
            ),
            show_alert=True,
        )
        return

    title = data.get("poll_title")
    description = data.get("poll_description")
    questions = _get_state_questions(data)
    reward_enabled = bool(data.get("reward_enabled"))
    reward_amount = int(data.get("reward_amount_kopeks") or 0)

    if not title or not description:
        await callback.answer(
            texts.t(
                "ADMIN_POLLS_MISSING_DATA",
                "Заполните заголовок и описание перед сохранением.",
            ),
            show_alert=True,
        )
        return

    poll = Poll(
        title=title,
        description=description,
        reward_enabled=reward_enabled and reward_amount > 0,
        reward_amount_kopeks=reward_amount if reward_amount > 0 else 0,
        created_by=db_user.id,
        created_at=datetime.utcnow(),
    )

    try:
        db.add(poll)
        await db.flush()

        for q_index, question_data in enumerate(questions, start=1):
            question = PollQuestion(
                poll_id=poll.id,
                text=question_data["text"],
                order=q_index,
            )
            db.add(question)
            await db.flush()

            for opt_index, option_text in enumerate(question_data["options"], start=1):
                option = PollOption(
                    question_id=question.id,
                    text=option_text,
                    order=opt_index,
                )
                db.add(option)

        await db.commit()
        await state.clear()

        poll = await _get_poll(db, poll.id)
        question_lines = [
            _format_question_summary(idx, question)
            for idx, question in enumerate(poll.questions, start=1)
        ]
        reward_info = (
            texts.t(
                "ADMIN_POLLS_REWARD_SUMMARY",
                "🎁 Награда: {amount}",
            ).format(amount=texts.format_price(poll.reward_amount_kopeks))
            if poll.reward_enabled and poll.reward_amount_kopeks > 0
            else texts.t("ADMIN_POLLS_REWARD_SUMMARY_NONE", "🎁 Награда: не выдается")
        )

        summary_text = (
            texts.t("ADMIN_POLLS_CREATED", "✅ Опрос сохранён!")
            + "\n\n"
            + f"<b>{html.escape(poll.title)}</b>\n"
            + texts.t("ADMIN_POLLS_QUESTIONS_COUNT", "Вопросов: {count}").format(count=len(poll.questions))
            + "\n"
            + reward_info
            + "\n\n"
            + "\n".join(question_lines)
        )

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t("ADMIN_POLLS_OPEN", "📋 К опросу"),
                        callback_data=f"admin_poll_{poll.id}",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=texts.BACK,
                        callback_data="admin_polls",
                    )
                ],
            ]
        )

        await callback.message.edit_text(
            summary_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await callback.answer()

    except Exception as exc:  # pragma: no cover - defensive logging
        await db.rollback()
        logger.exception("Failed to create poll: %s", exc)
        await callback.answer(
            texts.t(
                "ADMIN_POLLS_SAVE_ERROR",
                "❌ Не удалось сохранить опрос. Попробуйте ещё раз позже.",
            ),
            show_alert=True,
        )


@admin_required
@error_handler
async def show_poll_details(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    try:
        poll_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    poll = await _get_poll(db, poll_id)
    if not poll:
        await callback.answer(
            texts.t("ADMIN_POLLS_NOT_FOUND", "Опрос не найден или был удалён."),
            show_alert=True,
        )
        return

    question_lines = [
        _format_question_summary(idx, question)
        for idx, question in enumerate(poll.questions, start=1)
    ]

    runs_total = sum(run.sent_count for run in poll.runs)
    completions = await db.scalar(
        select(func.count(PollResponse.id)).where(
            PollResponse.poll_id == poll.id,
            PollResponse.is_completed.is_(True),
        )
    ) or 0

    reward_info = (
        texts.t(
            "ADMIN_POLLS_REWARD_SUMMARY",
            "🎁 Награда: {amount}",
        ).format(amount=texts.format_price(poll.reward_amount_kopeks))
        if poll.reward_enabled and poll.reward_amount_kopeks > 0
        else texts.t("ADMIN_POLLS_REWARD_SUMMARY_NONE", "🎁 Награда: не выдается")
    )

    description_preview = html.escape(poll.description)

    text = (
        f"📋 <b>{html.escape(poll.title)}</b>\n\n"
        + texts.t("ADMIN_POLLS_DESCRIPTION_LABEL", "Описание:")
        + f"\n{description_preview}\n\n"
        + texts.t(
            "ADMIN_POLLS_STATS_SENT",
            "Отправлено сообщений: <b>{count}</b>",
        ).format(count=runs_total)
        + "\n"
        + texts.t(
            "ADMIN_POLLS_STATS_COMPLETED",
            "Завершили опрос: <b>{count}</b>",
        ).format(count=completions)
        + "\n"
        + reward_info
        + "\n\n"
        + texts.t("ADMIN_POLLS_QUESTIONS_LIST", "Вопросы:")
        + "\n"
        + ("\n".join(question_lines) if question_lines else texts.t("ADMIN_POLLS_NO_QUESTIONS", "— вопросы не добавлены —"))
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_SEND", "🚀 Отправить"),
                    callback_data=f"admin_poll_send_{poll.id}",
                ),
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_STATS_BUTTON", "📊 Статистика"),
                    callback_data=f"admin_poll_stats_{poll.id}",
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_DELETE", "🗑️ Удалить"),
                    callback_data=f"admin_poll_delete_{poll.id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.BACK,
                    callback_data="admin_polls",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def show_poll_target_selection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    try:
        poll_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    poll = await _get_poll(db, poll_id)
    if not poll or not poll.questions:
        await callback.answer(
            texts.t(
                "ADMIN_POLLS_NO_QUESTIONS",
                "Сначала добавьте вопросы к опросу, чтобы отправлять его пользователям.",
            ),
            show_alert=True,
        )
        return

    from app.keyboards.admin import get_poll_target_keyboard

    await callback.message.edit_text(
        texts.t(
            "ADMIN_POLLS_SELECT_TARGET",
            "🎯 Выберите категорию пользователей для отправки опроса.",
        ),
        reply_markup=get_poll_target_keyboard(poll.id, db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def preview_poll_target(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    try:
        _, _, poll_id_str, target = callback.data.split("_", 3)
        poll_id = int(poll_id_str)
    except (ValueError, IndexError):
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    poll = await _get_poll(db, poll_id)
    if not poll:
        await callback.answer(
            texts.t("ADMIN_POLLS_NOT_FOUND", "Опрос не найден."),
            show_alert=True,
        )
        return

    users = await get_target_users(db, target)
    target_name = get_target_name(target)

    confirm_keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_CONFIRM_SEND", "✅ Отправить"),
                    callback_data=f"admin_poll_send_confirm_{poll_id}_{target}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.BACK,
                    callback_data=f"admin_poll_send_{poll_id}",
                )
            ],
        ]
    )

    text = (
        texts.t("ADMIN_POLLS_CONFIRMATION_TITLE", "📨 Подтверждение отправки")
        + "\n\n"
        + texts.t(
            "ADMIN_POLLS_CONFIRMATION_BODY",
            "Категория: <b>{category}</b>\nПользователей: <b>{count}</b>",
        ).format(category=target_name, count=len(users))
        + "\n\n"
        + texts.t(
            "ADMIN_POLLS_CONFIRMATION_HINT",
            "После отправки пользователи получат приглашение пройти опрос.",
        )
    )

    await callback.message.edit_text(
        text,
        reply_markup=confirm_keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


async def _send_poll_invitation(
    bot: types.Bot,
    poll: Poll,
    run: PollRun,
    users: list,
) -> tuple[int, int]:
    sent_count = 0
    failed_count = 0

    invite_text = (
        f"📋 <b>{html.escape(poll.title)}</b>\n\n"
        f"{poll.description}\n\n"
        "📝 Нажмите кнопку ниже, чтобы пройти опрос."
    )

    for index, user in enumerate(users, start=1):
        try:
            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="📝 Пройти опрос",
                            callback_data=f"poll_start_{poll.id}_{run.id}",
                        )
                    ]
                ]
            )
            await bot.send_message(
                chat_id=user.telegram_id,
                text=invite_text,
                reply_markup=keyboard,
            )
            sent_count += 1
        except Exception as exc:  # pragma: no cover - defensive logging
            failed_count += 1
            logger.warning(
                "Failed to send poll %s to user %s: %s",
                poll.id,
                getattr(user, "telegram_id", "unknown"),
                exc,
            )
        if index % 25 == 0:
            await asyncio.sleep(0.5)

    return sent_count, failed_count


@admin_required
@error_handler
async def confirm_poll_sending(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    parts = callback.data.split("_")
    if len(parts) < 6:
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    try:
        poll_id = int(parts[4])
    except ValueError:
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    target = "_".join(parts[5:])

    poll = await _get_poll(db, poll_id)
    if not poll:
        await callback.answer(
            texts.t("ADMIN_POLLS_NOT_FOUND", "Опрос не найден."),
            show_alert=True,
        )
        return

    users = await get_target_users(db, target)
    if not users:
        await callback.answer(
            texts.t(
                "ADMIN_POLLS_NO_USERS",
                "Подходящих пользователей не найдено для выбранной категории.",
            ),
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        texts.t("ADMIN_POLLS_SENDING", "📨 Отправляем опрос..."),
    )

    run = PollRun(
        poll_id=poll.id,
        target_type=target,
        status="in_progress",
        total_count=len(users),
        created_by=db_user.id,
        created_at=datetime.utcnow(),
        started_at=datetime.utcnow(),
    )
    db.add(run)
    await db.flush()

    sent_count, failed_count = await _send_poll_invitation(callback.bot, poll, run, users)

    run.sent_count = sent_count
    run.failed_count = failed_count
    run.status = "completed"
    run.completed_at = datetime.utcnow()

    await db.commit()

    result_text = (
        texts.t("ADMIN_POLLS_SENT", "✅ Отправка завершена!")
        + "\n\n"
        + texts.t("ADMIN_POLLS_SENT_SUCCESS", "Успешно отправлено: <b>{count}</b>").format(count=sent_count)
        + "\n"
        + texts.t("ADMIN_POLLS_SENT_FAILED", "Ошибок доставки: <b>{count}</b>").format(count=failed_count)
        + "\n"
        + texts.t("ADMIN_POLLS_SENT_TOTAL", "Всего пользователей: <b>{count}</b>").format(count=len(users))
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_STATS_BUTTON", "📊 Статистика"),
                    callback_data=f"admin_poll_stats_{poll.id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.BACK,
                    callback_data=f"admin_poll_{poll.id}",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        result_text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def show_poll_stats(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    try:
        poll_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    poll = await _get_poll(db, poll_id)
    if not poll:
        await callback.answer(
            texts.t("ADMIN_POLLS_NOT_FOUND", "Опрос не найден."),
            show_alert=True,
        )
        return

    total_responses = await db.scalar(
        select(func.count(PollResponse.id)).where(PollResponse.poll_id == poll.id)
    ) or 0
    completed_responses = await db.scalar(
        select(func.count(PollResponse.id)).where(
            PollResponse.poll_id == poll.id,
            PollResponse.is_completed.is_(True),
        )
    ) or 0
    reward_sum = await db.scalar(
        select(func.coalesce(func.sum(PollResponse.reward_amount_kopeks), 0)).where(
            PollResponse.poll_id == poll.id,
            PollResponse.reward_given.is_(True),
        )
    ) or 0

    runs_total = await db.scalar(
        select(func.coalesce(func.sum(PollRun.sent_count), 0)).where(PollRun.poll_id == poll.id)
    ) or 0

    answers_stmt = (
        select(PollAnswer.question_id, PollAnswer.option_id, func.count(PollAnswer.id))
        .join(PollResponse, PollResponse.id == PollAnswer.response_id)
        .where(PollResponse.poll_id == poll.id)
        .group_by(PollAnswer.question_id, PollAnswer.option_id)
    )
    answers_result = await db.execute(answers_stmt)
    answer_counts = {
        (question_id, option_id): count
        for question_id, option_id, count in answers_result.all()
    }

    question_lines = []
    for question in sorted(poll.questions, key=lambda q: q.order):
        total_answers_for_question = sum(
            answer_counts.get((question.id, option.id), 0)
            for option in question.options
        ) or 0
        question_lines.append(f"<b>{html.escape(question.text)}</b>")
        for option in sorted(question.options, key=lambda o: o.order):
            option_count = answer_counts.get((question.id, option.id), 0)
            percent = (
                round(option_count / total_answers_for_question * 100, 1)
                if total_answers_for_question
                else 0
            )
            question_lines.append(
                texts.t(
                    "ADMIN_POLLS_STATS_OPTION",
                    "• {text} — {count} ({percent}%)",
                ).format(
                    text=html.escape(option.text),
                    count=option_count,
                    percent=percent,
                )
            )
        question_lines.append("")

    text = (
        texts.t("ADMIN_POLLS_STATS_TITLE", "📊 Статистика опроса")
        + "\n\n"
        + f"<b>{html.escape(poll.title)}</b>\n"
        + texts.t("ADMIN_POLLS_STATS_SENT", "Сообщений отправлено: <b>{count}</b>").format(count=runs_total)
        + "\n"
        + texts.t(
            "ADMIN_POLLS_STATS_RESPONDED",
            "Ответов получено: <b>{count}</b>",
        ).format(count=total_responses)
        + "\n"
        + texts.t(
            "ADMIN_POLLS_STATS_COMPLETED_LABEL",
            "Прошли до конца: <b>{count}</b>",
        ).format(count=completed_responses)
        + "\n"
        + texts.t(
            "ADMIN_POLLS_STATS_REWARD_TOTAL",
            "Выдано наград: <b>{amount}</b>",
        ).format(amount=texts.format_price(reward_sum))
        + "\n\n"
        + ("\n".join(question_lines).strip() or texts.t("ADMIN_POLLS_STATS_NO_DATA", "Ответов пока нет."))
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.BACK,
                    callback_data=f"admin_poll_{poll.id}",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def ask_delete_poll(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    try:
        poll_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    poll = await _get_poll(db, poll_id)
    if not poll:
        await callback.answer(
            texts.t("ADMIN_POLLS_NOT_FOUND", "Опрос не найден."),
            show_alert=True,
        )
        return

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_POLLS_DELETE_CONFIRM", "🗑️ Удалить"),
                    callback_data=f"admin_poll_delete_confirm_{poll.id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.BACK,
                    callback_data=f"admin_poll_{poll.id}",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        texts.t(
            "ADMIN_POLLS_DELETE_PROMPT",
            "❓ Удалить опрос? Это действие нельзя отменить.",
        ),
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_poll(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    try:
        poll_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    poll = await db.get(Poll, poll_id)
    if not poll:
        await callback.answer(
            texts.t("ADMIN_POLLS_NOT_FOUND", "Опрос уже удалён."),
            show_alert=True,
        )
        return

    await db.delete(poll)
    await db.commit()

    await callback.message.edit_text(
        texts.t("ADMIN_POLLS_DELETED", "🗑️ Опрос удалён."),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t("ADMIN_POLLS_BACK_TO_LIST", "⬅️ К списку опросов"),
                        callback_data="admin_polls",
                    )
                ]
            ]
        ),
    )
    await callback.answer()


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(show_polls_menu, F.data == "admin_polls")
    dp.callback_query.register(start_poll_creation, F.data == "admin_poll_create")
    dp.message.register(process_poll_title, AdminStates.creating_poll_title)
    dp.message.register(process_poll_description, AdminStates.creating_poll_description)
    dp.message.register(process_poll_question_text, AdminStates.creating_poll_question_text)
    dp.message.register(process_poll_question_options, AdminStates.creating_poll_question_options)
    dp.message.register(process_reward_amount, AdminStates.creating_poll_reward_amount)

    dp.callback_query.register(add_another_question, F.data == "admin_poll_add_question")
    dp.callback_query.register(show_reward_menu, F.data == "admin_poll_reward_menu")
    dp.callback_query.register(toggle_reward, F.data == "admin_poll_toggle_reward")
    dp.callback_query.register(request_reward_amount, F.data == "admin_poll_reward_amount")
    dp.callback_query.register(save_poll, F.data == "admin_poll_save")

    dp.callback_query.register(
        show_poll_details,
        F.data.regexp(r"^admin_poll_(?!send_|stats_|delete_|create).+"),
    )
    dp.callback_query.register(
        show_poll_target_selection,
        F.data.regexp(r"^admin_poll_send_\\d+$"),
    )
    dp.callback_query.register(preview_poll_target, F.data.startswith("poll_target_"))
    dp.callback_query.register(
        confirm_poll_sending,
        F.data.regexp(r"^admin_poll_send_confirm_\\d+_.+"),
    )
    dp.callback_query.register(
        show_poll_stats,
        F.data.regexp(r"^admin_poll_stats_\\d+$"),
    )
    dp.callback_query.register(
        ask_delete_poll,
        F.data.regexp(r"^admin_poll_delete_\\d+$"),
    )
    dp.callback_query.register(
        delete_poll,
        F.data.regexp(r"^admin_poll_delete_confirm_\\d+$"),
    )
