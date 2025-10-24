import logging
from typing import Iterable, Sequence

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    Poll,
    PollAnswer,
    PollOption,
    PollQuestion,
    PollResponse,
)

logger = logging.getLogger(__name__)


async def create_poll(
    db: AsyncSession,
    *,
    title: str,
    description: str | None,
    reward_enabled: bool,
    reward_amount_kopeks: int,
    created_by: int | None,
    questions: Sequence[dict[str, Iterable[str]]],
) -> Poll:
    poll = Poll(
        title=title,
        description=description,
        reward_enabled=reward_enabled,
        reward_amount_kopeks=reward_amount_kopeks if reward_enabled else 0,
        created_by=created_by,
    )
    db.add(poll)
    await db.flush()

    for order, question_data in enumerate(questions, start=1):
        question_text = question_data.get("text", "").strip()
        if not question_text:
            continue

        question = PollQuestion(
            poll_id=poll.id,
            text=question_text,
            order=order,
        )
        db.add(question)
        await db.flush()

        for option_order, option_text in enumerate(question_data.get("options", []), start=1):
            option_text = option_text.strip()
            if not option_text:
                continue
            option = PollOption(
                question_id=question.id,
                text=option_text,
                order=option_order,
            )
            db.add(option)

    await db.commit()
    await db.refresh(
        poll,
        attribute_names=["questions"],
    )
    return poll


async def list_polls(db: AsyncSession) -> list[Poll]:
    result = await db.execute(
        select(Poll)
        .options(
            selectinload(Poll.questions).options(selectinload(PollQuestion.options))
        )
        .order_by(Poll.created_at.desc())
    )
    return result.scalars().all()


async def get_poll_by_id(db: AsyncSession, poll_id: int) -> Poll | None:
    result = await db.execute(
        select(Poll)
        .options(
            selectinload(Poll.questions).options(selectinload(PollQuestion.options)),
            selectinload(Poll.responses),
        )
        .where(Poll.id == poll_id)
    )
    return result.scalar_one_or_none()


async def delete_poll(db: AsyncSession, poll_id: int) -> bool:
    poll = await db.get(Poll, poll_id)
    if not poll:
        return False

    await db.delete(poll)
    await db.commit()
    logger.info("🗑️ Удалён опрос %s", poll_id)
    return True


async def create_poll_response(
    db: AsyncSession,
    poll_id: int,
    user_id: int,
) -> PollResponse:
    result = await db.execute(
        select(PollResponse)
        .where(
            and_(
                PollResponse.poll_id == poll_id,
                PollResponse.user_id == user_id,
            )
        )
    )
    response = result.scalar_one_or_none()
    if response:
        return response

    response = PollResponse(
        poll_id=poll_id,
        user_id=user_id,
    )
    db.add(response)
    await db.commit()
    await db.refresh(response)
    return response


async def get_poll_response_by_id(
    db: AsyncSession,
    response_id: int,
) -> PollResponse | None:
    result = await db.execute(
        select(PollResponse)
        .options(
            selectinload(PollResponse.poll)
            .options(selectinload(Poll.questions).options(selectinload(PollQuestion.options))),
            selectinload(PollResponse.answers),
            selectinload(PollResponse.user),
        )
        .where(PollResponse.id == response_id)
    )
    return result.scalar_one_or_none()


async def record_poll_answer(
    db: AsyncSession,
    *,
    response_id: int,
    question_id: int,
    option_id: int,
) -> PollAnswer:
    result = await db.execute(
        select(PollAnswer)
        .where(
            and_(
                PollAnswer.response_id == response_id,
                PollAnswer.question_id == question_id,
            )
        )
    )
    answer = result.scalar_one_or_none()
    if answer:
        answer.option_id = option_id
        await db.commit()
        await db.refresh(answer)
        return answer

    answer = PollAnswer(
        response_id=response_id,
        question_id=question_id,
        option_id=option_id,
    )
    db.add(answer)
    await db.commit()
    await db.refresh(answer)
    return answer


async def reset_poll_answers(db: AsyncSession, response_id: int) -> None:
    await db.execute(
        delete(PollAnswer).where(PollAnswer.response_id == response_id)
    )
    await db.commit()


async def get_poll_statistics(db: AsyncSession, poll_id: int) -> dict:
    totals_result = await db.execute(
        select(
            func.count(PollResponse.id),
            func.count(PollResponse.completed_at),
            func.coalesce(func.sum(PollResponse.reward_amount_kopeks), 0),
        ).where(PollResponse.poll_id == poll_id)
    )
    total_responses, completed_responses, reward_sum = totals_result.one()

    option_counts_result = await db.execute(
        select(
            PollQuestion.id,
            PollQuestion.text,
            PollQuestion.order,
            PollOption.id,
            PollOption.text,
            PollOption.order,
            func.count(PollAnswer.id),
        )
        .join(PollOption, PollOption.question_id == PollQuestion.id)
        .outerjoin(
            PollAnswer,
            and_(
                PollAnswer.question_id == PollQuestion.id,
                PollAnswer.option_id == PollOption.id,
            ),
        )
        .where(PollQuestion.poll_id == poll_id)
        .group_by(
            PollQuestion.id,
            PollQuestion.text,
            PollQuestion.order,
            PollOption.id,
            PollOption.text,
            PollOption.order,
        )
        .order_by(PollQuestion.order.asc(), PollOption.order.asc())
    )

    questions_map: dict[int, dict] = {}
    for (
        question_id,
        question_text,
        question_order,
        option_id,
        option_text,
        option_order,
        answer_count,
    ) in option_counts_result:
        question_entry = questions_map.setdefault(
            question_id,
            {
                "id": question_id,
                "text": question_text,
                "order": question_order,
                "options": [],
            },
        )
        question_entry["options"].append(
            {
                "id": option_id,
                "text": option_text,
                "count": answer_count,
            }
        )

    questions = sorted(questions_map.values(), key=lambda item: item["order"])

    return {
        "total_responses": total_responses,
        "completed_responses": completed_responses,
        "reward_sum_kopeks": reward_sum,
        "questions": questions,
    }


async def get_poll_responses_with_answers(
    db: AsyncSession,
    poll_id: int,
    *,
    limit: int,
    offset: int,
) -> tuple[list[PollResponse], int]:
    total_result = await db.execute(
        select(func.count()).select_from(PollResponse).where(PollResponse.poll_id == poll_id)
    )
    total = int(total_result.scalar_one() or 0)

    if total == 0:
        return [], 0

    result = await db.execute(
        select(PollResponse)
        .options(
            selectinload(PollResponse.user),
            selectinload(PollResponse.answers).selectinload(PollAnswer.question),
            selectinload(PollResponse.answers).selectinload(PollAnswer.option),
        )
        .where(PollResponse.poll_id == poll_id)
        .order_by(PollResponse.sent_at.asc())
        .offset(offset)
        .limit(limit)
    )

    responses = result.scalars().unique().all()
    return responses, total
