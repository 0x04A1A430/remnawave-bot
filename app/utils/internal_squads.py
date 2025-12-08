from typing import List, Optional

from app.database.models import Subscription, User


def resolve_user_internal_squads(
    user: Optional[User], subscription: Optional[Subscription]
) -> List[str]:
    """Возвращает список Internal Squads для пользователя.

    Приоритет у персональных сквадов пользователя. Если они не заданы,
    используется список сквадов из подписки.
    """

    if user and getattr(user, "active_internal_squads", None) is not None:
        return list(user.active_internal_squads or [])

    if subscription:
        return list(subscription.connected_squads or [])

    return []
