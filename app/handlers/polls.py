import asyncio
import html
import logging
from datetime import datetime

from aiogram import Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.crud.user import add_user_balance
from app.database.models import (
    Poll,
    PollAnswer,
    PollOption,
    PollQuestion,
    PollResponse,
    PollRun,
    User,
)
from app.localization.texts import get_texts
from app.utils.decorators import error_handler

logger = logging.getLogger(__name__)


async def _get_poll_with_questions(db: AsyncSession, poll_id: int) -> Poll | None:
    stmt = (
        select(Poll)
        .options(selectinload(Poll.questions).selectinload(PollQuestion.options))
        .where(Poll.id == poll_id)
    )
    result = await db.execute(stmt)
    return result.unique().scalar_one_or_none()


async def _get_response(
    db: AsyncSession,
    poll_id: int,
    user_id: int,
) -> PollResponse | None:
    stmt = (
        select(PollResponse)
        .options(selectinload(PollResponse.answers))
        .where(PollResponse.poll_id == poll_id, PollResponse.user_id == user_id)
    )
    result = await db.execute(stmt)
    return result.unique().scalar_one_or_none()


def _get_next_question(
    poll: Poll,
    response: PollResponse,
) -> PollQuestion | None:
    questions = sorted(poll.questions, key=lambda q: q.order)
    answered_ids = {answer.question_id for answer in response.answers}
    for question in questions:
        if question.id not in answered_ids:
            return question
    return None


async def _delete_message_after_delay(bot: types.Bot, chat_id: int, message_id: int) -> None:
    await asyncio.sleep(10)
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to delete poll message %s: %s", message_id, exc)


def _build_question_text(
    poll: Poll,
    question: PollQuestion,
    question_index: int,
    total_questions: int,
    include_description: bool,
) -> str:
    header = f"📋 <b>{html.escape(poll.title)}</b>\n\n"
    body = ""
    if include_description:
        body += f"{poll.description}\n\n"
    body += (
        f"❓ <b>{question_index}/{total_questions}</b>\n"
        f"{html.escape(question.text)}"
    )
    return header + body


def _build_question_keyboard(response_id: int, question: PollQuestion) -> types.InlineKeyboardMarkup:
    buttons = []
    for option in sorted(question.options, key=lambda o: o.order):
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text=option.text,
                    callback_data=f"poll_answer_{response_id}_{question.id}_{option.id}",
                )
            ]
        )
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


