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
                    
                    logger.info(f"🔍 DEBUG AUTH: user_id={user.id}, current_state={current_state}, event_type={type(event).__name__}")
                    if isinstance(event, Message):
                        logger.info(f"🔍 DEBUG AUTH: message_text='{event.text}'")
                    
                    registration_states = [
                        RegistrationStates.waiting_for_rules_accept,
                        RegistrationStates.waiting_for_referral_code
                    ]
                    
                    is_start_command = isinstance(event, Message) and event.text and event.text.startswith('/start')
                    is_registration_callback = isinstance(event, CallbackQuery) and event.data and event.data in ['rules_accept', 'rules_decline', 'referral_skip']
                    is_waiting_rules = current_state == RegistrationStates.waiting_for_rules_accept.state
                    is_waiting_referral = current_state == RegistrationStates.waiting_for_referral_code.state
                    is_message_in_referral_state = isinstance(event, Message) and is_waiting_referral
                    
                    is_registration_process = (
                        is_start_command 
                        or is_registration_callback
                        or is_waiting_rules 
                        or is_message_in_referral_state
                    )
                    
                    logger.info(f"🔍 DEBUG AUTH: is_start={is_start_command}, is_callback={is_registration_callback}, is_waiting_rules={is_waiting_rules}, is_waiting_referral={is_waiting_referral}, is_msg_in_ref={is_message_in_referral_state}")
                    logger.info(f"🔍 DEBUG AUTH: final_result={is_registration_process}")
                    
                    if is_registration_process:
                        logger.info(f"📝 Пропускаем пользователя {user.id} в процессе регистрации")
                        data['db'] = db
                        data['db_user'] = None
                        data['is_admin'] = False
                        return await handler(event, data)
                    else:
                        if isinstance(event, Message):
                            await event.answer(
                                "▶️ Для начала работы необходимо выполнить команду /start"
                            )
                        elif isinstance(event, CallbackQuery):
                            await event.answer(
                                "▶️ Необходимо начать с команды /start",
                                show_alert=True
                            )
                        logger.info(f"🚫 Заблокирован незарегистрированный пользователь {user.id}")
                        return
                else:
                    from app.database.models import UserStatus
                    
                    if db_user.status == UserStatus.BLOCKED.value:
                        if isinstance(event, Message):
                            await event.answer("🚫 Ваш аккаунт заблокирован администратором.")
                        elif isinstance(event, CallbackQuery):
                            await event.answer("🚫 Ваш аккаунт заблокирован администратором.", show_alert=True)
                        logger.info(f"🚫 Заблокированный пользователь {user.id} попытался использовать бота")
                        return
                    
                    if db_user.status == UserStatus.DELETED.value:
                        state: FSMContext = data.get('state')
                        current_state = None
                        
                        if state:
                            current_state = await state.get_state()
                        
                        logger.info(f"🔍 DEBUG AUTH DELETED: user_id={user.id}, current_state={current_state}, event_type={type(event).__name__}")
                        
                        is_start_command = isinstance(event, Message) and event.text and event.text.startswith('/start')
                        is_registration_callback = isinstance(event, CallbackQuery) and event.data and event.data in ['rules_accept', 'rules_decline', 'referral_skip']
                        is_waiting_rules = current_state == RegistrationStates.waiting_for_rules_accept.state
                        is_waiting_referral = current_state == RegistrationStates.waiting_for_referral_code.state
                        is_message_in_referral_state = isinstance(event, Message) and is_waiting_referral
                        
                        is_start_or_registration = (
                            is_start_command 
                            or is_registration_callback
                            or is_waiting_rules 
                            or is_message_in_referral_state
                        )
                        
                        logger.info(f"🔍 DEBUG AUTH DELETED: is_start={is_start_command}, is_callback={is_registration_callback}, is_waiting_rules={is_waiting_rules}, is_waiting_referral={is_waiting_referral}, is_msg_in_ref={is_message_in_referral_state}")
                        logger.info(f"🔍 DEBUG AUTH DELETED: final_result={is_start_or_registration}")
                        
                        if is_start_or_registration:
                            logger.info(f"🔄 Удаленный пользователь {user.id} начинает повторную регистрацию")
                            data['db'] = db
                            data['db_user'] = None 
                            data['is_admin'] = False
                            return await handler(event, data)
                        else:
                            if isinstance(event, Message):
                                await event.answer(
                                    "❌ Ваш аккаунт был удален.\n"
                                    "🔄 Для повторной регистрации выполните команду /start"
                                )
                            elif isinstance(event, CallbackQuery):
                                await event.answer(
                                    "❌ Ваш аккаунт был удален. Для повторной регистрации выполните /start",
                                    show_alert=True
                                )
                            logger.info(f"❌ Удаленный пользователь {user.id} попытался использовать бота без /start")
                            return
                    
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
