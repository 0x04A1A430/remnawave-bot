import logging
from typing import Any

from aiogram import types
from aiogram.fsm.context import FSMContext

from app.database.models import User
from app.keyboards.inline import get_subscription_confirm_keyboard
from app.services.subscription_checkout_service import save_subscription_checkout_draft
from app.states import SubscriptionStates

from .pricing import _prepare_subscription_summary


logger = logging.getLogger(__name__)


async def present_subscription_summary(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    texts: Any,
) -> bool:
    data = await state.get_data()

    try:
        summary_text, prepared_data = await _prepare_subscription_summary(db_user, data, texts)
    except ValueError:
        logger.error(
            "Ошибка в расчете цены подписки для пользователя %s",
            db_user.telegram_id,
        )
        await callback.answer(
            "Ошибка расчета цены. Обратитесь в поддержку.",
            show_alert=True,
        )
        return False

    await state.set_data(prepared_data)
    await save_subscription_checkout_draft(db_user.id, prepared_data)

    await callback.message.edit_text(
        summary_text,
        reply_markup=get_subscription_confirm_keyboard(db_user.language),
        parse_mode="HTML",
    )
    await state.set_state(SubscriptionStates.confirming_purchase)

    return True
