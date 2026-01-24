import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.database.models import SubscriptionStatus


logger = logging.getLogger(__name__)

# Буфер времени перед деактивацией (защита от race condition при продлении)
EXPIRATION_BUFFER_MINUTES = 5


class SubscriptionStatusMiddleware(BaseMiddleware):
    """
    Проверяет статус подписки пользователя.
    ВАЖНО: Использует db и db_user из data, которые уже загружены в AuthMiddleware.
    Не создаёт дополнительных сессий БД.

    Деактивирует подписку только если она истекла более чем на EXPIRATION_BUFFER_MINUTES минут.
    Это защищает от race conditions при продлении подписки.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Используем db и user из AuthMiddleware - не создаём новую сессию!
        db = data.get('db')
        user = data.get('db_user')

        if db and user and user.subscription:
            try:
                current_time = datetime.utcnow()
                subscription = user.subscription

                if (
                    subscription.status == SubscriptionStatus.ACTIVE.value
                    and subscription.end_date
                    and subscription.end_date <= current_time
                ):
                    # Вычисляем насколько давно истекла подписка
                    time_since_expiry = current_time - subscription.end_date

                    # Деактивируем только если прошло больше буфера (защита от race condition)
                    if time_since_expiry > timedelta(minutes=EXPIRATION_BUFFER_MINUTES):
                        subscription.status = SubscriptionStatus.EXPIRED.value
                        subscription.updated_at = current_time
                        await db.commit()

                        logger.warning(
                            f'⏰ Middleware DEACTIVATION: подписка {subscription.id} '
                            f'(user_id={user.id}) деактивирована. '
                            f'end_date={subscription.end_date}, просрочена на {time_since_expiry}'
                        )
                    else:
                        # Подписка только что истекла - не деактивируем сразу (может быть продление)
                        logger.debug(
                            f'⏰ Middleware: подписка пользователя {user.id} истекла недавно '
                            f'({time_since_expiry}), ждём буфер {EXPIRATION_BUFFER_MINUTES} мин'
                        )

            except Exception as e:
                logger.error(f'Ошибка проверки статуса подписки: {e}')

        return await handler(event, data)
