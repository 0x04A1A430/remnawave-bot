"""
Сервис стартового уведомления бота.

Отправляет красивое сообщение с информацией о системе при запуске бота.
"""

import logging
import os
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.models import User, UserStatus
from app.external.remnawave_api import RemnaWaveAPI, test_api_connection
from app.utils.timezone import format_local_datetime


logger = logging.getLogger(__name__)


class StartupNotificationService:
    """Сервис для отправки стартового уведомления в админский чат."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.chat_id = getattr(settings, 'ADMIN_NOTIFICATIONS_CHAT_ID', None)
        self.topic_id = getattr(settings, 'ADMIN_NOTIFICATIONS_TOPIC_ID', None)
        self.enabled = getattr(settings, 'ADMIN_NOTIFICATIONS_ENABLED', False)

    def _get_version(self) -> str:
        """Получает версию из переменной окружения VERSION."""
        version = os.getenv('VERSION', '').strip()
        if version:
            return version
        return 'dev'

    async def _get_users_count(self) -> int:
        """Получает количество активных пользователей в базе."""
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(func.count(User.id)).where(User.status == UserStatus.ACTIVE.value))
                return result.scalar() or 0
        except Exception as e:
            logger.error(f'Ошибка получения количества пользователей: {e}')
            return 0

    async def _get_total_balance(self) -> int:
        """Получает сумму балансов всех пользователей в копейках."""
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(func.coalesce(func.sum(User.balance_kopeks), 0)).where(
                        User.status == UserStatus.ACTIVE.value
                    )
                )
                return result.scalar() or 0
        except Exception as e:
            logger.error(f'Ошибка получения суммы балансов: {e}')
            return 0

    async def _check_remnawave_connection(self) -> tuple[bool, str]:
        """
        Проверяет соединение с панелью RemnaWave.

        Returns:
            Tuple[bool, str]: (is_connected, status_message)
        """
        try:
            auth_params = settings.get_remnawave_auth_params()
            base_url = (auth_params.get('base_url') or '').strip()
            api_key = (auth_params.get('api_key') or '').strip()

            if not base_url or not api_key:
                return False, 'Не настроен'

            secret_key = (auth_params.get('secret_key') or '').strip() or None
            username = (auth_params.get('username') or '').strip() or None
            password = (auth_params.get('password') or '').strip() or None
            caddy_token = (auth_params.get('caddy_token') or '').strip() or None
            auth_type = (auth_params.get('auth_type') or 'api_key').strip()

            api = RemnaWaveAPI(
                base_url=base_url,
                api_key=api_key,
                secret_key=secret_key,
                username=username,
                password=password,
                caddy_token=caddy_token,
                auth_type=auth_type,
            )

            async with api:
                is_connected = await test_api_connection(api)
                if is_connected:
                    return True, 'Подключено'
                return False, 'Недоступна'

        except Exception as e:
            logger.error(f'Ошибка проверки соединения с RemnaWave: {e}')
            return False, 'Ошибка подключения'

    def _format_balance(self, kopeks: int) -> str:
        """Форматирует баланс в рублях."""
        rubles = kopeks / 100
        if rubles >= 1_000_000:
            return f'{rubles / 1_000_000:.2f}M RUB'
        if rubles >= 1_000:
            return f'{rubles / 1_000:.1f}K RUB'
        return f'{rubles:.2f} RUB'

    async def send_startup_notification(self) -> bool:
        """
        Отправляет стартовое уведомление в админский чат.

        Returns:
            bool: True если сообщение отправлено успешно
        """
        if not self.enabled or not self.chat_id:
            logger.debug('Стартовое уведомление отключено или chat_id не задан')
            return False

        try:
            version = self._get_version()
            users_count = await self._get_users_count()
            total_balance_kopeks = await self._get_total_balance()
            remnawave_connected, remnawave_status = await self._check_remnawave_connection()

            # Иконка статуса RemnaWave
            remnawave_icon = '🟢' if remnawave_connected else '🔴'

            # Формируем системную информацию для blockquote
            system_info_lines = [
                f'Версия: {version}',
                f'Пользователей: {users_count:,}'.replace(',', ' '),
                f'Сумма балансов: {self._format_balance(total_balance_kopeks)}',
                f'{remnawave_icon} RemnaWave: {remnawave_status}',
            ]
            system_info = '\n'.join(system_info_lines)

            timestamp = format_local_datetime(datetime.utcnow(), '%d.%m.%Y %H:%M:%S')

            message = (
                f'<b>Remnawave Bedolaga Bot</b>\n\n'
                f'<blockquote expandable>{system_info}</blockquote>\n\n'
                f'<i>{timestamp}</i>'
            )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text='Поставить звезду',
                            url='https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot',
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text='Вебкабинет',
                            url='https://github.com/BEDOLAGA-DEV/bedolaga-cabinet',
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text='Сообщество',
                            url='https://t.me/+wTdMtSWq8YdmZmVi',
                        ),
                    ],
                ]
            )

            message_kwargs: dict = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'HTML',
                'reply_markup': keyboard,
                'disable_web_page_preview': True,
            }

            if self.topic_id:
                message_kwargs['message_thread_id'] = self.topic_id

            await self.bot.send_message(**message_kwargs)
            logger.info(f'Стартовое уведомление отправлено в чат {self.chat_id}')
            return True

        except Exception as e:
            logger.error(f'Ошибка отправки стартового уведомления: {e}')
            return False


async def send_bot_startup_notification(bot: Bot) -> bool:
    """
    Удобная функция для отправки стартового уведомления.

    Args:
        bot: Экземпляр бота aiogram

    Returns:
        bool: True если уведомление отправлено успешно
    """
    service = StartupNotificationService(bot)
    return await service.send_startup_notification()
