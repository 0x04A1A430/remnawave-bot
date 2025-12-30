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
    """Получить лидерборд конкурса.

    Учитывает только рефералов, зарегистрированных В ПЕРИОД конкурса.
    """
    contest = await get_referral_contest(db, contest_id)
    if not contest:
        return []

    # Нормализуем границы дат
    contest_start = contest.start_at
    contest_end = contest.end_at
    if contest_end.hour == 0 and contest_end.minute == 0 and contest_end.second == 0:
        contest_end = contest_end.replace(hour=23, minute=59, second=59, microsecond=999999)

    query = (
        select(
            User,
            func.count(ReferralContestEvent.id).label("referral_count"),
            func.coalesce(func.sum(ReferralContestEvent.amount_kopeks), 0).label("total_amount"),
        )
        .join(User, User.id == ReferralContestEvent.referrer_id)
        .where(
            and_(
                ReferralContestEvent.contest_id == contest_id,
                ReferralContestEvent.occurred_at >= contest_start,
                ReferralContestEvent.occurred_at <= contest_end,
            )
        )
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
    """Получить участников конкурса.

    Учитывает только рефералов, зарегистрированных В ПЕРИОД конкурса.
    """
    contest = await get_referral_contest(db, contest_id)
    if not contest:
        return []

    contest_start = contest.start_at
    contest_end = contest.end_at
    if contest_end.hour == 0 and contest_end.minute == 0 and contest_end.second == 0:
        contest_end = contest_end.replace(hour=23, minute=59, second=59, microsecond=999999)

    result = await db.execute(
        select(User, func.count(ReferralContestEvent.id).label("referral_count"))
        .join(User, User.id == ReferralContestEvent.referrer_id)
        .where(
            and_(
                ReferralContestEvent.contest_id == contest_id,
                ReferralContestEvent.occurred_at >= contest_start,
                ReferralContestEvent.occurred_at <= contest_end,
            )
        )
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


async def get_contest_payment_stats(
    db: AsyncSession,
    contest_id: int,
) -> dict:
    """Получить статистику оплат по конкурсу.

    Учитывает только рефералов, зарегистрированных В ПЕРИОД конкурса.

    Returns:
        dict: {
            "paid_count": int,    # Рефералов с платежами > 0
            "unpaid_count": int,  # Рефералов без платежей
            "total_amount": int,  # Общая сумма платежей
        }
    """
    # Получаем даты конкурса для фильтрации
    contest = await get_referral_contest(db, contest_id)
    if not contest:
        return {"paid_count": 0, "unpaid_count": 0, "total_amount": 0}

    contest_start = contest.start_at
    contest_end = contest.end_at
    if contest_end.hour == 0 and contest_end.minute == 0 and contest_end.second == 0:
        contest_end = contest_end.replace(hour=23, minute=59, second=59, microsecond=999999)

    # Считаем рефералов с платежами (только зарегистрированных в период конкурса)
    paid_result = await db.execute(
        select(func.count(ReferralContestEvent.id))
        .where(
            and_(
                ReferralContestEvent.contest_id == contest_id,
                ReferralContestEvent.amount_kopeks > 0,
                ReferralContestEvent.occurred_at >= contest_start,
                ReferralContestEvent.occurred_at <= contest_end,
            )
        )
    )
    paid_count = int(paid_result.scalar_one() or 0)

    # Считаем рефералов без платежей (только зарегистрированных в период конкурса)
    unpaid_result = await db.execute(
        select(func.count(ReferralContestEvent.id))
        .where(
            and_(
                ReferralContestEvent.contest_id == contest_id,
                ReferralContestEvent.amount_kopeks == 0,
                ReferralContestEvent.occurred_at >= contest_start,
                ReferralContestEvent.occurred_at <= contest_end,
            )
        )
    )
    unpaid_count = int(unpaid_result.scalar_one() or 0)

    # Общая сумма (только за рефералов зарегистрированных в период конкурса)
    total_result = await db.execute(
        select(func.coalesce(func.sum(ReferralContestEvent.amount_kopeks), 0))
        .where(
            and_(
                ReferralContestEvent.contest_id == contest_id,
                ReferralContestEvent.occurred_at >= contest_start,
                ReferralContestEvent.occurred_at <= contest_end,
            )
        )
    )
    total_amount = int(total_result.scalar_one() or 0)

    return {
        "paid_count": paid_count,
        "unpaid_count": unpaid_count,
        "total_amount": total_amount,
    }


async def get_contest_transaction_breakdown(
    db: AsyncSession,
    contest_id: int,
) -> dict:
    """Получить разбивку транзакций по типам для конкурса.

    Учитывает только рефералов, зарегистрированных В ПЕРИОД конкурса.

    Returns:
        dict: {
            "subscription_total": int,  # Сумма покупок подписок (копейки)
            "deposit_total": int,       # Сумма пополнений баланса (копейки)
        }
    """
    contest = await get_referral_contest(db, contest_id)
    if not contest:
        return {"subscription_total": 0, "deposit_total": 0}

    contest_start = contest.start_at
    contest_end = contest.end_at
    if contest_end.hour == 0 and contest_end.minute == 0 and contest_end.second == 0:
        contest_end = contest_end.replace(hour=23, minute=59, second=59, microsecond=999999)

    # Получаем referral_id только из событий в период конкурса
    events_result = await db.execute(
        select(ReferralContestEvent.referral_id)
        .where(
            and_(
                ReferralContestEvent.contest_id == contest_id,
                ReferralContestEvent.occurred_at >= contest_start,
                ReferralContestEvent.occurred_at <= contest_end,
            )
        )
    )
    referral_ids = [r[0] for r in events_result.fetchall()]

    if not referral_ids:
        return {"subscription_total": 0, "deposit_total": 0}

    # Сумма покупок подписок
    subscription_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(
            and_(
                Transaction.user_id.in_(referral_ids),
                Transaction.is_completed.is_(True),
                Transaction.type == TransactionType.SUBSCRIPTION_PAYMENT.value,
                Transaction.created_at >= contest_start,
                Transaction.created_at <= contest_end,
            )
        )
    )
    subscription_total = int(subscription_result.scalar_one() or 0)

    # Сумма пополнений баланса
    deposit_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(
            and_(
                Transaction.user_id.in_(referral_ids),
                Transaction.is_completed.is_(True),
                Transaction.type == TransactionType.DEPOSIT.value,
                Transaction.created_at >= contest_start,
                Transaction.created_at <= contest_end,
            )
        )
    )
    deposit_total = int(deposit_result.scalar_one() or 0)

    return {
        "subscription_total": subscription_total,
        "deposit_total": deposit_total,
    }


