from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser, Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable
import logging
from database import Database, User
from config import Config

logger = logging.getLogger(__name__)

class DatabaseMiddleware(BaseMiddleware):
    """Middleware for database operations"""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        data['db'] = self.db
        return await handler(event, data)

class UserMiddleware(BaseMiddleware):
    """Middleware for user management"""
    
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        telegram_user: TgUser = data.get('event_from_user')
        
        if telegram_user and not telegram_user.is_bot:
            try:
                # Get or create user
                user = await self.db.get_user_by_telegram_id(telegram_user.id)
                
                if not user:
                    # Check if user is admin
                    is_admin = telegram_user.id in self.config.ADMIN_IDS
                    
                    # Create new user
                    user = await self.db.create_user(
                        telegram_id=telegram_user.id,
                        username=telegram_user.username,
                        first_name=telegram_user.first_name,
                        last_name=telegram_user.last_name,
                        language=self.config.DEFAULT_LANGUAGE,
                        is_admin=is_admin
                    )
                    logger.info(f"Created new user: {telegram_user.id}")
                else:
                    # Update user info if changed
                    updated = False
                    if user.username != telegram_user.username:
                        user.username = telegram_user.username
                        updated = True
                    if user.first_name != telegram_user.first_name:
                        user.first_name = telegram_user.first_name
                        updated = True
                    if user.last_name != telegram_user.last_name:
                        user.last_name = telegram_user.last_name
                        updated = True
                    
                    # Check admin status
                    should_be_admin = telegram_user.id in self.config.ADMIN_IDS
                    if user.is_admin != should_be_admin:
                        user.is_admin = should_be_admin
                        updated = True
                    
                    if updated:
                        await self.db.update_user(user)
                
                data['user'] = user
                data['lang'] = user.language if user else self.config.DEFAULT_LANGUAGE
                
            except Exception as e:
                logger.error(f"Error in UserMiddleware: {e}")
                # Create a minimal user object with defaults if database fails
                class FallbackUser:
                    def __init__(self, telegram_id: int, username: str = None, config: Config = None):
                        self.telegram_id = telegram_id
                        self.username = username
                        self.first_name = None
                        self.last_name = None
                        self.language = config.DEFAULT_LANGUAGE if config else 'ru'
                        self.balance = 0.0
                        self.is_admin = telegram_id in (config.ADMIN_IDS if config else [])
                        self.remnawave_uuid = None
                
                # Try to use fallback, but if database is completely broken, set None
                try:
                    fallback_user = FallbackUser(telegram_user.id, telegram_user.username, self.config)
                    data['user'] = fallback_user
                    data['lang'] = self.config.DEFAULT_LANGUAGE
                except:
                    data['user'] = None
                    data['lang'] = self.config.DEFAULT_LANGUAGE
        else:
            data['user'] = None
            data['lang'] = self.config.DEFAULT_LANGUAGE
        
        return await handler(event, data)

class LoggingMiddleware(BaseMiddleware):
    """Middleware for logging"""
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        telegram_user: TgUser = data.get('event_from_user')
        
        if isinstance(event, Message):
            username = f"@{telegram_user.username}" if telegram_user and telegram_user.username else "no_username"
            user_id = telegram_user.id if telegram_user else "unknown"
            text = event.text or "no_text"
            logger.info(f"Message from {user_id} ({username}): {text}")
        elif isinstance(event, CallbackQuery):
            username = f"@{telegram_user.username}" if telegram_user and telegram_user.username else "no_username"
            user_id = telegram_user.id if telegram_user else "unknown"
            callback_data = event.data or "no_data"
            logger.info(f"Callback from {user_id} ({username}): {callback_data}")
        
        return await handler(event, data)

class ThrottlingMiddleware(BaseMiddleware):
    """Simple throttling middleware"""
    
    def __init__(self, rate_limit: float = 1.0):
        self.rate_limit = rate_limit
        self.user_last_action = {}
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        telegram_user: TgUser = data.get('event_from_user')
        
        if telegram_user:
            import time
            current_time = time.time()
            user_id = telegram_user.id
            
            if user_id in self.user_last_action:
                if current_time - self.user_last_action[user_id] < self.rate_limit:
                    logger.warning(f"Throttling user {user_id}")
                    return
            
            self.user_last_action[user_id] = current_time
        
        return await handler(event, data)

class WorkflowDataMiddleware(BaseMiddleware):
    """Middleware to pass workflow data to handlers"""
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Get dispatcher from event
        if hasattr(event, 'bot'):
            dp = getattr(event.bot, '_dispatcher', None)
            if dp and hasattr(dp, 'workflow_data'):
                # Add workflow data to handler data
                workflow_data = dp.workflow_data
                for key, value in workflow_data.items():
                    if key not in data:  # Don't override existing data
                        data[key] = value
        
        return await handler(event, data)

class BotMiddleware(BaseMiddleware):
    """Middleware to add bot instance to handler data"""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        data['bot'] = self.bot
        return await handler(event, data)
