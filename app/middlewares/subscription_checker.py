import logging
from typing import Callable, Dict, Any, Awaitable
from datetime import datetime
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, Message, CallbackQuery

from app.database.database import get_db
from app.database.crud.user import get_user_by_telegram_id
from app.database.models import SubscriptionStatus

logger = logging.getLogger(__name__)


class SubscriptionStatusMiddleware(BaseMiddleware):
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        telegram_id = None
        if isinstance(event, (Message, CallbackQuery)):
            telegram_id = event.from_user.id
        elif isinstance(event, Update):
            if event.message:
                telegram_id = event.message.from_user.id
            elif event.callback_query:
                telegram_id = event.callback_query.from_user.id
        
        if telegram_id:
            try:
                async for db in get_db():
                    user = await get_user_by_telegram_id(db, telegram_id)
                    if user and user.subscription:
                        current_time = datetime.utcnow()
                        subscription = user.subscription
                        
                        if (subscription.status == SubscriptionStatus.ACTIVE.value and 
                            subscription.end_date <= current_time):
                            
                            subscription.status = SubscriptionStatus.EXPIRED.value
                            subscription.updated_at = current_time
                            await db.commit()
                            
                            logger.info(f"⏰ Middleware: Статус подписки пользователя {user.id} изменен на 'expired' (время истекло)")
                    break
                    
            except Exception as e:
                logger.error(f"Ошибка проверки статуса подписки для пользователя {telegram_id}: {e}")
        
        return await handler(event, data)
