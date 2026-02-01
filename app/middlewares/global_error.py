import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, TelegramObject

from app.config import settings
from app.utils.timezone import format_local_datetime


logger = logging.getLogger(__name__)

# Троттлинг для предотвращения спама ошибками
_last_error_notification: datetime | None = None
_error_notification_cooldown = timedelta(minutes=5)  # Минимум 5 минут между уведомлениями
_error_buffer: list[tuple[str, str, str]] = []  # (error_type, error_message, traceback)
_max_buffer_size = 10


class GlobalErrorMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramBadRequest as e:
            return await self._handle_telegram_error(event, e)
        except Exception as e:
            logger.error('Неожиданная ошибка в GlobalErrorMiddleware: %s', e, exc_info=True)
            # Отправляем уведомление об ошибке в админский чат
            bot = data.get('bot')
            if bot:
                user_info = self._get_user_info(event)
                schedule_error_notification(bot, e, f'Пользователь: {user_info}')
            raise

    async def _handle_telegram_error(self, event: TelegramObject, error: TelegramBadRequest):
        error_message = str(error).lower()

        if self._is_old_query_error(error_message):
            return await self._handle_old_query(event, error)
        if self._is_message_not_modified_error(error_message):
            return await self._handle_message_not_modified(event, error)
        if self._is_topic_required_error(error_message):
            # Канал с топиками — просто игнорируем
            logger.debug('[GlobalErrorMiddleware] Игнорируем ошибку топика: %s', error)
            return None
        if self._is_bad_request_error(error_message):
            return await self._handle_bad_request(event, error)
        logger.error('Неизвестная Telegram API ошибка: %s', error)
        raise error

    def _is_old_query_error(self, error_message: str) -> bool:
        return any(
            phrase in error_message
            for phrase in ['query is too old', 'query id is invalid', 'response timeout expired']
        )

    def _is_message_not_modified_error(self, error_message: str) -> bool:
        return 'message is not modified' in error_message

    def _is_bad_request_error(self, error_message: str) -> bool:
        return any(
            phrase in error_message
            for phrase in ['message not found', 'chat not found', 'bot was blocked by the user', 'user is deactivated']
        )

    def _is_topic_required_error(self, error_message: str) -> bool:
        return any(
            phrase in error_message
            for phrase in ['topic must be specified', 'topic_closed', 'topic_deleted', 'forum_closed']
        )

    async def _handle_old_query(self, event: TelegramObject, error: TelegramBadRequest):
        if isinstance(event, CallbackQuery):
            user_info = self._get_user_info(event)
            logger.warning("[GlobalErrorMiddleware] Игнорируем устаревший callback '%s' от %s", event.data, user_info)
        else:
            logger.warning('[GlobalErrorMiddleware] Игнорируем устаревший запрос: %s', error)

    async def _handle_message_not_modified(self, event: TelegramObject, error: TelegramBadRequest):
        logger.debug('[GlobalErrorMiddleware] Сообщение не было изменено: %s', error)

        if isinstance(event, CallbackQuery):
            try:
                await event.answer()
                logger.debug("Успешно ответили на callback после 'message not modified'")
            except TelegramBadRequest as answer_error:
                if not self._is_old_query_error(str(answer_error).lower()):
                    logger.error('Ошибка при ответе на callback: %s', answer_error)

    async def _handle_bad_request(self, event: TelegramObject, error: TelegramBadRequest):
        error_message = str(error).lower()

        if 'bot was blocked' in error_message:
            user_info = self._get_user_info(event) if hasattr(event, 'from_user') else 'Unknown'
            logger.info('[GlobalErrorMiddleware] Бот заблокирован пользователем %s', user_info)
            return
        if 'user is deactivated' in error_message:
            user_info = self._get_user_info(event) if hasattr(event, 'from_user') else 'Unknown'
            logger.info('[GlobalErrorMiddleware] Пользователь деактивирован %s', user_info)
            return
        if 'chat not found' in error_message or 'message not found' in error_message:
            logger.warning('[GlobalErrorMiddleware] Чат или сообщение не найдено: %s', error)
            return
        logger.error('[GlobalErrorMiddleware] Неизвестная bad request ошибка: %s', error)
        raise error

    def _get_user_info(self, event: TelegramObject) -> str:
        if hasattr(event, 'from_user') and event.from_user:
            if event.from_user.username:
                return f'@{event.from_user.username}'
            return f'ID:{event.from_user.id}'
        return 'Unknown'


