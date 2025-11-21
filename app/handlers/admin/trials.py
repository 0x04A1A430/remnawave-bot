import logging

from aiogram import Dispatcher, F, types
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.subscription import (
    get_trial_statistics,
    reset_trials_for_users_without_paid_subscription,
)
from app.database.models import User
from app.keyboards.admin import get_admin_trials_keyboard
from app.localization.texts import get_texts
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_trials_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    stats = await get_trial_statistics(db)
    message = texts.t("ADMIN_TRIALS_TITLE", "🧪 Управление триалами") + "\n\n" + texts.t(
        "ADMIN_TRIALS_STATS",
        "• Использовано всего: {used}\n"
        "• Активно сейчас: {active}\n"
        "• Доступно к сбросу: {resettable}",
    ).format(
        used=stats.get("used_trials", 0),
        active=stats.get("active_trials", 0),
        resettable=stats.get("resettable_trials", 0),
    )

    await callback.message.edit_text(
        message,
        reply_markup=get_admin_trials_keyboard(db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def reset_trials(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    reset_count = await reset_trials_for_users_without_paid_subscription(db)
    stats = await get_trial_statistics(db)

    message = texts.t(
        "ADMIN_TRIALS_RESET_RESULT",
        "♻️ Сбросили {reset_count} триалов.\n\n"
        "• Использовано всего: {used}\n"
        "• Активно сейчас: {active}\n"
        "• Доступно к сбросу: {resettable}",
    ).format(
        reset_count=reset_count,
        used=stats.get("used_trials", 0),
        active=stats.get("active_trials", 0),
        resettable=stats.get("resettable_trials", 0),
    )

    await callback.message.edit_text(
        message,
        reply_markup=get_admin_trials_keyboard(db_user.language),
    )
    await callback.answer(texts.t("ADMIN_TRIALS_RESET_TOAST", "✅ Сброс завершен"))


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        show_trials_panel,
        F.data == "admin_trials",
    )
    dp.callback_query.register(
        reset_trials,
        F.data == "admin_trials_reset",
    )
