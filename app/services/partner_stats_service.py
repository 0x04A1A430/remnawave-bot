"""Сервис расширенной статистики партнёров (рефереров)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ReferralEarning,
    Subscription,
    SubscriptionStatus,
    User,
)

logger = logging.getLogger(__name__)


class PartnerStatsService:
    """Сервис для детальной статистики партнёров."""

    @classmethod
    async def get_referrer_detailed_stats(
        cls,
        db: AsyncSession,
        user_id: int,
    ) -> Dict[str, Any]:
        """Получить детальную статистику реферера."""
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)
        year_ago = now - timedelta(days=365)

        # Базовые данные о рефералах
        referrals_query = select(User).where(User.referred_by_id == user_id)
        referrals_result = await db.execute(referrals_query)
        referrals = referrals_result.scalars().all()
        referral_ids = [r.id for r in referrals]

        total_referrals = len(referrals)

        # Сколько сделали первое пополнение (has_made_first_topup)
        paid_referrals = sum(1 for r in referrals if r.has_made_first_topup)

        # Активные рефералы (с активной подпиской)
        if referral_ids:
            active_result = await db.execute(
                select(func.count(func.distinct(User.id)))
                .join(Subscription, User.id == Subscription.user_id)
                .where(
                    and_(
                        User.id.in_(referral_ids),
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                        Subscription.end_date > now,
                    )
                )
            )
            active_referrals = active_result.scalar() or 0
        else:
            active_referrals = 0

        # Заработки по периодам
        earnings_all_time = await cls._get_earnings_for_period(db, user_id, None)
        earnings_today = await cls._get_earnings_for_period(db, user_id, today_start)
        earnings_week = await cls._get_earnings_for_period(db, user_id, week_ago)
        earnings_month = await cls._get_earnings_for_period(db, user_id, month_ago)
        earnings_year = await cls._get_earnings_for_period(db, user_id, year_ago)

        # Рефералы по периодам
        referrals_today = sum(1 for r in referrals if r.created_at >= today_start)
        referrals_week = sum(1 for r in referrals if r.created_at >= week_ago)
        referrals_month = sum(1 for r in referrals if r.created_at >= month_ago)
        referrals_year = sum(1 for r in referrals if r.created_at >= year_ago)

        # Конверсии
        conversion_to_paid = round((paid_referrals / total_referrals * 100), 2) if total_referrals > 0 else 0
        conversion_to_active = round((active_referrals / total_referrals * 100), 2) if total_referrals > 0 else 0

        # Средний доход с реферала
        avg_earnings_per_referral = round(earnings_all_time / paid_referrals, 2) if paid_referrals > 0 else 0

        return {
            "user_id": user_id,
            "summary": {
                "total_referrals": total_referrals,
                "paid_referrals": paid_referrals,
                "active_referrals": active_referrals,
                "conversion_to_paid_percent": conversion_to_paid,
                "conversion_to_active_percent": conversion_to_active,
                "avg_earnings_per_referral_kopeks": avg_earnings_per_referral,
            },
            "earnings": {
                "all_time_kopeks": earnings_all_time,
                "year_kopeks": earnings_year,
                "month_kopeks": earnings_month,
                "week_kopeks": earnings_week,
                "today_kopeks": earnings_today,
            },
            "referrals_count": {
                "all_time": total_referrals,
                "year": referrals_year,
                "month": referrals_month,
                "week": referrals_week,
                "today": referrals_today,
            },
        }

    @classmethod
    async def get_referrer_daily_stats(
        cls,
        db: AsyncSession,
        user_id: int,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Получить статистику реферера по дням."""
        now = datetime.utcnow()
        start_date = now - timedelta(days=days)

        # Рефералы по дням
        referrals_by_day = await db.execute(
            select(
                func.date(User.created_at).label("date"),
                func.count(User.id).label("referrals_count"),
            )
            .where(
                and_(
                    User.referred_by_id == user_id,
                    User.created_at >= start_date,
                )
            )
            .group_by(func.date(User.created_at))
            .order_by(func.date(User.created_at))
        )
        referrals_dict = {str(row.date): row.referrals_count for row in referrals_by_day.all()}

        # Заработки по дням (из ReferralEarning)
        earnings_by_day = await db.execute(
            select(
                func.date(ReferralEarning.created_at).label("date"),
                func.sum(ReferralEarning.amount_kopeks).label("earnings"),
            )
            .where(
                and_(
                    ReferralEarning.user_id == user_id,
                    ReferralEarning.created_at >= start_date,
                )
            )
            .group_by(func.date(ReferralEarning.created_at))
        )
        earnings_dict = {str(row.date): int(row.earnings or 0) for row in earnings_by_day.all()}

        # Формируем массив за все дни
        result = []
        for i in range(days):
            date = (start_date + timedelta(days=i)).date()
            date_str = str(date)
            result.append({
                "date": date_str,
                "referrals_count": referrals_dict.get(date_str, 0),
                "earnings_kopeks": earnings_dict.get(date_str, 0),
            })

        return result

    @classmethod
    async def get_referrer_top_referrals(
        cls,
        db: AsyncSession,
        user_id: int,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Получить топ рефералов по доходу для реферера."""
        # Получаем рефералов с их доходами
        result = await db.execute(
            select(
                User.id,
                User.telegram_id,
                User.username,
                User.first_name,
                User.last_name,
                User.created_at,
                User.has_made_first_topup,
                func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0).label("total_earnings"),
            )
            .outerjoin(ReferralEarning, ReferralEarning.referral_id == User.id)
            .where(User.referred_by_id == user_id)
            .group_by(User.id)
            .order_by(desc(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0)))
            .limit(limit)
        )

        referrals = []
        now = datetime.utcnow()
        for row in result.all():
            # Проверяем активность подписки
            sub_result = await db.execute(
                select(Subscription)
                .where(
                    and_(
                        Subscription.user_id == row.id,
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                        Subscription.end_date > now,
                    )
                )
                .limit(1)
            )
            is_active = sub_result.scalar_one_or_none() is not None

            referrals.append({
                "id": row.id,
                "telegram_id": row.telegram_id,
                "username": row.username,
                "first_name": row.first_name,
                "last_name": row.last_name,
                "full_name": f"{row.first_name or ''} {row.last_name or ''}".strip() or f"User {row.telegram_id}",
                "created_at": row.created_at,
                "has_made_first_topup": row.has_made_first_topup,
                "is_active": is_active,
                "total_earnings_kopeks": int(row.total_earnings),
            })

        return referrals

    @classmethod
    async def get_referrer_period_comparison(
        cls,
        db: AsyncSession,
        user_id: int,
        current_days: int = 7,
        previous_days: int = 7,
    ) -> Dict[str, Any]:
        """Сравнить текущий и предыдущий период."""
        now = datetime.utcnow()
        current_start = now - timedelta(days=current_days)
        previous_start = current_start - timedelta(days=previous_days)
        previous_end = current_start

        # Рефералы за текущий период
        current_referrals = await db.execute(
            select(func.count(User.id))
            .where(
                and_(
                    User.referred_by_id == user_id,
                    User.created_at >= current_start,
                )
            )
        )
        current_referrals_count = current_referrals.scalar() or 0

        # Рефералы за предыдущий период
        previous_referrals = await db.execute(
            select(func.count(User.id))
            .where(
                and_(
                    User.referred_by_id == user_id,
                    User.created_at >= previous_start,
                    User.created_at < previous_end,
                )
            )
        )
        previous_referrals_count = previous_referrals.scalar() or 0

        # Заработки за текущий период
        current_earnings = await cls._get_earnings_for_period(db, user_id, current_start)

        # Заработки за предыдущий период
        previous_earnings = await cls._get_earnings_for_period(
            db, user_id, previous_start, previous_end
        )

        # Расчёт изменений
        referrals_change = current_referrals_count - previous_referrals_count
        referrals_change_percent = (
            round((referrals_change / previous_referrals_count * 100), 2)
            if previous_referrals_count > 0
            else 0
        )

        earnings_change = current_earnings - previous_earnings
        earnings_change_percent = (
            round((earnings_change / previous_earnings * 100), 2)
            if previous_earnings > 0
            else 0
        )

        return {
            "current_period": {
                "days": current_days,
                "start": current_start.isoformat(),
                "end": now.isoformat(),
                "referrals_count": current_referrals_count,
                "earnings_kopeks": current_earnings,
            },
            "previous_period": {
                "days": previous_days,
                "start": previous_start.isoformat(),
                "end": previous_end.isoformat(),
                "referrals_count": previous_referrals_count,
                "earnings_kopeks": previous_earnings,
            },
            "change": {
                "referrals": {
                    "absolute": referrals_change,
                    "percent": referrals_change_percent,
                    "trend": "up" if referrals_change > 0 else "down" if referrals_change < 0 else "stable",
                },
                "earnings": {
                    "absolute": earnings_change,
                    "percent": earnings_change_percent,
                    "trend": "up" if earnings_change > 0 else "down" if earnings_change < 0 else "stable",
                },
            },
        }

    @classmethod
    async def get_global_partner_stats(
        cls,
        db: AsyncSession,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Глобальная статистика партнёрской программы."""
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)
        year_ago = now - timedelta(days=365)
        start_date = now - timedelta(days=days)

        # Всего рефереров (у кого есть рефералы)
        total_referrers = await db.execute(
            select(func.count(func.distinct(User.referred_by_id)))
            .where(User.referred_by_id.isnot(None))
        )
        total_referrers_count = total_referrers.scalar() or 0

        # Всего рефералов
        total_referrals = await db.execute(
            select(func.count(User.id))
            .where(User.referred_by_id.isnot(None))
        )
        total_referrals_count = total_referrals.scalar() or 0

        # Рефералы которые заплатили
        paid_referrals = await db.execute(
            select(func.count(User.id))
            .where(
                and_(
                    User.referred_by_id.isnot(None),
                    User.has_made_first_topup == True,
                )
            )
        )
        paid_referrals_count = paid_referrals.scalar() or 0

        # Всего выплачено
        total_paid = await cls._get_total_earnings(db, None)
        today_paid = await cls._get_total_earnings(db, today_start)
        week_paid = await cls._get_total_earnings(db, week_ago)
        month_paid = await cls._get_total_earnings(db, month_ago)
        year_paid = await cls._get_total_earnings(db, year_ago)

        # Новые рефералы по периодам
        new_referrals_today = await db.execute(
            select(func.count(User.id))
            .where(
                and_(
                    User.referred_by_id.isnot(None),
                    User.created_at >= today_start,
                )
            )
        )
        new_referrals_week = await db.execute(
            select(func.count(User.id))
            .where(
                and_(
                    User.referred_by_id.isnot(None),
                    User.created_at >= week_ago,
                )
            )
        )
        new_referrals_month = await db.execute(
            select(func.count(User.id))
            .where(
                and_(
                    User.referred_by_id.isnot(None),
                    User.created_at >= month_ago,
                )
            )
        )

        # Конверсия
        conversion_rate = (
            round((paid_referrals_count / total_referrals_count * 100), 2)
            if total_referrals_count > 0
            else 0
        )

        # Средний доход с реферала
        avg_per_referral = (
            round(total_paid / paid_referrals_count, 2)
            if paid_referrals_count > 0
            else 0
        )

        return {
            "summary": {
                "total_referrers": total_referrers_count,
                "total_referrals": total_referrals_count,
                "paid_referrals": paid_referrals_count,
                "conversion_rate_percent": conversion_rate,
                "avg_earnings_per_referral_kopeks": avg_per_referral,
            },
            "payouts": {
                "all_time_kopeks": total_paid,
                "year_kopeks": year_paid,
                "month_kopeks": month_paid,
                "week_kopeks": week_paid,
                "today_kopeks": today_paid,
            },
            "new_referrals": {
                "today": new_referrals_today.scalar() or 0,
                "week": new_referrals_week.scalar() or 0,
                "month": new_referrals_month.scalar() or 0,
            },
        }

    @classmethod
    async def get_global_daily_stats(
        cls,
        db: AsyncSession,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Глобальная статистика по дням."""
        now = datetime.utcnow()
        start_date = now - timedelta(days=days)

        # Рефералы по дням
        referrals_by_day = await db.execute(
            select(
                func.date(User.created_at).label("date"),
                func.count(User.id).label("referrals_count"),
            )
            .where(
                and_(
                    User.referred_by_id.isnot(None),
                    User.created_at >= start_date,
                )
            )
            .group_by(func.date(User.created_at))
        )
        referrals_dict = {str(row.date): row.referrals_count for row in referrals_by_day.all()}

        # Выплаты по дням
        earnings_by_day = await db.execute(
            select(
                func.date(ReferralEarning.created_at).label("date"),
                func.sum(ReferralEarning.amount_kopeks).label("earnings"),
            )
            .where(ReferralEarning.created_at >= start_date)
            .group_by(func.date(ReferralEarning.created_at))
        )
        earnings_dict = {str(row.date): int(row.earnings or 0) for row in earnings_by_day.all()}

        result = []
        for i in range(days):
            date = (start_date + timedelta(days=i)).date()
            date_str = str(date)
            result.append({
                "date": date_str,
                "referrals_count": referrals_dict.get(date_str, 0),
                "earnings_kopeks": earnings_dict.get(date_str, 0),
            })

        return result

    @classmethod
    async def get_top_referrers(
        cls,
        db: AsyncSession,
        limit: int = 10,
        days: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Получить топ рефереров."""
        now = datetime.utcnow()
        start_date = now - timedelta(days=days) if days else None

        # Подсчёт рефералов и заработков
        earnings_query = (
            select(
                ReferralEarning.user_id,
                func.sum(ReferralEarning.amount_kopeks).label("total_earnings"),
            )
            .group_by(ReferralEarning.user_id)
        )
        if start_date:
            earnings_query = earnings_query.where(ReferralEarning.created_at >= start_date)

        earnings_result = await db.execute(earnings_query)
        earnings_dict = {row.user_id: int(row.total_earnings or 0) for row in earnings_result.all()}

        # Подсчёт рефералов
        referrals_query = (
            select(
                User.referred_by_id,
                func.count(User.id).label("referrals_count"),
            )
            .where(User.referred_by_id.isnot(None))
            .group_by(User.referred_by_id)
        )
        if start_date:
            referrals_query = referrals_query.where(User.created_at >= start_date)

        referrals_result = await db.execute(referrals_query)
        referrals_dict = {row.referred_by_id: row.referrals_count for row in referrals_result.all()}

        # Объединяем данные
        all_referrer_ids = set(earnings_dict.keys()) | set(referrals_dict.keys())
        referrers_data = []

        for referrer_id in all_referrer_ids:
            referrers_data.append({
                "user_id": referrer_id,
                "referrals_count": referrals_dict.get(referrer_id, 0),
                "total_earnings": earnings_dict.get(referrer_id, 0),
            })

        # Сортируем по заработку
        referrers_data.sort(key=lambda x: x["total_earnings"], reverse=True)
        top_referrers = referrers_data[:limit]

        # Получаем данные пользователей
        result = []
        for data in top_referrers:
            user_result = await db.execute(
                select(User).where(User.id == data["user_id"])
            )
            user = user_result.scalar_one_or_none()

            if user:
                result.append({
                    "id": user.id,
                    "telegram_id": user.telegram_id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "full_name": f"{user.first_name or ''} {user.last_name or ''}".strip() or f"User {user.telegram_id}",
                    "referral_code": user.referral_code,
                    "referrals_count": data["referrals_count"],
                    "total_earnings_kopeks": data["total_earnings"],
                })

        return result

    @classmethod
    async def _get_earnings_for_period(
        cls,
        db: AsyncSession,
        user_id: int,
        start_date: Optional[datetime],
        end_date: Optional[datetime] = None,
    ) -> int:
        """Получить заработки за период."""
        query = select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0)).where(
            ReferralEarning.user_id == user_id
        )

        if start_date:
            query = query.where(ReferralEarning.created_at >= start_date)
        if end_date:
            query = query.where(ReferralEarning.created_at < end_date)

        result = await db.execute(query)
        return int(result.scalar() or 0)

    @classmethod
    async def _get_total_earnings(
        cls,
        db: AsyncSession,
        start_date: Optional[datetime],
    ) -> int:
        """Получить общие выплаты за период."""
        query = select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0))

        if start_date:
            query = query.where(ReferralEarning.created_at >= start_date)

        result = await db.execute(query)
        return int(result.scalar() or 0)
