"""Revalidate automatic renewal against locked, current account/subscription data."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.models import Subscription, User
from app.utils.subscription_time import ends_within_days


async def lock_autopay_target(
    db: AsyncSession,
    user_id: int,
    subscription_id: int,
    *,
    expected_end_date: datetime,
    expired_only: bool = False,
    allow_without_autopay: bool = False,
) -> tuple[User, Subscription] | None:
    """Hold User then Subscription until caller commits the whole renewal.

    The candidate list and ORM identity map may predate a manual renewal,
    consent change, transfer or block. Recheck them before computing a price.
    """
    from app.database.crud.user import lock_user_for_pricing

    user = await lock_user_for_pricing(db, user_id)
    subscription = await db.scalar(
        select(Subscription)
        .where(Subscription.id == subscription_id, Subscription.user_id == user_id)
        .options(selectinload(Subscription.tariff))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = datetime.now(UTC)
    if subscription is not None:
        tariff = subscription.tariff
        # Обычный autopay требует явного согласия. Для истёкшей подписки с
        # балансом разрешаем продлить и без него, если глобальная настройка включена.
        autopay_ok = subscription.autopay_enabled is True
        if allow_without_autopay and settings.AUTO_RENEW_EXPIRED_FROM_BALANCE:
            autopay_ok = autopay_ok or (
                subscription.status == 'expired' and now >= subscription.end_date
            )
        allowed = (
            user.status == 'active'
            and autopay_ok
            and subscription.is_trial is False
            and subscription.end_date == expected_end_date
            and not subscription.is_daily_paused
            and not (tariff and (tariff.is_daily or not tariff.is_active))
            and not (settings.is_tariffs_mode() and not subscription.tariff_id)
        )
        if expired_only:
            allowed = (
                allowed
                and subscription.status == 'expired'
                and now >= subscription.end_date
                and (now - subscription.end_date).days <= 30
            )
        else:
            expired_window = timedelta(
                hours=max(1, int(settings.AUTO_RENEW_EXPIRED_WINDOW_HOURS))
                if allow_without_autopay
                else 2
            )
            allowed = (
                allowed
                and (
                    subscription.status == 'active'
                    or (subscription.status == 'expired' and subscription.end_date >= now - expired_window)
                )
                and ends_within_days(subscription.end_date, min(subscription.autopay_days_before or 3, 3), now)
            )
        if allowed:
            from app.services.cispay_recurrent_guard import has_conflicting_cispay

            if not await has_conflicting_cispay(db, user_id=user.id, subscription_id=subscription.id):
                return user, subscription
    # No billing mutations have occurred: release the read locks without
    # expiring the caller's user object (used by the top-up response).
    await db.commit()
    return None
