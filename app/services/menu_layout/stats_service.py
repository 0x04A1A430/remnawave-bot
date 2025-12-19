"""Сервис статистики кликов по кнопкам меню."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ButtonClickLog


class MenuLayoutStatsService:
    """Сервис для сбора и анализа статистики кликов по кнопкам."""

    @classmethod
    async def log_button_click(
        cls,
        db: AsyncSession,
        button_id: str,
        user_id: Optional[int] = None,
        callback_data: Optional[str] = None,
        button_type: Optional[str] = None,
        button_text: Optional[str] = None,
    ) -> ButtonClickLog:
        """Записать клик по кнопке."""
        click_log = ButtonClickLog(
            button_id=button_id,
            user_id=user_id,
            callback_data=callback_data,
            button_type=button_type,
            button_text=button_text,
        )
        db.add(click_log)
        await db.commit()
        return click_log

    @classmethod
    async def get_button_stats(
        cls,
        db: AsyncSession,
        button_id: str,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Получить статистику кликов по конкретной кнопке."""
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=days)

        # Общее количество кликов
        total_result = await db.execute(
            select(func.count(ButtonClickLog.id))
            .where(ButtonClickLog.button_id == button_id)
        )
        clicks_total = total_result.scalar() or 0

        # Клики сегодня
        today_result = await db.execute(
            select(func.count(ButtonClickLog.id))
            .where(and_(
                ButtonClickLog.button_id == button_id,
                ButtonClickLog.clicked_at >= today_start
            ))
        )
        clicks_today = today_result.scalar() or 0

        # Клики за неделю
        week_result = await db.execute(
            select(func.count(ButtonClickLog.id))
            .where(and_(
                ButtonClickLog.button_id == button_id,
                ButtonClickLog.clicked_at >= week_ago
            ))
        )
        clicks_week = week_result.scalar() or 0

        # Клики за месяц
        month_result = await db.execute(
            select(func.count(ButtonClickLog.id))
            .where(and_(
                ButtonClickLog.button_id == button_id,
                ButtonClickLog.clicked_at >= month_ago
            ))
        )
        clicks_month = month_result.scalar() or 0

        # Уникальные пользователи
        unique_result = await db.execute(
            select(func.count(func.distinct(ButtonClickLog.user_id)))
            .where(ButtonClickLog.button_id == button_id)
        )
        unique_users = unique_result.scalar() or 0

        # Последний клик
        last_click_result = await db.execute(
            select(ButtonClickLog.clicked_at)
            .where(ButtonClickLog.button_id == button_id)
            .order_by(desc(ButtonClickLog.clicked_at))
            .limit(1)
        )
        last_click = last_click_result.scalar_one_or_none()

        return {
            "button_id": button_id,
            "clicks_total": clicks_total,
            "clicks_today": clicks_today,
            "clicks_week": clicks_week,
            "clicks_month": clicks_month,
            "unique_users": unique_users,
            "last_click_at": last_click,
        }

    @classmethod
    async def get_button_clicks_by_day(
        cls,
        db: AsyncSession,
        button_id: str,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Получить статистику кликов по дням."""
        start_date = datetime.now() - timedelta(days=days)

        # Группировка по дате
        result = await db.execute(
            select(
                func.date(ButtonClickLog.clicked_at).label("date"),
                func.count(ButtonClickLog.id).label("count")
            )
            .where(and_(
                ButtonClickLog.button_id == button_id,
                ButtonClickLog.clicked_at >= start_date
            ))
            .group_by(func.date(ButtonClickLog.clicked_at))
            .order_by(func.date(ButtonClickLog.clicked_at))
        )

        return [
            {"date": str(row.date), "count": row.count}
            for row in result.all()
        ]

    @classmethod
    async def get_all_buttons_stats(
        cls,
        db: AsyncSession,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Получить статистику по всем кнопкам."""
        start_date = datetime.now() - timedelta(days=days)

        result = await db.execute(
            select(
                ButtonClickLog.button_id,
                func.count(ButtonClickLog.id).label("clicks_total"),
                func.count(func.distinct(ButtonClickLog.user_id)).label("unique_users"),
                func.max(ButtonClickLog.clicked_at).label("last_click_at")
            )
            .where(ButtonClickLog.clicked_at >= start_date)
            .group_by(ButtonClickLog.button_id)
            .order_by(desc(func.count(ButtonClickLog.id)))
        )

        return [
            {
                "button_id": row.button_id,
                "clicks_total": row.clicks_total,
                "unique_users": row.unique_users,
                "last_click_at": row.last_click_at,
            }
            for row in result.all()
        ]

    @classmethod
    async def get_total_clicks(
        cls,
        db: AsyncSession,
        days: int = 30,
    ) -> int:
        """Получить общее количество кликов за период."""
        start_date = datetime.now() - timedelta(days=days)

        result = await db.execute(
            select(func.count(ButtonClickLog.id))
            .where(ButtonClickLog.clicked_at >= start_date)
        )
        return result.scalar() or 0
