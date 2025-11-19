import logging
from typing import Callable, Dict, Any, Awaitable, Optional
from aiogram import BaseMiddleware, Bot, types
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import TelegramObject, Update, Message, CallbackQuery
from aiogram.enums import ChatMemberStatus

from app.config import settings
from app.database.database import get_db
from app.database.crud.campaign import get_campaign_by_start_parameter
from app.database.crud.subscription import deactivate_subscription
from app.database.crud.user import get_user_by_telegram_id
from app.database.models import SubscriptionStatus
from app.keyboards.inline import get_channel_sub_keyboard
from app.localization.loader import DEFAULT_LANGUAGE
from app.localization.texts import get_texts
from app.utils.check_reg_process import is_registration_process
from app.services.subscription_service import SubscriptionService
from app.services.admin_notification_service import AdminNotificationService

logger = logging.getLogger(__name__)


class ChannelCheckerMiddleware(BaseMiddleware):
    def __init__(self):
        self.BAD_MEMBER_STATUS = (
            ChatMemberStatus.LEFT,
            ChatMemberStatus.KICKED,
            ChatMemberStatus.RESTRICTED
        )
        self.GOOD_MEMBER_STATUS = (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR
        )
        logger.info("🔧 ChannelCheckerMiddleware инициализирован")

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
            logger.debug("❌ telegram_id не найден, пропускаем")
            return await handler(event, data)


        # Админам разрешаем пропускать проверку подписки, чтобы не блокировать
        # работу панели управления даже при отсутствии подписки. Важно делать
        # это до обращения к состоянию, чтобы не выполнять лишние операции.
        if settings.is_admin(telegram_id):
            logger.debug(
                "✅ Пользователь %s является администратором — пропускаем проверку подписки",
                telegram_id,
            )
            return await handler(event, data)

        state: FSMContext = data.get('state')
        current_state = None

        if state:
            current_state = await state.get_state()


        is_reg_process = is_registration_process(event, current_state)

        if is_reg_process:
            logger.debug("✅ Событие разрешено (процесс регистрации), пропускаем проверку")
            return await handler(event, data)

        bot: Bot = data["bot"]

        channel_id = settings.CHANNEL_SUB_ID
        
        if not channel_id:
            logger.warning("⚠️ CHANNEL_SUB_ID не установлен, пропускаем проверку")
            return await handler(event, data)

        is_required = settings.CHANNEL_IS_REQUIRED_SUB

        if not is_required:
            logger.debug("⚠️ Обязательная подписка отключена, пропускаем проверку")
            return await handler(event, data)

        channel_link = settings.CHANNEL_LINK

        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=telegram_id)

            if member.status in self.GOOD_MEMBER_STATUS:
                return await handler(event, data)
            elif member.status in self.BAD_MEMBER_STATUS:
                logger.info(f"❌ Пользователь {telegram_id} не подписан на канал (статус: {member.status})")

                if telegram_id:
                    await self._deactivate_trial_subscription(telegram_id)

                await self._capture_start_payload(state, event, bot)

                if isinstance(event, CallbackQuery) and event.data == "sub_channel_check":
                    await event.answer("❌ Вы еще не подписались на канал! Подпишитесь и попробуйте снова.", show_alert=True)
                    return

                return await self._deny_message(event, bot, channel_link)
            else:
                logger.warning(f"⚠️ Неожиданный статус пользователя {telegram_id}: {member.status}")
                await self._capture_start_payload(state, event, bot)
                return await self._deny_message(event, bot, channel_link)

        except TelegramForbiddenError as e:
            logger.error(f"❌ Бот заблокирован в канале {channel_id}: {e}")
            await self._capture_start_payload(state, event, bot)
            return await self._deny_message(event, bot, channel_link)
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                logger.error(f"❌ Канал {channel_id} не найден: {e}")
            elif "user not found" in str(e).lower():
                logger.error(f"❌ Пользователь {telegram_id} не найден: {e}")
            else:
                logger.error(f"❌ Ошибка запроса к каналу {channel_id}: {e}")
            await self._capture_start_payload(state, event, bot)
            return await self._deny_message(event, bot, channel_link)
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка при проверке подписки: {e}")
            return await handler(event, data)

    async def _capture_start_payload(
        self,
        state: Optional[FSMContext],
        event: TelegramObject,
        bot: Optional[Bot] = None,
    ) -> None:
        if not state:
            return

        message: Optional[Message] = None
        if isinstance(event, Message):
            message = event
        elif isinstance(event, CallbackQuery):
            message = event.message
        elif isinstance(event, Update):
            message = event.message

        if not message or not message.text:
            return

        text = message.text.strip()
        if not text.startswith("/start"):
            return

        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1]:
            return

        payload = parts[1]

        data = await state.get_data() or {}
        if data.get("pending_start_payload") != payload:
            data["pending_start_payload"] = payload
            await state.set_data(data)
            logger.debug("💾 Сохранен start payload %s для последующей обработки", payload)

        if bot and message.from_user:
            await self._try_send_campaign_visit_notification(
                bot,
                message.from_user,
                state,
                payload,
            )

    async def _try_send_campaign_visit_notification(
        self,
        bot: Bot,
        telegram_user: types.User,
        state: FSMContext,
        payload: str,
    ) -> None:
        try:
            data = await state.get_data() or {}
        except Exception as error:
            logger.error(
                "❌ Не удалось получить данные состояния для уведомления по кампании %s: %s",
                payload,
                error,
            )
            return

        if data.get("campaign_notification_sent"):
            return

        async for db in get_db():
            try:
                campaign = await get_campaign_by_start_parameter(
                    db,
                    payload,
                    only_active=True,
                )
                if not campaign:
                    break

                user = await get_user_by_telegram_id(db, telegram_user.id)

                notification_service = AdminNotificationService(bot)
                sent = await notification_service.send_campaign_link_visit_notification(
                    db,
                    telegram_user,
                    campaign,
                    user,
                )
                if sent:
                    await state.update_data(campaign_notification_sent=True)
                break
            except Exception as error:
                logger.error(
                    "❌ Ошибка отправки уведомления о переходе по кампании %s: %s",
                    payload,
                    error,
                )
            finally:
                break

    async def _deactivate_trial_subscription(self, telegram_id: int) -> None:
        async for db in get_db():
            try:
                user = await get_user_by_telegram_id(db, telegram_id)
                if not user or not user.subscription:
                    logger.debug(
                        "⚠️ Пользователь %s отсутствует или не имеет подписки — пропускаем деактивацию",
                        telegram_id,
                    )
                    break

                subscription = user.subscription
                if (not subscription.is_trial or
                        subscription.status != SubscriptionStatus.ACTIVE.value):
                    logger.debug(
                        "ℹ️ Подписка пользователя %s не требует деактивации (trial=%s, status=%s)",
                        telegram_id,
                        subscription.is_trial,
                        subscription.status,
                    )
                    break

                await deactivate_subscription(db, subscription)
                logger.info(
                    "🚫 Триальная подписка пользователя %s отключена после отписки от канала",
                    telegram_id,
                )

                if user.remnawave_uuid:
                    service = SubscriptionService()
                    try:
                        await service.disable_remnawave_user(user.remnawave_uuid)
                    except Exception as api_error:
                        logger.error(
                            "❌ Не удалось отключить пользователя RemnaWave %s: %s",
                            user.remnawave_uuid,
                            api_error,
                        )
            except Exception as db_error:
                logger.error(
                    "❌ Ошибка деактивации подписки пользователя %s после отписки: %s",
                    telegram_id,
                    db_error,
                )
            finally:
                break

    @staticmethod
    async def _deny_message(event: TelegramObject, bot: Bot, channel_link: str):
        logger.debug("🚫 Отправляем сообщение о необходимости подписки")

        user = None
        if isinstance(event, (Message, CallbackQuery)):
            user = getattr(event, "from_user", None)
        elif isinstance(event, Update):
            if event.message and event.message.from_user:
                user = event.message.from_user
            elif event.callback_query and event.callback_query.from_user:
                user = event.callback_query.from_user

        language = DEFAULT_LANGUAGE
        if user and user.language_code:
            language = user.language_code.split('-')[0]

        texts = get_texts(language)
        channel_sub_kb = get_channel_sub_keyboard(channel_link, language=language)
        text = texts.t(
            "CHANNEL_REQUIRED_TEXT",
            "🔒 Для использования бота подпишитесь на новостной канал, чтобы получать уведомления о новых возможностях и обновлениях бота. Спасибо!",
        )

        try:
            if isinstance(event, Message):
                return await event.answer(text, reply_markup=channel_sub_kb)
            elif isinstance(event, CallbackQuery):
                try:
                    return await event.message.edit_text(text, reply_markup=channel_sub_kb)
                except TelegramBadRequest as e:
                    if "message is not modified" in str(e).lower():
                        logger.debug("ℹ️ Сообщение уже содержит текст проверки подписки, пропускаем редактирование")
                        return await event.answer(text, show_alert=True)
                    raise
            elif isinstance(event, Update) and event.message:
                return await bot.send_message(event.message.chat.id, text, reply_markup=channel_sub_kb)
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке сообщения о подписке: {e}")
