import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.models import (
    Subscription,
    SubscriptionStatus,
    Transaction,
    TransactionType,
)
from app.services.admin_notification_service import AdminNotificationService


logger = logging.getLogger(__name__)


class ReportPeriod(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass
class ReportPeriodInfo:
    start_msk: datetime
    end_msk: datetime
    title: str
    caption: str
    range_caption: str
    emoji: str


class ReportService:
    def __init__(self) -> None:
        self.notification_service: Optional[AdminNotificationService] = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._moscow_tz = ZoneInfo("Europe/Moscow")
        self._utc_tz = ZoneInfo("UTC")

    def set_notification_service(self, service: AdminNotificationService) -> None:
        self.notification_service = service

    async def start(self) -> Optional[asyncio.Task]:
        if self._task and not self._task.done():
            return self._task

        if not self.notification_service or not self.notification_service.is_enabled():
            logger.info("Сервис отчетов не запущен: админ-уведомления отключены или не настроен чат")
            return None

        self._stop_event.clear()
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("Сервис отчетов запущен")
        return self._task

    async def stop(self) -> None:
        if not self._task:
            return

        self._stop_event.set()
        try:
            await self._task
        finally:
            self._task = None
            self._stop_event.clear()
            logger.info("Сервис отчетов остановлен")

    async def send_report(self, period: ReportPeriod) -> Tuple[bool, str]:
        text, _ = await self.generate_report(period)

        if not text:
            return False, text

        if not self.notification_service or not self.notification_service.is_enabled():
            logger.warning("Отчет не отправлен: сервис админ-уведомлений недоступен")
            return False, text

        success = await self.notification_service.send_report_message(text)
        if success:
            logger.info("Отчет %s отправлен", period.value)
        else:
            logger.error("Не удалось отправить отчет %s", period.value)
        return success, text

    async def generate_report(self, period: ReportPeriod) -> Tuple[str, dict]:
        info = self._get_period_info(period)
        if not info:
            return "", {}

        start_utc = info.start_msk.astimezone(self._utc_tz).replace(tzinfo=None)
        end_utc = info.end_msk.astimezone(self._utc_tz).replace(tzinfo=None)

        async with AsyncSessionLocal() as session:
            stats = await self._collect_stats(session, start_utc, end_utc)

        text = self._format_report(info, stats)
        return text, stats

    async def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            next_run = self._get_next_run_datetime()
            now_utc = datetime.now(self._utc_tz)
            wait_seconds = max(0, (next_run - now_utc).total_seconds())

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                break
            except asyncio.TimeoutError:
                pass

            if self._stop_event.is_set():
                break

            try:
                await self.send_report(ReportPeriod.DAILY)
            except Exception as error:  # pragma: no cover - defensive logging
                logger.error("Ошибка отправки ежедневного отчета: %s", error, exc_info=True)

    async def _collect_stats(self, session: AsyncSession, start: datetime, end: datetime) -> dict:
        now_utc = datetime.utcnow()

        total_trials_query = select(func.count()).select_from(Subscription).where(
            Subscription.is_trial.is_(True),
            Subscription.end_date > now_utc,
            Subscription.status.in_([
                SubscriptionStatus.ACTIVE.value,
                SubscriptionStatus.TRIAL.value,
            ]),
        )
        total_trials = (await session.scalar(total_trials_query)) or 0

        total_paid_query = select(func.count()).select_from(Subscription).where(
            Subscription.is_trial.is_(False),
            Subscription.end_date > now_utc,
            Subscription.status == SubscriptionStatus.ACTIVE.value,
        )
        total_paid = (await session.scalar(total_paid_query)) or 0

        new_trials_query = select(func.count()).select_from(Subscription).where(
            Subscription.is_trial.is_(True),
            Subscription.start_date >= start,
            Subscription.start_date < end,
        )
        new_trials = (await session.scalar(new_trials_query)) or 0

        new_paid_query = select(func.count()).select_from(Subscription).where(
            Subscription.is_trial.is_(False),
            Subscription.start_date >= start,
            Subscription.start_date < end,
        )
        new_paid = (await session.scalar(new_paid_query)) or 0

        payments_query = select(
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.amount_kopeks), 0),
        ).where(
            Transaction.type == TransactionType.DEPOSIT.value,
            Transaction.is_completed.is_(True),
            Transaction.created_at >= start,
            Transaction.created_at < end,
        )
        payments_count, payments_sum = (await session.execute(payments_query)).one()

        return {
            "total_trials": int(total_trials),
            "total_paid": int(total_paid),
            "new_trials": int(new_trials),
            "new_paid": int(new_paid),
            "payments_count": int(payments_count or 0),
            "payments_sum": int(payments_sum or 0),
            "period_start": start,
            "period_end": end,
        }

    def _format_report(self, info: ReportPeriodInfo, stats: dict) -> str:
        now_msk = datetime.now(self._moscow_tz)
        end_display = info.end_msk - timedelta(seconds=1)
        period_range = (
            f"{info.start_msk.strftime('%d.%m.%Y %H:%M')} — "
            f"{end_display.strftime('%d.%m.%Y %H:%M')}"
        )

        lines = [
            f"{info.emoji} <b>{info.title}</b> ({info.caption})",
            "",
            "🎯 <b>Триалы</b>",
            f"• Активных сейчас: {stats['total_trials']}",
            f"• Новых за период: {stats['new_trials']}",
            "",
            "💎 <b>Платные подписки</b>",
            f"• Активных сейчас: {stats['total_paid']}",
            f"• Новых за период: {stats['new_paid']}",
            "",
            "💳 <b>Пополнения</b>",
            f"• Количество платежей: {stats['payments_count']}",
            f"• Сумма: {settings.format_price(stats['payments_sum'])}",
            "",
            f"🕒 Период (МСК): {period_range}",
            f"📅 Сформировано: {now_msk.strftime('%d.%m.%Y %H:%M')}",
        ]

        return "\n".join(lines)

    def _get_period_info(self, period: ReportPeriod) -> Optional[ReportPeriodInfo]:
        now_msk = datetime.now(self._moscow_tz)

        if period is ReportPeriod.DAILY:
            target_date = now_msk.date() - timedelta(days=1)
            start_msk = datetime.combine(target_date, datetime.min.time(), tzinfo=self._moscow_tz)
            end_msk = start_msk + timedelta(days=1)
            caption = start_msk.strftime('%d.%m.%Y')
            return ReportPeriodInfo(
                start_msk=start_msk,
                end_msk=end_msk,
                title="Ежедневный отчет",
                caption=caption,
                range_caption=caption,
                emoji="🗓️",
            )

        if period is ReportPeriod.WEEKLY:
            end_msk = datetime.combine(now_msk.date(), datetime.min.time(), tzinfo=self._moscow_tz)
            start_msk = end_msk - timedelta(days=7)
            caption = (
                f"{start_msk.strftime('%d.%m.%Y')} — "
                f"{(end_msk - timedelta(days=1)).strftime('%d.%m.%Y')}"
            )
            return ReportPeriodInfo(
                start_msk=start_msk,
                end_msk=end_msk,
                title="Еженедельный отчет",
                caption=caption,
                range_caption=caption,
                emoji="🗓️",
            )

        if period is ReportPeriod.MONTHLY:
            current_month_start = datetime(now_msk.year, now_msk.month, 1, tzinfo=self._moscow_tz)
            end_msk = current_month_start
            previous_month_last_day = current_month_start - timedelta(days=1)
            start_msk = datetime(
                previous_month_last_day.year,
                previous_month_last_day.month,
                1,
                tzinfo=self._moscow_tz,
            )
            caption = (
                f"{start_msk.strftime('%d.%m.%Y')} — "
                f"{previous_month_last_day.strftime('%d.%m.%Y')}"
            )
            return ReportPeriodInfo(
                start_msk=start_msk,
                end_msk=end_msk,
                title="Ежемесячный отчет",
                caption=caption,
                range_caption=caption,
                emoji="📆",
            )

        logger.warning("Неизвестный период отчета: %s", period)
        return None

    def _get_next_run_datetime(self) -> datetime:
        dispatch_time = settings.get_admin_reports_time()
        now_msk = datetime.now(self._moscow_tz)

        run_msk = datetime.combine(now_msk.date(), dispatch_time, tzinfo=self._moscow_tz)
        if run_msk <= now_msk:
            run_msk += timedelta(days=1)

        return run_msk.astimezone(self._utc_tz)


report_service = ReportService()
