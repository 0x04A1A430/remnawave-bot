import logging
from typing import Optional

from sqlalchemy.exc import MissingGreenlet

from app.database.models import Subscription, User
from app.utils.cache import UserCache


logger = logging.getLogger(__name__)


_CHECKOUT_SESSION_KEY = "subscription_checkout"
_CHECKOUT_TTL_SECONDS = 3600


async def save_subscription_checkout_draft(
    user_id: int, data: dict, ttl: int = _CHECKOUT_TTL_SECONDS
) -> bool:
    """Persist subscription checkout draft data in cache."""

    return await UserCache.set_user_session(user_id, _CHECKOUT_SESSION_KEY, data, ttl)


async def get_subscription_checkout_draft(user_id: int) -> Optional[dict]:
    """Retrieve subscription checkout draft from cache."""

    return await UserCache.get_user_session(user_id, _CHECKOUT_SESSION_KEY)


async def clear_subscription_checkout_draft(user_id: int) -> bool:
    """Remove stored subscription checkout draft for the user."""

    return await UserCache.delete_user_session(user_id, _CHECKOUT_SESSION_KEY)


async def has_subscription_checkout_draft(user_id: int) -> bool:
    draft = await get_subscription_checkout_draft(user_id)
    return draft is not None


def should_offer_checkout_resume(
    user: User,
    has_draft: bool,
    *,
    subscription: Subscription | None = None,
) -> bool:
    """
    Determine whether checkout resume button should be available for the user.

    Only users without an active paid subscription or users currently on trial
    are eligible to continue assembling the subscription from the saved draft.
    """

    if not has_draft:
        return False

    if subscription is None:
        try:
            subscription = getattr(user, "subscription", None)
        except MissingGreenlet as error:
            logger.warning(
                "Не удалось лениво загрузить подписку пользователя %s при проверке возврата к checkout: %s",
                getattr(user, "id", None),
                error,
            )
            subscription = None

    if subscription is None:
        return True

    if getattr(subscription, "is_trial", False):
        return True

    if getattr(subscription, "actual_status", None) == "expired":
        return True

    return False
