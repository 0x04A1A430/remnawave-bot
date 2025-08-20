import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject, User as TgUser
from aiogram.fsm.context import FSMContext

from app.config import settings
from app.database.database import get_db
from app.database.crud.user import get_user_by_telegram_id, create_user
from app.states import RegistrationStates

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        user: TgUser = None
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
        
        if not user:
            return await handler(event, data)
        
        if user.is_bot:
            return await handler(event, data)
        
        async for db in get_db():
            try:
                db_user = await get_user_by_telegram_id(db, user.id)
                
                if not db_user:
                    state: FSMContext = data.get('state')
                    current_state = None
                    
                    if state:
                        current_state = await state.get_state()
                    
                    registration_states = [
                        RegistrationStates.waiting_for_rules_accept,
                        RegistrationStates.waiting_for_referral_code
                    ]
                    
                    is_registration_process = (
                        (isinstance(event, Message) and event.text and event.text.startswith('/start'))
                        or (isinstance(event, CallbackQuery) and current_state and 
                            any(str(state) in str(current_state) for state in registration_states))
                        or (isinstance(event, CallbackQuery) and event.data and 
                            (event.data in ['rules_accept', 'rules_decline', 'referral_skip']))
                    )
                    
                    if is_registration_process:
                        logger.info(f"🔓 Пропускаем пользователя {user.id} в процессе регистрации")
                        data['db'] = db
                        data['db_user'] = None
                        data['is_admin'] = False
                        return await handler(event, data)
                    else:
                        if isinstance(event, Message):
                            await event.answer(
                                "◀️ Для начала работы необходимо выполнить команду /start"
                            )
                        elif isinstance(event, CallbackQuery):
                            await event.answer(
                                "◀️ Необходимо начать с команды /start",
                                show_alert=True
                            )
                        logger.info(f"🚫 Заблокирован незарегистрированный пользователь {user.id}")
                        return
                else:
                    from datetime import datetime
                    db_user.last_activity = datetime.utcnow()
                    await db.commit()
                
                data['db'] = db
                data['db_user'] = db_user
                data['is_admin'] = settings.is_admin(user.id)
                
                return await handler(event, data)
                
            except Exception as e:
                logger.error(f"Ошибка в AuthMiddleware: {e}")
                logger.error(f"Event type: {type(event)}")
                if hasattr(event, 'data'):
                    logger.error(f"Callback data: {event.data}")
                await db.rollback()
                raise