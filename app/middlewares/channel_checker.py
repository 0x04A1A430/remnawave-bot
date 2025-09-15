import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import TelegramObject, Update, Message, CallbackQuery

from app.config import settings
from app.keyboards.inline import get_channel_sub_keyboard
from app.utils.check_reg_process import is_registration_process

logger = logging.getLogger(__name__)


class ChannelCheckerMiddleware(BaseMiddleware):
    def __init__(self):
        self.BAD_MEMBER_STATUS = ("left", "kicked")

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

        if telegram_id is None:
            return await handler(event, data)

        state: FSMContext = data.get('state')
        current_state = None

        if state:
            current_state = await state.get_state()

        is_reg_process = is_registration_process(event, current_state)

        # Пропускаем пользователя на разрешенные ивенты
        if is_reg_process:
            return await handler(event, data)

        bot: Bot = data["bot"]

        channel_id = settings.CHANNEL_SUB_ID
        if not channel_id:
            return await handler(event, data)

        channel_link = settings.CHANNEL_LINK
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=telegram_id)
            if member.status in self.BAD_MEMBER_STATUS:
                return await self._deny_message(event, bot, channel_link)
        except (TelegramForbiddenError, TelegramBadRequest):
            # бот не админ или нет доступа к каналу
            return await self._deny_message(event, bot, channel_link)

        # если все каналы пройдены
        return await handler(event, data)

    @staticmethod
    async def _deny_message(event: TelegramObject, bot: Bot, channel_link: str):
        channel_sub_kb = get_channel_sub_keyboard(channel_link)
        text = f"""🔔 Для использования бота подпишитесь на новостной канал, чтобы получать уведомления о новых возможностях и обновлениях бота. Спасибо!"""
        if isinstance(event, Message):
            return await event.answer(text, reply_markup=channel_sub_kb)
        elif isinstance(event, CallbackQuery):
            return await event.message.edit_text(text, reply_markup=channel_sub_kb)
        elif isinstance(event, Update) and event.message:
            return await bot.send_message(event.message.chat.id, text, reply_markup=channel_sub_kb)

