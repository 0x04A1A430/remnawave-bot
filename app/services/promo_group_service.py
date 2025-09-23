import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PromoGroup, User
from app.database.crud.promo_group import get_auto_assign_promo_groups
from app.database.crud.transaction import get_user_total_spent


logger = logging.getLogger(__name__)


async def auto_assign_promo_group_by_spent(
    db: AsyncSession,
    user: Optional[User],
) -> Optional[PromoGroup]:
    if not user:
        return None

    groups = await get_auto_assign_promo_groups(db)
    if not groups:
        return None

    total_spent = await get_user_total_spent(db, user.id)

    target_group: Optional[PromoGroup] = None
    for group in groups:
        threshold = max(0, group.spent_threshold_kopeks or 0)
        if total_spent >= threshold:
            target_group = group
            break

    if not target_group or user.promo_group_id == target_group.id:
        return None

    previous_group_id = user.promo_group_id

    user.promo_group_id = target_group.id
    user.promo_group = target_group
    user.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(user)

    logger.info(
        "👥 Пользователь %s автоматически переведен в промогруппу '%s' (порог=%s, потрачено=%s)",
        user.telegram_id,
        target_group.name,
        target_group.spent_threshold_kopeks,
        total_spent,
    )

    if previous_group_id != target_group.id:
        logger.debug(
            "👥 Пользователь %s покинул промогруппу %s → %s",
            user.telegram_id,
            previous_group_id,
            target_group.id,
        )

    return target_group
