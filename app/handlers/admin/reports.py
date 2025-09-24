import logging
from aiogram import Dispatcher, F, types
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.keyboards.admin import get_admin_reports_keyboard
from app.services.reporting_service import (
    ReportPeriod,
    ReportingServiceError,
    reporting_service,
)
from app.utils.decorators import admin_required, error_handler


logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_reports_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await callback.message.edit_text(
        "📊 <b>Отчеты</b>\n\n"
        "Выберите период, чтобы отправить отчет в админский топик.",
        reply_markup=get_admin_reports_keyboard(db_user.language),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def send_daily_report(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await _send_report(callback, ReportPeriod.DAILY)


@admin_required
@error_handler
async def send_weekly_report(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await _send_report(callback, ReportPeriod.WEEKLY)


@admin_required
@error_handler
async def send_monthly_report(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await _send_report(callback, ReportPeriod.MONTHLY)


async def _send_report(callback: types.CallbackQuery, period: ReportPeriod) -> None:
    try:
        report_text = await reporting_service.send_report(period, send_to_topic=True)
    except ReportingServiceError as exc:
        logger.warning("Не удалось отправить отчет: %s", exc)
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception as exc:  # noqa: BLE001
        logger.error("Непредвиденная ошибка при отправке отчета: %s", exc)
        await callback.answer("Не удалось отправить отчет. Попробуйте позже.", show_alert=True)
        return

    await callback.message.answer(report_text)
    await callback.answer("Отчет отправлен в топик")


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(show_reports_menu, F.data == "admin_reports")
    dp.callback_query.register(send_daily_report, F.data == "admin_reports_daily")
    dp.callback_query.register(send_weekly_report, F.data == "admin_reports_weekly")
    dp.callback_query.register(send_monthly_report, F.data == "admin_reports_monthly")

