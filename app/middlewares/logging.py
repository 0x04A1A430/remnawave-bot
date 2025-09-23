import logging
import time
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseMiddleware):
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        start_time = time.time()
        
        try:
            if isinstance(event, Message):
                user_info = f"@{event.from_user.username}" if event.from_user.username else f"ID:{event.from_user.id}"
                text = event.text or event.caption or "[медиа]"
                logger.info(f"📩 Сообщение от {user_info}: {text}")
                
            elif isinstance(event, CallbackQuery):
                user_info = f"@{event.from_user.username}" if event.from_user.username else f"ID:{event.from_user.id}"
                logger.info(f"🔘 Callback от {user_info}: {event.data}")
            
            result = await handler(event, data)
            
            execution_time = time.time() - start_time
            if execution_time > 1.0:  
                logger.warning(f"⏱️ Медленная операция: {execution_time:.2f}s")
            
            return result
            
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"❌ Ошибка при обработке события за {execution_time:.2f}s: {e}")
            raise