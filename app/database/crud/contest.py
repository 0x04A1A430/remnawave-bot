import logging
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import ContestTemplate, ContestRound, ContestAttempt, User

logger = logging.getLogger(__name__)


# Templates
async def get_template_by_id(db: AsyncSession, template_id: int) -> Optional[ContestTemplate]:
    result = await db.execute(
        select(ContestTemplate).where(ContestTemplate.id == template_id)
    )
    return result.scalar_one_or_none()


async def get_template_by_slug(db: AsyncSession, slug: str) -> Optional[ContestTemplate]:
    result = await db.execute(
        select(ContestTemplate).where(ContestTemplate.slug == slug)
    )
    return result.scalar_one_or_none()


async def list_templates(db: AsyncSession, enabled_only: bool = True) -> List[ContestTemplate]:
    query = select(ContestTemplate).order_by(ContestTemplate.id)
    if enabled_only:
        query = query.where(ContestTemplate.is_enabled.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


async def upsert_template(
    db: AsyncSession,
    *,
    slug: str,
    name: str,
    description: str = "",
    prize_type: str = "days",
    prize_value: str = "1",
    max_winners: int = 1,
    attempts_per_user: int = 1,
    times_per_day: int = 1,
    schedule_times: Optional[str] = None,
    cooldown_hours: int = 24,
    payload: Optional[dict] = None,
    is_enabled: Optional[bool] = None,
) -> ContestTemplate:
    template = await get_template_by_slug(db, slug)
    if not template:
        template = ContestTemplate(slug=slug)
        db.add(template)

    template.name = name
    template.description = description
    template.prize_type = prize_type
    template.prize_value = prize_value
    template.max_winners = max_winners
    template.attempts_per_user = attempts_per_user
    template.times_per_day = times_per_day
    template.schedule_times = schedule_times
    template.cooldown_hours = cooldown_hours
    template.payload = payload or {}
    if is_enabled is not None:
        template.is_enabled = is_enabled
    await db.commit()
    await db.refresh(template)
    return template


async def update_template_fields(
    db: AsyncSession,
    template: ContestTemplate,
    **fields: object,
) -> ContestTemplate:
    for key, value in fields.items():
        if hasattr(template, key):
            setattr(template, key, value)
    await db.commit()
    await db.refresh(template)
    return template


# Rounds
async def create_round(
    db: AsyncSession,
    *,
    template: ContestTemplate,
    starts_at: datetime,
    ends_at: datetime,
    payload: dict,
) -> ContestRound:
    round_obj = ContestRound(
        template_id=template.id,
        starts_at=starts_at,
        ends_at=ends_at,
        status="active",
        payload=payload,
        max_winners=template.max_winners,
        attempts_per_user=template.attempts_per_user,
    )
    db.add(round_obj)
    await db.commit()
    await db.refresh(round_obj)
    return round_obj


async def get_active_rounds(db: AsyncSession) -> List[ContestRound]:
    now = datetime.utcnow()
    result = await db.execute(
        select(ContestRound)
        .options(selectinload(ContestRound.template))
        .where(
            and_(
                ContestRound.status == "active",
                ContestRound.starts_at <= now,
                ContestRound.ends_at >= now,
            )
        )
        .order_by(ContestRound.starts_at)
    )
    return list(result.scalars().all())


async def get_active_round_by_template(db: AsyncSession, template_id: int) -> Optional[ContestRound]:
    now = datetime.utcnow()
    result = await db.execute(
        select(ContestRound)
        .options(selectinload(ContestRound.template))
        .where(
            and_(
                ContestRound.template_id == template_id,
                ContestRound.status == "active",
                ContestRound.starts_at <= now,
                ContestRound.ends_at >= now,
            )
        )
        .order_by(desc(ContestRound.starts_at))
    )
    return result.scalars().first()


async def finish_round(db: AsyncSession, round_obj: ContestRound) -> ContestRound:
    round_obj.status = "finished"
    await db.commit()
    await db.refresh(round_obj)
    return round_obj


async def increment_winner_count(db: AsyncSession, round_obj: ContestRound) -> ContestRound:
    round_obj.winners_count += 1
    await db.commit()
    await db.refresh(round_obj)
    return round_obj


# Attempts
async def get_attempt(db: AsyncSession, round_id: int, user_id: int) -> Optional[ContestAttempt]:
    result = await db.execute(
        select(ContestAttempt).where(
            and_(
                ContestAttempt.round_id == round_id,
                ContestAttempt.user_id == user_id,
            )
        )
    )
    return result.scalar_one_or_none()


async def create_attempt(
    db: AsyncSession,
    *,
    round_id: int,
    user_id: int,
    answer: Optional[str],
    is_winner: bool,
) -> ContestAttempt:
    attempt = ContestAttempt(
        round_id=round_id,
        user_id=user_id,
        answer=answer,
        is_winner=is_winner,
    )
    db.add(attempt)
    await db.commit()
    await db.refresh(attempt)
    return attempt


async def update_attempt(
    db: AsyncSession,
    attempt: ContestAttempt,
    *,
    answer: Optional[str] = None,
    is_winner: bool = False,
) -> ContestAttempt:
    """Update existing attempt with answer and winner status."""
    if answer is not None:
        attempt.answer = answer
    attempt.is_winner = is_winner
    await db.commit()
    await db.refresh(attempt)
    return attempt


async def clear_attempts(db: AsyncSession, round_id: int) -> int:
    result = await db.execute(delete(ContestAttempt).where(ContestAttempt.round_id == round_id))
    deleted_count = result.rowcount
    await db.commit()
    return deleted_count


async def list_winners(db: AsyncSession, round_id: int) -> Sequence[Tuple[User, ContestAttempt]]:
    result = await db.execute(
        select(User, ContestAttempt)
        .join(ContestAttempt, ContestAttempt.user_id == User.id)
        .where(
            and_(
                ContestAttempt.round_id == round_id,
                ContestAttempt.is_winner.is_(True),
            )
        )
    )
    return result.all()
