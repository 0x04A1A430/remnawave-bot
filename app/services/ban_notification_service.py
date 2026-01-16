"""
Сервис для отправки уведомлений от ban системы пользователям
"""
import logging
from typing import Optional, Tuple
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.models import User
from app.services.remnawave_service import remnawave_service
from app.config import settings


logger = logging.getLogger(__name__)


def get_delete_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой удаления уведомления"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить", callback_data="ban_notify:delete")]
    ])


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
        ban_minutes: int,
        node_name: Optional[str] = None
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

        # Формируем информацию о ноде (заметно выделяем)
        node_info = f"🖥 <b>Нода:</b> <code>{node_name}</code>" if node_name else ""

        # Формируем сообщение из настроек
        # Используем безопасное форматирование - если {node_info} отсутствует в шаблоне, не будет ошибки
        format_vars = {
            "ip_count": ip_count,
            "limit": limit,
            "ban_minutes": ban_minutes,
            "node_info": node_info
        }
        try:
            message_text = settings.BAN_MSG_PUNISHMENT.format(**format_vars)
        except KeyError:
            # Старый шаблон без {node_info} - форматируем без него
            message_text = settings.BAN_MSG_PUNISHMENT.format(
                ip_count=ip_count,
                limit=limit,
                ban_minutes=ban_minutes
            )
            # Добавляем информацию о ноде в конец, если она есть
            if node_info:
                message_text = message_text.rstrip() + f"\n\n{node_info.rstrip()}"

        # Отправляем сообщение с кнопкой удаления
        try:
            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=get_delete_keyboard()
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

        # Отправляем сообщение с кнопкой удаления
        try:
            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=get_delete_keyboard()
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

        # Отправляем сообщение с кнопкой удаления
        try:
            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=get_delete_keyboard()
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

        # Формируем сообщение из настроек (заметно выделяем)
        network_info = f"├ 🌐 Сеть: <b>{network_type}</b>\n" if network_type else ""
        node_info = f"🖥 <b>Нода:</b> <code>{node_name}</code>" if node_name else ""

        logger.info(f"WiFi notification: node_name={node_name!r}, node_info={node_info!r}")

        # Безопасное форматирование
        format_vars = {
            "ban_minutes": ban_minutes,
            "network_info": network_info,
            "node_info": node_info
        }
        try:
            message_text = settings.BAN_MSG_WIFI.format(**format_vars)
        except KeyError:
            logger.warning("BAN_MSG_WIFI template missing placeholders, adding node_info to end")
            message_text = settings.BAN_MSG_WIFI.format(ban_minutes=ban_minutes)
            extra_info = (network_info + node_info).strip()
            if extra_info:
                message_text = message_text.rstrip() + f"\n\n{extra_info}"

        # Отправляем сообщение с кнопкой удаления
        try:
            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=get_delete_keyboard()
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

        # Формируем сообщение из настроек (заметно выделяем)
        network_info = f"├ 🌐 Сеть: <b>{network_type}</b>\n" if network_type else ""
        node_info = f"🖥 <b>Нода:</b> <code>{node_name}</code>" if node_name else ""

        # Безопасное форматирование
        format_vars = {
            "ban_minutes": ban_minutes,
            "network_info": network_info,
            "node_info": node_info
        }
        try:
            message_text = settings.BAN_MSG_MOBILE.format(**format_vars)
        except KeyError:
            message_text = settings.BAN_MSG_MOBILE.format(ban_minutes=ban_minutes)
            extra_info = (network_info + node_info).strip()
            if extra_info:
                message_text = message_text.rstrip() + f"\n\n{extra_info}"

        # Отправляем сообщение с кнопкой удаления
        try:
            await self._bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=get_delete_keyboard()
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