@error_handler
async def start_poll(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    try:
        _, poll_id_str, run_id_str = callback.data.split("_", 2)
        poll_id = int(poll_id_str)
        run_id = int(run_id_str)
    except (ValueError, IndexError):
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    poll = await _get_poll_with_questions(db, poll_id)
    if not poll or not poll.questions:
        await callback.answer(
            texts.t("POLL_NOT_AVAILABLE", "Опрос недоступен."),
            show_alert=True,
        )
        return

    response = await _get_response(db, poll.id, db_user.id)
    if response and response.is_completed:
        await callback.answer(
            texts.t("POLL_ALREADY_PASSED", "Вы уже участвовали в этом опросе."),
            show_alert=True,
        )
        return

    if not response:
        response = PollResponse(
            poll_id=poll.id,
            run_id=run_id,
            user_id=db_user.id,
            message_id=callback.message.message_id,
            chat_id=callback.message.chat.id,
            created_at=datetime.utcnow(),
        )
        db.add(response)
        await db.flush()
    else:
        response.message_id = callback.message.message_id
        response.chat_id = callback.message.chat.id
        await db.flush()

    next_question = _get_next_question(poll, response)
    if not next_question:
        await callback.answer(
            texts.t("POLL_NO_QUESTIONS", "Вопросы опроса не найдены."),
            show_alert=True,
        )
        return

    response.current_question_id = next_question.id
    await db.commit()

    question_index = sorted(poll.questions, key=lambda q: q.order).index(next_question) + 1
    total_questions = len(poll.questions)
    include_description = len(response.answers) == 0
    text = _build_question_text(poll, next_question, question_index, total_questions, include_description)
    keyboard = _build_question_keyboard(response.id, next_question)

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        new_message = None
    except TelegramBadRequest:
        new_message = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    if new_message:
        response.message_id = new_message.message_id
        response.chat_id = new_message.chat.id
        await db.commit()
    await callback.answer()


@error_handler
async def answer_poll(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    try:
        _, response_id_str, question_id_str, option_id_str = callback.data.split("_", 3)
        response_id = int(response_id_str)
        question_id = int(question_id_str)
        option_id = int(option_id_str)
    except (ValueError, IndexError):
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    response = await db.get(PollResponse, response_id)
    if not response or response.user_id != db_user.id:
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    if response.is_completed:
        await callback.answer(
            texts.t("POLL_ALREADY_PASSED", "Вы уже участвовали в этом опросе."),
            show_alert=True,
        )
        return

    poll = await _get_poll_with_questions(db, response.poll_id)
    if not poll:
        await callback.answer(texts.t("POLL_NOT_AVAILABLE", "Опрос недоступен."), show_alert=True)
        return

    question = next((q for q in poll.questions if q.id == question_id), None)
    if not question:
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    option = next((opt for opt in question.options if opt.id == option_id), None)
    if not option:
        await callback.answer(texts.UNKNOWN_ERROR, show_alert=True)
        return

    existing_answer = next((ans for ans in response.answers if ans.question_id == question_id), None)
    if existing_answer:
        await callback.answer(texts.t("POLL_OPTION_ALREADY_CHOSEN", "Ответ уже выбран."))
        return

    answer = PollAnswer(
        response_id=response.id,
        question_id=question.id,
        option_id=option.id,
    )
    db.add(answer)
    await db.flush()

    response.answers.append(answer)

    next_question = _get_next_question(poll, response)

    if next_question:
        response.current_question_id = next_question.id
        await db.commit()

        question_index = sorted(poll.questions, key=lambda q: q.order).index(next_question) + 1
        total_questions = len(poll.questions)
        include_description = False
        text = _build_question_text(poll, next_question, question_index, total_questions, include_description)
        keyboard = _build_question_keyboard(response.id, next_question)

        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            new_message = None
        except TelegramBadRequest:
            new_message = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        if new_message:
            response.message_id = new_message.message_id
            response.chat_id = new_message.chat.id
            await db.commit()
        await callback.answer()
        return

    # Completed
    response.current_question_id = None
    response.is_completed = True
    response.completed_at = datetime.utcnow()

    reward_text = ""
    if poll.reward_enabled and poll.reward_amount_kopeks > 0 and not response.reward_given:
        success = await add_user_balance(
            db,
            db_user,
            poll.reward_amount_kopeks,
            description=texts.t(
                "POLL_REWARD_DESCRIPTION",
                "Награда за участие в опросе '{title}'",
            ).format(title=poll.title),
        )
        if success:
            response.reward_given = True
            response.reward_amount_kopeks = poll.reward_amount_kopeks
            reward_text = texts.t(
                "POLL_REWARD_RECEIVED",
                "\n\n🎁 На баланс зачислено: {amount}",
            ).format(amount=texts.format_price(poll.reward_amount_kopeks))
        else:
            logger.warning("Failed to add reward for poll %s to user %s", poll.id, db_user.telegram_id)

    if response.run_id:
        run = await db.get(PollRun, response.run_id)
        if run:
            run.completed_count = (run.completed_count or 0) + 1

    await db.commit()

    thank_you_text = (
        texts.t("POLL_COMPLETED", "🙏 Спасибо за участие в опросе!")
        + reward_text
    )

    try:
        await callback.message.edit_text(thank_you_text)
        new_message = None
    except TelegramBadRequest:
        new_message = await callback.message.answer(thank_you_text)
    if new_message:
        response.message_id = new_message.message_id
        response.chat_id = new_message.chat.id
        await db.commit()

    if response.chat_id and response.message_id:
        asyncio.create_task(
            _delete_message_after_delay(callback.bot, response.chat_id, response.message_id)
        )

    await callback.answer()


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(start_poll, F.data.startswith("poll_start_"))
    dp.callback_query.register(answer_poll, F.data.startswith("poll_answer_"))
