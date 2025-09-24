import logging
from datetime import datetime
from typing import Optional

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.promo_group import get_auto_assign_promo_group
from app.database.crud.transaction import get_user_total_completed_deposits
from app.database.models import PromoGroup, User
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


def _format_total_amount(total_amount_kopeks: int) -> str:
    return settings.format_price(total_amount_kopeks)


async def maybe_assign_auto_promo_group(
    db: AsyncSession,
    user: User,
    bot: Optional[Bot] = None,
) -> Optional[PromoGroup]:
    """Назначает промогруппу автоматически при достижении нужной суммы пополнений."""
    try:
        if getattr(user, "promo_group_auto_assigned", False):
            return None

        total_amount_kopeks = await get_user_total_completed_deposits(db, user.id)
        target_group = await get_auto_assign_promo_group(db, total_amount_kopeks)

        if not target_group or target_group.id == user.promo_group_id:
            return None

        user.promo_group_id = target_group.id
        user.promo_group = target_group
        user.promo_group_auto_assigned = True
        user.updated_at = datetime.utcnow()

        await db.commit()
        await db.refresh(user)

        logger.info(
            "Автоматически назначена промогруппа '%s' пользователю %s (сумма пополнений: %s)",
            target_group.name,
            user.telegram_id,
            _format_total_amount(total_amount_kopeks),
        )

        if bot:
            try:
                texts = get_texts(user.language)
                message = texts.t(
                    "PROMO_GROUP_AUTO_ASSIGN_NOTIFICATION",
                    "🎉 Вы автоматически переведены в промогруппу «{name}» за пополнения на {amount}.",
                ).format(name=target_group.name, amount=_format_total_amount(total_amount_kopeks))
                await bot.send_message(user.telegram_id, message, parse_mode="HTML")
            except Exception as notify_error:
                logger.error(
                    "Ошибка отправки уведомления об автоназначении промогруппы пользователю %s: %s",
                    user.telegram_id,
                    notify_error,
                )

        return target_group

    except Exception as error:
        logger.error(
            "Ошибка автоматического назначения промогруппы пользователю %s: %s",
            getattr(user, "telegram_id", "unknown"),
            error,
            exc_info=True,
        )
        await db.rollback()
        return None