async def upsert_contest_event(
    db: AsyncSession,
    *,
    contest_id: int,
    referrer_id: int,
    referral_id: int,
    amount_kopeks: int = 0,
    event_type: str = "subscription_purchase",
) -> Tuple[ReferralContestEvent, bool]:
    """Создать или обновить событие конкурса.

    Returns:
        Tuple[ReferralContestEvent, bool]: (событие, создано_новое)
    """
    result = await db.execute(
        select(ReferralContestEvent).where(
            and_(
                ReferralContestEvent.contest_id == contest_id,
                ReferralContestEvent.referral_id == referral_id,
            )
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Обновляем сумму если она изменилась
        if existing.amount_kopeks != amount_kopeks:
            existing.amount_kopeks = amount_kopeks
            await db.commit()
            await db.refresh(existing)
        return existing, False

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
    return event, True


async def debug_contest_transactions(
    db: AsyncSession,
    contest_id: int,
    limit: int = 20,
) -> dict:
    """Показать транзакции которые учитываются в конкурсе для отладки.

    Возвращает информацию о транзакциях рефералов конкурса,
    чтобы понять какие именно платежи считаются.
    """
    contest = await get_referral_contest(db, contest_id)
    if not contest:
        return {"error": "Contest not found"}

    # Нормализуем границы дат
    contest_start = contest.start_at
    contest_end = contest.end_at

    if contest_end.hour == 0 and contest_end.minute == 0 and contest_end.second == 0:
        contest_end = contest_end.replace(hour=23, minute=59, second=59, microsecond=999999)

    # Получаем referral_id ТОЛЬКО из событий которые произошли в период конкурса
    events_result = await db.execute(
        select(ReferralContestEvent.referral_id)
        .where(
            and_(
                ReferralContestEvent.contest_id == contest_id,
                ReferralContestEvent.occurred_at >= contest_start,
                ReferralContestEvent.occurred_at <= contest_end,
            )
        )
    )
    referral_ids = [r[0] for r in events_result.fetchall()]

    # Также считаем сколько всего событий для сравнения
    all_events_result = await db.execute(
        select(func.count(ReferralContestEvent.id))
        .where(ReferralContestEvent.contest_id == contest_id)
    )
    total_all_events = int(all_events_result.scalar_one() or 0)

    if not referral_ids:
        return {
            "contest_start": contest_start.isoformat(),
            "contest_end": contest_end.isoformat(),
            "referral_count": 0,
            "total_all_events": total_all_events,
            "transactions": [],
        }

    # Получаем транзакции этих рефералов ЗА период конкурса
    transactions_in_period = await db.execute(
        select(Transaction)
        .where(
            and_(
                Transaction.user_id.in_(referral_ids),
                Transaction.is_completed.is_(True),
                Transaction.type.in_([
                    TransactionType.DEPOSIT.value,
                    TransactionType.SUBSCRIPTION_PAYMENT.value,
                ]),
                Transaction.created_at >= contest_start,
                Transaction.created_at <= contest_end,
            )
        )
        .order_by(desc(Transaction.created_at))
        .limit(limit)
    )
    txs_in = transactions_in_period.scalars().all()

    # Также получаем транзакции ВНЕ периода для сравнения
    transactions_outside = await db.execute(
        select(Transaction)
        .where(
            and_(
                Transaction.user_id.in_(referral_ids),
                Transaction.is_completed.is_(True),
                Transaction.type.in_([
                    TransactionType.DEPOSIT.value,
                    TransactionType.SUBSCRIPTION_PAYMENT.value,
                ]),
                func.not_(
                    and_(
                        Transaction.created_at >= contest_start,
                        Transaction.created_at <= contest_end,
                    )
                ),
            )
        )
        .order_by(desc(Transaction.created_at))
        .limit(limit)
    )
    txs_out = transactions_outside.scalars().all()

    # Подсчёт общих сумм ПО ТИПАМ
    deposit_in_period = sum(tx.amount_kopeks for tx in txs_in if tx.type == TransactionType.DEPOSIT.value)
    subscription_in_period = sum(tx.amount_kopeks for tx in txs_in if tx.type == TransactionType.SUBSCRIPTION_PAYMENT.value)
    total_in_period = deposit_in_period + subscription_in_period
    total_outside = sum(tx.amount_kopeks for tx in txs_out)

    # Подсчёт ПОЛНЫХ сумм (не только sample)
    full_deposit_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(
            and_(
                Transaction.user_id.in_(referral_ids),
                Transaction.is_completed.is_(True),
                Transaction.type == TransactionType.DEPOSIT.value,
                Transaction.created_at >= contest_start,
                Transaction.created_at <= contest_end,
            )
        )
    )
    full_deposit_total = int(full_deposit_result.scalar_one() or 0)

    full_subscription_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
        .where(
            and_(
                Transaction.user_id.in_(referral_ids),
                Transaction.is_completed.is_(True),
                Transaction.type == TransactionType.SUBSCRIPTION_PAYMENT.value,
                Transaction.created_at >= contest_start,
                Transaction.created_at <= contest_end,
            )
        )
    )
    full_subscription_total = int(full_subscription_result.scalar_one() or 0)

    return {
        "contest_start": contest_start.isoformat(),
        "contest_end": contest_end.isoformat(),
        "referral_count": len(referral_ids),
        "total_all_events": total_all_events,
        "filtered_out": total_all_events - len(referral_ids),
        "transactions_in_period": [
            {
                "id": tx.id,
                "user_id": tx.user_id,
                "type": tx.type,
                "amount_kopeks": tx.amount_kopeks,
                "created_at": tx.created_at.isoformat() if tx.created_at else None,
                "payment_method": tx.payment_method,
            }
            for tx in txs_in
        ],
        "transactions_outside_period": [
            {
                "id": tx.id,
                "user_id": tx.user_id,
                "type": tx.type,
                "amount_kopeks": tx.amount_kopeks,
                "created_at": tx.created_at.isoformat() if tx.created_at else None,
                "payment_method": tx.payment_method,
            }
            for tx in txs_out
        ],
        "total_in_period_kopeks": total_in_period,
        "total_outside_period_kopeks": total_outside,
        "deposit_total_kopeks": full_deposit_total,
        "subscription_total_kopeks": full_subscription_total,
        "sample_size": limit,
    }


