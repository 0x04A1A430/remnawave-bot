import logging
import functools
from typing import Callable, Any
from aiogram import types
from aiogram.fsm.context import FSMContext

from app.config import settings
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


def admin_required(func: Callable) -> Callable:
    
    @functools.wraps(func)
    async def wrapper(
        event: types.Update,
        *args,
        **kwargs
    ) -> Any:
        user = None
        if isinstance(event, (types.Message, types.CallbackQuery)):
            user = event.from_user
        
        if not user or not settings.is_admin(user.id):
            texts = get_texts()
            
            if isinstance(event, types.Message):
                await event.answer(texts.ACCESS_DENIED)
            elif isinstance(event, types.CallbackQuery):
                await event.answer(texts.ACCESS_DENIED, show_alert=True)
            
            logger.warning(f"Попытка доступа к админской функции от {user.id if user else 'Unknown'}")
            return
        
        return await func(event, *args, **kwargs)
    
    return wrapper


def error_handler(func: Callable) -> Callable:
    
    @functools.wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в {func.__name__}: {e}", exc_info=True)
            
            event = None
            db_user = kwargs.get('db_user')
            
            for arg in args:
                if isinstance(arg, (types.Message, types.CallbackQuery)):
                    event = arg
                    break
            
            if event:
                texts = get_texts(db_user.language if db_user else 'ru')
                
                if isinstance(event, types.Message):
                    await event.answer(texts.ERROR)
                elif isinstance(event, types.CallbackQuery):
                    await event.answer(texts.ERROR, show_alert=True)
    
    return wrapper


def state_cleanup(func: Callable) -> Callable:
    
    @functools.wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        state = kwargs.get('state')
        
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if state and isinstance(state, FSMContext):
                await state.clear()
            raise e
    
    return wrapper


def typing_action(func: Callable) -> Callable:
    
    @functools.wraps(func)
    async def wrapper(
        event: types.Update,
        *args,
        **kwargs
    ) -> Any:
        if isinstance(event, types.Message):
            await event.bot.send_chat_action(
                chat_id=event.chat.id,
                action="typing"
            )
        
        return await func(event, *args, **kwargs)
    
    return wrapper


def rate_limit(rate: float = 1.0, key: str = None):
    def decorator(func: Callable) -> Callable:
        
        @functools.wraps(func)
        async def wrapper(
            event: types.Update,
            *args,
            **kwargs
        ) -> Any:
            return await func(event, *args, **kwargs)
        
        return wrapper
    
    return decorator