import logging
import functools
from typing import Callable, Any
from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

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
            
            try:
                if isinstance(event, types.Message):
                    await event.answer(texts.ACCESS_DENIED)
                elif isinstance(event, types.CallbackQuery):
                    await event.answer(texts.ACCESS_DENIED, show_alert=True)
            except TelegramBadRequest as e:
                if "query is too old" in str(e).lower():
                    logger.warning(f"Попытка ответить на устаревший callback query от {user.id if user else 'Unknown'}")
                else:
                    raise
            
            logger.warning(f"Попытка доступа к админской функции от {user.id if user else 'Unknown'}")
            return
        
        return await func(event, *args, **kwargs)
    
    return wrapper


def auth_required(func: Callable) -> Callable:
    """
    Простая проверка на наличие пользователя в апдейте. Middleware уже подтягивает db_user,
    но здесь страхуемся от вызовов без from_user.
    """
    @functools.wraps(func)
    async def wrapper(event: types.Update, *args, **kwargs) -> Any:
        user = None
        if isinstance(event, (types.Message, types.CallbackQuery)):
            user = event.from_user
        if not user:
            logger.warning("auth_required: нет from_user, пропускаем")
            return
        return await func(event, *args, **kwargs)
    return wrapper


def error_handler(func: Callable) -> Callable:
    
    @functools.wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        try:
            return await func(*args, **kwargs)
        except TelegramBadRequest as e:
            error_message = str(e).lower()
            
            if "query is too old" in error_message or "query id is invalid" in error_message:
                event = _extract_event(args)
                if event and isinstance(event, types.CallbackQuery):
                    user_info = f"@{event.from_user.username}" if event.from_user.username else f"ID:{event.from_user.id}"
                    logger.warning(f"🕐 Игнорируем устаревший callback '{event.data}' от {user_info} в {func.__name__}")
                else:
                    logger.warning(f"🕐 Игнорируем устаревший запрос в {func.__name__}: {e}")
                return None
                
            elif "message is not modified" in error_message:
                logger.debug(f"📝 Сообщение не изменено в {func.__name__}")
                event = _extract_event(args)
                if event and isinstance(event, types.CallbackQuery):
                    try:
                        await event.answer()
                    except TelegramBadRequest as answer_error:
                        if "query is too old" not in str(answer_error).lower():
                            logger.error(f"Ошибка при ответе на callback: {answer_error}")
                return None
                
            else:
                logger.error(f"Telegram API error в {func.__name__}: {e}")
                await _send_error_message(args, kwargs, e)
                
        except Exception as e:
            logger.error(f"Ошибка в {func.__name__}: {e}", exc_info=True)
            await _send_error_message(args, kwargs, e)
    
    return wrapper


def _extract_event(args) -> types.TelegramObject:
    for arg in args:
        if isinstance(arg, (types.Message, types.CallbackQuery)):
            return arg
    return None


async def _send_error_message(args, kwargs, original_error):
    try:
        event = _extract_event(args)
        db_user = kwargs.get('db_user')
        
        if not event:
            return
            
        texts = get_texts(db_user.language if db_user else 'ru')
        
        if isinstance(event, types.Message):
            await event.answer(texts.ERROR)
        elif isinstance(event, types.CallbackQuery):
            await event.answer(texts.ERROR, show_alert=True)
                
    except TelegramBadRequest as e:
        if "query is too old" in str(e).lower():
            logger.warning("Не удалось отправить сообщение об ошибке - callback query устарел")
        else:
            logger.error(f"Ошибка при отправке сообщения об ошибке: {e}")
    except Exception as e:
        logger.error(f"Критическая ошибка при отправке сообщения об ошибке: {e}")


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
            try:
                await event.bot.send_chat_action(
                    chat_id=event.chat.id,
                    action="typing"
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить typing action: {e}")
        
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


def modem_available(for_enable: bool = False, for_disable: bool = False):
    """
    Декоратор для проверки доступности модема.

    Проверяет:
    - Наличие подписки
    - Подписка не триальная
    - Функция модема включена в настройках
    - (опционально) Модем ещё не подключен (for_enable=True)
    - (опционально) Модем уже подключен (for_disable=True)

    Args:
        for_enable: Проверять, что модем ещё не подключен
        for_disable: Проверять, что модем подключен

    Usage:
        @modem_available()
        async def handle_modem_menu(callback, db_user, db): ...

        @modem_available(for_enable=True)
        async def handle_modem_enable(callback, db_user, db): ...
    """
    def decorator(func: Callable) -> Callable:

        @functools.wraps(func)
        async def wrapper(
            event: types.Update,
            *args,
            **kwargs
        ) -> Any:
            db_user = kwargs.get('db_user')

            if not db_user:
                logger.warning("modem_available: нет db_user в kwargs")
                return

            from app.services.modem_service import get_modem_service, ModemError

            service = get_modem_service()
            result = service.check_availability(
                db_user,
                for_enable=for_enable,
                for_disable=for_disable
            )

            if not result.available:
                texts = get_texts(db_user.language if db_user else 'ru')

                error_messages = {
                    ModemError.NO_SUBSCRIPTION: texts.t(
                        "MODEM_PAID_ONLY",
                        "Модем доступен только для платных подписок"
                    ),
                    ModemError.TRIAL_SUBSCRIPTION: texts.t(
                        "MODEM_PAID_ONLY",
                        "Модем доступен только для платных подписок"
                    ),
                    ModemError.MODEM_DISABLED: texts.t(
                        "MODEM_DISABLED",
                        "Функция модема отключена"
                    ),
                    ModemError.ALREADY_ENABLED: texts.t(
                        "MODEM_ALREADY_ENABLED",
                        "Модем уже подключен"
                    ),
                    ModemError.NOT_ENABLED: texts.t(
                        "MODEM_NOT_ENABLED",
                        "Модем не подключен"
                    ),
                }

                error_text = error_messages.get(result.error, texts.ERROR)

                try:
                    if isinstance(event, types.CallbackQuery):
                        await event.answer(error_text, show_alert=True)
                    elif isinstance(event, types.Message):
                        await event.answer(error_text)
                except TelegramBadRequest as e:
                    if "query is too old" not in str(e).lower():
                        raise

                return None

            return await func(event, *args, **kwargs)

        return wrapper

    return decorator
