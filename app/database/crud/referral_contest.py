import logging
from datetime import datetime, date, time, timezone
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, aliased

from app.database.models import (
    ReferralContest,
    ReferralContestEvent,
    User,
    Transaction,
    TransactionType,
)

logger = logging.getLogger(__name__)


async def create_referral_contest(
    db: AsyncSession,
    *,
    title: str,
    description: Optional[str],
    prize_text: Optional[str],
    contest_type: str,
    start_at: datetime,
    end_at: datetime,
    daily_summary_time: time,
    daily_summary_times: Optional[str] = None,
    timezone_name: str,
    created_by: Optional[int] = None,
) -> ReferralContest:
    contest = ReferralContest(
        title=title,
        description=description,
        prize_text=prize_text,
        contest_type=contest_type,
        start_at=start_at,
        end_at=end_at,
        daily_summary_time=daily_summary_time,
        daily_summary_times=daily_summary_times,
        timezone=timezone_name or "UTC",
        created_by=created_by,
    )
    db.add(contest)
    await db.commit()
    await db.refresh(contest)
    return contest


async def list_referral_contests(
    db: AsyncSession,
    *,
    limit: int = 10,
    offset: int = 0,
    contest_type: Optional[str] = None,
) -> List[ReferralContest]:
    query = (
        select(ReferralContest)
        .options(selectinload(ReferralContest.creator))
        .order_by(desc(ReferralContest.start_at))
        .offset(offset)
        .limit(limit)
    )
    if contest_type:
        query = query.where(ReferralContest.contest_type == contest_type)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_referral_contests_count(db: AsyncSession, contest_type: Optional[str] = None) -> int:
    query = select(func.count(ReferralContest.id))
    if contest_type:
        query = query.where(ReferralContest.contest_type == contest_type)
    result = await db.execute(query)
    return int(result.scalar_one())


async def get_referral_contest(db: AsyncSession, contest_id: int) -> Optional[ReferralContest]:
    result = await db.execute(
        select(ReferralContest)
        .options(
            selectinload(ReferralContest.creator),
            selectinload(ReferralContest.events),
        )
        .where(ReferralContest.id == contest_id)
    )
    return result.scalar_one_or_none()


async def update_referral_contest(
    db: AsyncSession,
    contest: ReferralContest,
    **fields: object,
) -> ReferralContest:
    for key, value in fields.items():
        if hasattr(contest, key):
            setattr(contest, key, value)
    await db.commit()
    await db.refresh(contest)
    return contest


async def toggle_referral_contest(
    db: AsyncSession,
    contest: ReferralContest,
    is_active: bool,
) -> ReferralContest:
    contest.is_active = is_active
    return await update_referral_contest(db, contest)


async def get_contests_for_events(
    db: AsyncSession,
    now_utc: datetime,
    *,
    contest_types: Optional[List[str]] = None,
) -> List[ReferralContest]:
    query = select(ReferralContest).where(
        and_(
            ReferralContest.is_active.is_(True),
            ReferralContest.start_at <= now_utc,
            ReferralContest.end_at >= now_utc,
        )
    )
    if contest_types:
        query = query.where(ReferralContest.contest_type.in_(contest_types))

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_contests_for_summaries(db: AsyncSession) -> List[ReferralContest]:
    result = await db.execute(
        select(ReferralContest).where(ReferralContest.is_active.is_(True))
    )
    return list(result.scalars().all())


async def add_contest_event(
    db: AsyncSession,
    *,
    contest_id: int,
    referrer_id: int,
    referral_id: int,
    amount_kopeks: int = 0,
    event_type: str = "subscription_purchase",
) -> Optional[ReferralContestEvent]:
    existing = await db.execute(
        select(ReferralContestEvent).where(
            and_(
                ReferralContestEvent.contest_id == contest_id,
                ReferralContestEvent.referral_id == referral_id,
            )
        )
    )
    if existing.scalar_one_or_none():
        return None

    event = ReferralContestEvent(
        contest_id=contest_id,
        referrer_id=referrer_id,
        referral_id=referral_id,
        amount_kopeks=amount_kopeks,
        event_type=event_type,
        occurred_at=datetime.utcnow(),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def get_contest_leaderboard(
    db: AsyncSession,
    contest_id: int,
    *,
    limit: Optional[int] = None,
) -> Sequence[Tuple[User, int, int]]:
    contest = await get_referral_contest(db, contest_id)
    if not contest:
        return []

    query = (
        select(
            User,
            func.count(ReferralContestEvent.id).label("referral_count"),
            func.coalesce(func.sum(ReferralContestEvent.amount_kopeks), 0).label("total_amount"),
        )
        .join(User, User.id == ReferralContestEvent.referrer_id)
        .where(ReferralContestEvent.contest_id == contest_id)
        .group_by(User.id)
        .order_by(desc("referral_count"), desc("total_amount"), User.id)
    )
    if limit:
        query = query.limit(limit)
    result = await db.execute(query)
    leaderboard = result.all()

    return leaderboard


async def get_contest_participants(
    db: AsyncSession,
    contest_id: int,
) -> Sequence[Tuple[User, int]]:
    result = await db.execute(
        select(User, func.count(ReferralContestEvent.id).label("referral_count"))
        .join(User, User.id == ReferralContestEvent.referrer_id)
        .where(ReferralContestEvent.contest_id == contest_id)
        .group_by(User.id)
    )
    return result.all()


async def get_referrer_score(
    db: AsyncSession,
    contest_id: int,
    referrer_id: int,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> int:
    query = select(func.count(ReferralContestEvent.id)).where(
        and_(
            ReferralContestEvent.contest_id == contest_id,
            ReferralContestEvent.referrer_id == referrer_id,
        )
    )
    if start:
        query = query.where(ReferralContestEvent.occurred_at >= start)
    if end:
        query = query.where(ReferralContestEvent.occurred_at < end)

    result = await db.execute(query)
    return int(result.scalar_one())


async def get_contest_events_count(
    db: AsyncSession,
    contest_id: int,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> int:
    query = select(func.count(ReferralContestEvent.id)).where(
        ReferralContestEvent.contest_id == contest_id
    )
    if start:
        query = query.where(ReferralContestEvent.occurred_at >= start)
    if end:
        query = query.where(ReferralContestEvent.occurred_at < end)
    result = await db.execute(query)
    return int(result.scalar_one())


async def get_contest_events(
    db: AsyncSession,
    contest_id: int,
) -> List[ReferralContestEvent]:
    result = await db.execute(
        select(ReferralContestEvent).where(ReferralContestEvent.contest_id == contest_id)
    )
    return list(result.scalars().all())


async def mark_daily_summary_sent(
    db: AsyncSession,
    contest: ReferralContest,
    summary_date: date,
    summary_dt_utc: Optional[datetime] = None,
) -> ReferralContest:
    contest.last_daily_summary_date = summary_date
    if summary_dt_utc:
        contest.last_daily_summary_at = summary_dt_utc
    await db.commit()
    await db.refresh(contest)
    return contest


async def mark_final_summary_sent(
    db: AsyncSession,
    contest: ReferralContest,
) -> ReferralContest:
    contest.final_summary_sent = True
    contest.is_active = False
    await db.commit()
    await db.refresh(contest)
    return contest


async def delete_referral_contest(
    db: AsyncSession,
    contest: ReferralContest,
) -> None:
    await db.delete(contest)
    await db.commit()