class ErrorStatisticsMiddleware(BaseMiddleware):
    def __init__(self):
        self.error_counts = {
            'old_queries': 0,
            'message_not_modified': 0,
            'bot_blocked': 0,
            'user_deactivated': 0,
            'other_errors': 0,
        }

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramBadRequest as e:
            self._count_error(e)
            raise

    def _count_error(self, error: TelegramBadRequest):
        error_message = str(error).lower()

        if 'query is too old' in error_message:
            self.error_counts['old_queries'] += 1
        elif 'message is not modified' in error_message:
            self.error_counts['message_not_modified'] += 1
        elif 'bot was blocked' in error_message:
            self.error_counts['bot_blocked'] += 1
        elif 'user is deactivated' in error_message:
            self.error_counts['user_deactivated'] += 1
        else:
            self.error_counts['other_errors'] += 1

    def get_statistics(self) -> dict:
        return self.error_counts.copy()

    def reset_statistics(self):
        for key in self.error_counts:
            self.error_counts[key] = 0


async def send_error_to_admin_chat(bot: Bot, error: Exception, context: str = '') -> bool:
    """
    Отправляет уведомление об ошибке в админский чат с троттлингом.

    Args:
        bot: Экземпляр бота
        error: Исключение
        context: Дополнительный контекст (например, информация о пользователе)

    Returns:
        bool: True если уведомление отправлено
    """
    global _last_error_notification

    chat_id = getattr(settings, 'ADMIN_NOTIFICATIONS_CHAT_ID', None)
    topic_id = getattr(settings, 'ADMIN_NOTIFICATIONS_TOPIC_ID', None)
    enabled = getattr(settings, 'ADMIN_NOTIFICATIONS_ENABLED', False)

    if not enabled or not chat_id:
        return False

    error_type = type(error).__name__
    error_message = str(error)[:500]
    tb_str = traceback.format_exc()

    # Добавляем в буфер
    _error_buffer.append((error_type, error_message, tb_str))
    if len(_error_buffer) > _max_buffer_size:
        _error_buffer.pop(0)

    # Проверяем троттлинг
    now = datetime.utcnow()
    if _last_error_notification and (now - _last_error_notification) < _error_notification_cooldown:
        logger.debug('Ошибка добавлена в буфер, троттлинг активен: %s', error_type)
        return False

    _last_error_notification = now

    try:
        timestamp = format_local_datetime(now, '%d.%m.%Y %H:%M:%S')

        # Формируем лог-файл со всеми ошибками из буфера
        log_lines = [
            'ERROR REPORT',
            '=' * 50,
            f'Timestamp: {timestamp}',
            f'Errors in buffer: {len(_error_buffer)}',
            '',
        ]

        for i, (err_type, err_msg, err_tb) in enumerate(_error_buffer):
            log_lines.extend(
                [
                    f'{"=" * 50}',
                    f'ERROR #{i}: {err_type}',
                    f'{"=" * 50}',
                    f'Message: {err_msg}',
                    '',
                    'Traceback:',
                    err_tb,
                    '',
                ]
            )

        log_content = '\n'.join(log_lines)

        # Очищаем буфер после отправки
        errors_count = len(_error_buffer)
        _error_buffer.clear()

        file_name = f'error_report_{now.strftime("%Y%m%d_%H%M%S")}.txt'
        file = BufferedInputFile(
            file=log_content.encode('utf-8'),
            filename=file_name,
        )

        message_text = (
            f'<b>Remnawave Bedolaga Bot</b>\n\n'
            f'⚠️ Ошибка во время работы\n\n'
            f'<b>Тип:</b> <code>{error_type}</code>\n'
            f'<b>Ошибок в отчёте:</b> {errors_count}\n'
        )
        if context:
            message_text += f'<b>Контекст:</b> {context}\n'
        message_text += f'\n<i>{timestamp}</i>'

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text='💬 Сообщить разработчику',
                        url='https://t.me/fringg',
                    ),
                ],
            ]
        )

        message_kwargs: dict = {
            'chat_id': chat_id,
            'document': file,
            'caption': message_text,
            'parse_mode': ParseMode.HTML,
            'reply_markup': keyboard,
        }

        if topic_id:
            message_kwargs['message_thread_id'] = topic_id

        await bot.send_document(**message_kwargs)
        logger.info('Уведомление об ошибке отправлено в чат %s', chat_id)
        return True

    except Exception as e:
        logger.error('Ошибка отправки уведомления об ошибке: %s', e)
        return False


def schedule_error_notification(bot: Bot, error: Exception, context: str = '') -> None:
    """
    Планирует отправку уведомления об ошибке в фоне (не блокирует).

    Args:
        bot: Экземпляр бота
        error: Исключение
        context: Дополнительный контекст
    """
    asyncio.create_task(send_error_to_admin_chat(bot, error, context))
