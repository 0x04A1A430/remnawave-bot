import logging

from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.report_service import report_service, ReportPeriod
from app.utils.decorators import admin_required, error_handler


logger = logging.getLogger(__name__)


async def _send_report(
    callback: types.CallbackQuery,
    db_user: User,
    period: ReportPeriod,
    success_message: str,
    error_message: str,
):
    success, _ = await report_service.send_report(period)

    if success:
        logger.info("Админ %s отправил отчет %s", db_user.id, period.value)
        await callback.answer(success_message)
    else:
        logger.error("Не удалось отправить отчет %s по запросу админа %s", period.value, db_user.id)
        await callback.answer(error_message, show_alert=True)


@admin_required
@error_handler
async def send_daily_report(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    await _send_report(
        callback,
        db_user,
        ReportPeriod.DAILY,
        texts.ADMIN_REPORTS_SENT,
        texts.ADMIN_REPORTS_ERROR,
    )


@admin_required
@error_handler
async def send_weekly_report(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    await _send_report(
        callback,
        db_user,
        ReportPeriod.WEEKLY,
        texts.ADMIN_REPORTS_SENT,
        texts.ADMIN_REPORTS_ERROR,
    )


@admin_required
@error_handler
async def send_monthly_report(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    await _send_report(
        callback,
        db_user,
        ReportPeriod.MONTHLY,
        texts.ADMIN_REPORTS_SENT,
        texts.ADMIN_REPORTS_ERROR,
    )


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        send_daily_report,
        F.data == "admin_report_daily",
    )
    dp.callback_query.register(
        send_weekly_report,
        F.data == "admin_report_weekly",
    )
    dp.callback_query.register(
        send_monthly_report,
        F.data == "admin_report_monthly",
    )
