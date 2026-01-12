import logging
from typing import Callable, Dict, Any, Awaitable
from datetime import datetime
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.database.models import SubscriptionStatus

logger = logging.getLogger(__name__)


class SubscriptionStatusMiddleware(BaseMiddleware):
    """
    Проверяет статус подписки пользователя.
    ВАЖНО: Использует db и db_user из data, которые уже загружены в AuthMiddleware.
    Не создаёт дополнительных сессий БД.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Используем db и user из AuthMiddleware - не создаём новую сессию!
        db = data.get('db')
        user = data.get('db_user')

        if db and user and user.subscription:
            try:
                current_time = datetime.utcnow()
                subscription = user.subscription

                if (subscription.status == SubscriptionStatus.ACTIVE.value and
                    subscription.end_date and
                    subscription.end_date <= current_time):

                    subscription.status = SubscriptionStatus.EXPIRED.value
                    subscription.updated_at = current_time
                    await db.commit()

                    logger.info(f"⏰ Middleware: Статус подписки пользователя {user.id} изменен на 'expired' (время истекло)")

            except Exception as e:
                logger.error(f"Ошибка проверки статуса подписки: {e}")

        return await handler(event, data)