async def sync_contest_events(
    db: AsyncSession,
    contest_id: int,
) -> dict:
    """Синхронизировать события конкурса с реальными данными.

    Обновляет ВСЕ существующие события конкурса, пересчитывая платежи
    каждого реферала СТРОГО за период конкурса (start_at - end_at).

    Returns:
        dict: {
            "updated": int,  # Событий обновлено
            "skipped": int,  # Пропущено (нет изменений)
            "total_events": int,  # Всего событий проверено
            "total_amount": int,  # Общая сумма платежей
            "paid_count": int,  # Рефералов с платежами
            "unpaid_count": int,  # Рефералов без платежей
        }
    """
    contest = await get_referral_contest(db, contest_id)
    if not contest:
        return {"error": "Contest not found"}

    # Нормализуем границы дат для СТРОГОЙ фильтрации
    # start_at должен быть началом дня (00:00:00)
    # end_at должен быть концом дня (23:59:59.999999)
    contest_start = contest.start_at
    contest_end = contest.end_at

    # Если start_at содержит только дату (время 00:00), оставляем как есть
    # Если end_at содержит только дату, добавляем время до конца дня
    if contest_end.hour == 0 and contest_end.minute == 0 and contest_end.second == 0:
        # Конец дня: 23:59:59.999999
        contest_end = contest_end.replace(hour=23, minute=59, second=59, microsecond=999999)

    logger.info(
        "Синхронизация конкурса %s: период с %s по %s",
        contest_id, contest_start, contest_end
    )

    stats = {
        "updated": 0,
        "skipped": 0,
        "total_events": 0,
        "total_amount": 0,
        "paid_count": 0,
        "unpaid_count": 0,
        "contest_start": contest_start.isoformat(),
        "contest_end": contest_end.isoformat(),
    }

    # Получаем события конкурса ТОЛЬКО те, что произошли в период конкурса
    # (реферал зарегистрировался в период проведения конкурса)
    events_result = await db.execute(
        select(ReferralContestEvent)
        .where(
            and_(
                ReferralContestEvent.contest_id == contest_id,
                ReferralContestEvent.occurred_at >= contest_start,
                ReferralContestEvent.occurred_at <= contest_end,
            )
        )
    )
    events = events_result.scalars().all()

    # Также считаем сколько всего событий (для отладки)
    all_events_result = await db.execute(
        select(func.count(ReferralContestEvent.id))
        .where(ReferralContestEvent.contest_id == contest_id)
    )
    total_all_events = int(all_events_result.scalar_one() or 0)

    stats["total_events"] = len(events)
    stats["total_all_events"] = total_all_events
    stats["filtered_out_events"] = total_all_events - len(events)

    stats["deposit_total"] = 0
    stats["subscription_total"] = 0

    for event in events:
        # Считаем ТОЛЬКО покупки подписок (реальные траты на подписки)
        subscription_query = (
            select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
            .where(
                and_(
                    Transaction.user_id == event.referral_id,
                    Transaction.is_completed.is_(True),
                    Transaction.type == TransactionType.SUBSCRIPTION_PAYMENT.value,
                    Transaction.created_at >= contest_start,
                    Transaction.created_at <= contest_end,
                )
            )
        )
        sub_result = await db.execute(subscription_query)
        subscription_paid = int(sub_result.scalar_one() or 0)

        # Также считаем пополнения баланса (для информации)
        deposit_query = (
            select(func.coalesce(func.sum(Transaction.amount_kopeks), 0))
            .where(
                and_(
                    Transaction.user_id == event.referral_id,
                    Transaction.is_completed.is_(True),
                    Transaction.type == TransactionType.DEPOSIT.value,
                    Transaction.created_at >= contest_start,
                    Transaction.created_at <= contest_end,
                )
            )
        )
        dep_result = await db.execute(deposit_query)
        deposit_paid = int(dep_result.scalar_one() or 0)

        stats["subscription_total"] += subscription_paid
        stats["deposit_total"] += deposit_paid

        # Основная метрика — покупки подписок
        total_paid = subscription_paid

        # Считаем статистику
        if total_paid > 0:
            stats["total_amount"] += total_paid
            stats["paid_count"] += 1
        else:
            stats["unpaid_count"] += 1

        # Обновляем сумму если изменилась
        if event.amount_kopeks != total_paid:
            old_amount = event.amount_kopeks
            event.amount_kopeks = total_paid
            stats["updated"] += 1
            # Логируем значительные изменения
            if abs(old_amount - total_paid) > 10000:  # больше 100 руб разницы
                logger.debug(
                    "Событие %s (реферал %s): %s -> %s коп.",
                    event.id, event.referral_id, old_amount, total_paid
                )
        else:
            stats["skipped"] += 1

    # Сохраняем изменения
    await db.commit()

    logger.info(
        "Синхронизация конкурса %s завершена: обновлено %s, пропущено %s, сумма %s коп.",
        contest_id, stats["updated"], stats["skipped"], stats["total_amount"]
    )

    return stats
