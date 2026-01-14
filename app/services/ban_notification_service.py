"""
Сервис для отправки уведомлений от ban системы пользователям
"""
import logging
from typing import Optional, Tuple
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.models import User
from app.services.remnawave_service import remnawave_service
from app.config import settings


logger = logging.getLogger(__name__)


class BanNotificationService:
    """Сервис для отправки уведомлений о банах пользователям"""

    def __init__(self):
        self._bot: Optional[Bot] = None

    def set_bot(self, bot: Bot):
        """Установить инстанс бота для отправки сообщений"""
        self._bot = bot

    async def _find_user_by_identifier(
        self,
        db: AsyncSession,
        user_identifier: str
    ) -> Optional[User]:
        """
        Найти пользователя по email или user_id из Remnawave Panel

        Args:
            db: Сессия БД
            user_identifier: Email или user_id пользователя

        Returns:
            User или None если не найден
        """
        # Сначала пытаемся получить telegram_id через remnawave_service
        try:
            telegram_id = await remnawave_service.get_telegram_id_by_email(user_identifier)
            if telegram_id:
                # Ищем пользователя по telegram_id
                result = await db.execute(
                    select(User).where(User.telegram_id == telegram_id)
                )
                user = result.scalar_one_or_none()
                if user:
                    return user
        except Exception as e:
            logger.warning(f"Не удалось получить telegram_id через remnawave: {e}")

        # Если не нашли через remnawave, пытаемся искать по email в подписках
        # (это может быть полезно если у пользователя есть подписка с таким email)
        try:
            # Импортируем здесь чтобы избежать циклических импортов
            from app.database.models import Subscription

            result = await db.execute(
                select(User)
                .join(Subscription)
                .where(Subscription.email == user_identifier)
                .limit(1)
            )
            user = result.scalar_one_or_none()
            if user:
                return user
        except Exception as e:
            logger.warning(f"Ошибка поиска пользователя по email в подписках: {e}")

        return None

    async def send_punishment_notification(
        self,
        db: AsyncSession,
        user_identifier: str,
        username: str,
        ip_count: int,
        limit: int,
        ban_minutes: int
    ) -> Tuple[bool, str, Optional[int]]:
        """
        Отправить уведомление о блокировке пользователю

        Returns:
            (success, message, telegram_id)
        """
        if not self._bot:
            return False, "Бот не инициализирован", None

        # Находим пользователя
        user = await self._find_user_by_identifier(db, user_identifier)
        if not user:
            logger.warning(f"Пользователь {user_identifier} не найден в базе данных")
            return False, f"Пользователь не найден: {user_identifier}", None

        # Формируем сообщение из настроек
        message_text = settings.BAN_MSG_PUNISHMENT.format(
            ip_count=ip_count,
            limit=limit,
            ban_minutes=ban_minutes
        )

        # Отправляем сообщение
        try:
            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="HTML"
            )
            logger.info(
                f"Уведомление о бане отправлено пользователю {username} "
                f"(telegram_id: {user.telegram_id})"
            )
            return True, "Уведомление отправлено", user.telegram_id

        except TelegramAPIError as e:
            logger.error(
                f"Ошибка отправки уведомления пользователю {username} "
                f"(telegram_id: {user.telegram_id}): {e}"
            )
            return False, f"Ошибка Telegram API: {str(e)}", user.telegram_id

    async def send_enabled_notification(
        self,
        db: AsyncSession,
        user_identifier: str,
        username: str
    ) -> Tuple[bool, str, Optional[int]]:
        """
        Отправить уведомление о разблокировке пользователю

        Returns:
            (success, message, telegram_id)
        """
        if not self._bot:
            return False, "Бот не инициализирован", None

        # Находим пользователя
        user = await self._find_user_by_identifier(db, user_identifier)
        if not user:
            logger.warning(f"Пользователь {user_identifier} не найден в базе данных")
            return False, f"Пользователь не найден: {user_identifier}", None

        # Формируем сообщение из настроек
        message_text = settings.BAN_MSG_ENABLED

        # Отправляем сообщение
        try:
            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="HTML"
            )
            logger.info(
                f"Уведомление о разбане отправлено пользователю {username} "
                f"(telegram_id: {user.telegram_id})"
            )
            return True, "Уведомление отправлено", user.telegram_id

        except TelegramAPIError as e:
            logger.error(
                f"Ошибка отправки уведомления пользователю {username} "
                f"(telegram_id: {user.telegram_id}): {e}"
            )
            return False, f"Ошибка Telegram API: {str(e)}", user.telegram_id

    async def send_warning_notification(
        self,
        db: AsyncSession,
        user_identifier: str,
        username: str,
        warning_message: str
    ) -> Tuple[bool, str, Optional[int]]:
        """
        Отправить предупреждение пользователю

        Returns:
            (success, message, telegram_id)
        """
        if not self._bot:
            return False, "Бот не инициализирован", None

        # Находим пользователя
        user = await self._find_user_by_identifier(db, user_identifier)
        if not user:
            logger.warning(f"Пользователь {user_identifier} не найден в базе данных")
            return False, f"Пользователь не найден: {user_identifier}", None

        # Формируем сообщение из настроек
        message_text = settings.BAN_MSG_WARNING.format(
            warning_message=warning_message
        )

        # Отправляем сообщение
        try:
            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="HTML"
            )
            logger.info(
                f"Предупреждение отправлено пользователю {username} "
                f"(telegram_id: {user.telegram_id})"
            )
            return True, "Предупреждение отправлено", user.telegram_id

        except TelegramAPIError as e:
            logger.error(
                f"Ошибка отправки предупреждения пользователю {username} "
                f"(telegram_id: {user.telegram_id}): {e}"
            )
            return False, f"Ошибка Telegram API: {str(e)}", user.telegram_id

    async def send_network_wifi_notification(
        self,
        db: AsyncSession,
        user_identifier: str,
        username: str,
        ban_minutes: int,
        network_type: Optional[str] = None,
        node_name: Optional[str] = None
    ) -> Tuple[bool, str, Optional[int]]:
        """
        Отправить уведомление о блокировке за использование WiFi сети

        Returns:
            (success, message, telegram_id)
        """
        if not self._bot:
            return False, "Бот не инициализирован", None

        # Находим пользователя
        user = await self._find_user_by_identifier(db, user_identifier)
        if not user:
            logger.warning(f"Пользователь {user_identifier} не найден в базе данных")
            return False, f"Пользователь не найден: {user_identifier}", None

        # Формируем сообщение из настроек
        network_info = f"Тип сети: <b>{network_type}</b>\n" if network_type else ""
        node_info = f"Сервер: <b>{node_name}</b>\n" if node_name else ""

        message_text = settings.BAN_MSG_WIFI.format(
            ban_minutes=ban_minutes,
            network_info=network_info,
            node_info=node_info
        )

        # Отправляем сообщение
        try:
            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="HTML"
            )
            logger.info(
                f"Уведомление о WiFi бане отправлено пользователю {username} "
                f"(telegram_id: {user.telegram_id})"
            )
            return True, "Уведомление отправлено", user.telegram_id

        except TelegramAPIError as e:
            logger.error(
                f"Ошибка отправки WiFi уведомления пользователю {username} "
                f"(telegram_id: {user.telegram_id}): {e}"
            )
            return False, f"Ошибка Telegram API: {str(e)}", user.telegram_id

    async def send_network_mobile_notification(
        self,
        db: AsyncSession,
        user_identifier: str,
        username: str,
        ban_minutes: int,
        network_type: Optional[str] = None,
        node_name: Optional[str] = None
    ) -> Tuple[bool, str, Optional[int]]:
        """
        Отправить уведомление о блокировке за использование мобильной сети

        Returns:
            (success, message, telegram_id)
        """
        if not self._bot:
            return False, "Бот не инициализирован", None

        # Находим пользователя
        user = await self._find_user_by_identifier(db, user_identifier)
        if not user:
            logger.warning(f"Пользователь {user_identifier} не найден в базе данных")
            return False, f"Пользователь не найден: {user_identifier}", None

        # Формируем сообщение из настроек
        network_info = f"Тип сети: <b>{network_type}</b>\n" if network_type else ""
        node_info = f"Сервер: <b>{node_name}</b>\n" if node_name else ""

        message_text = settings.BAN_MSG_MOBILE.format(
            ban_minutes=ban_minutes,
            network_info=network_info,
            node_info=node_info
        )

        # Отправляем сообщение
        try:
            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="HTML"
            )
            logger.info(
                f"Уведомление о Mobile бане отправлено пользователю {username} "
                f"(telegram_id: {user.telegram_id})"
            )
            return True, "Уведомление отправлено", user.telegram_id

        except TelegramAPIError as e:
            logger.error(
                f"Ошибка отправки Mobile уведомления пользователю {username} "
                f"(telegram_id: {user.telegram_id}): {e}"
            )
            return False, f"Ошибка Telegram API: {str(e)}", user.telegram_id


# Глобальный экземпляр сервиса
ban_notification_service = BanNotificationService()
