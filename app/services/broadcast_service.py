from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.exc import InterfaceError, SQLAlchemyError

from app.database.database import AsyncSessionLocal
from app.database.models import BroadcastHistory
from app.handlers.admin.messages import (
    create_broadcast_keyboard,
    get_custom_users,
    get_target_users,
)


if TYPE_CHECKING:
    from app.cabinet.services.email_service import EmailService


logger = logging.getLogger(__name__)


VALID_MEDIA_TYPES = {'photo', 'video', 'document'}
LARGE_BROADCAST_THRESHOLD = 20_000
PROGRESS_UPDATE_STEP = 5_000

# Email broadcast rate limiting: max 8 emails per second
EMAIL_RATE_LIMIT = 8
EMAIL_BATCH_SIZE = 50


@dataclass(slots=True)
class BroadcastMediaConfig:
    type: str
    file_id: str
    caption: str | None = None


@dataclass(slots=True)
class BroadcastConfig:
    target: str
    message_text: str
    selected_buttons: list[str]
    media: BroadcastMediaConfig | None = None
    initiator_name: str | None = None


@dataclass
class EmailBroadcastConfig:
    """Configuration for email broadcast."""

    target: str
    email_subject: str
    email_html_content: str
    initiator_name: str | None = None


@dataclass(slots=True)
class _BroadcastTask:
    task: asyncio.Task
    cancel_event: asyncio.Event


class BroadcastService:
    """Handles broadcast execution triggered from the admin web API."""

    def __init__(self) -> None:
        self._bot: Bot | None = None
        self._tasks: dict[int, _BroadcastTask] = {}
        self._lock = asyncio.Lock()

    def set_bot(self, bot: Bot) -> None:
        self._bot = bot

    def is_running(self, broadcast_id: int) -> bool:
        task_entry = self._tasks.get(broadcast_id)
        return bool(task_entry and not task_entry.task.done())

    async def start_broadcast(self, broadcast_id: int, config: BroadcastConfig) -> None:
        if self._bot is None:
            logger.error('Невозможно запустить рассылку %s: бот не инициализирован', broadcast_id)
            await self._mark_failed(broadcast_id)
            return

        cancel_event = asyncio.Event()

        async with self._lock:
            if broadcast_id in self._tasks and not self._tasks[broadcast_id].task.done():
                logger.warning('Рассылка %s уже запущена', broadcast_id)
                return

            task = asyncio.create_task(
                self._run_broadcast(broadcast_id, config, cancel_event),
                name=f'broadcast-{broadcast_id}',
            )
            self._tasks[broadcast_id] = _BroadcastTask(task=task, cancel_event=cancel_event)
            task.add_done_callback(lambda _: self._tasks.pop(broadcast_id, None))

    async def request_stop(self, broadcast_id: int) -> bool:
        async with self._lock:
            task_entry = self._tasks.get(broadcast_id)
            if not task_entry:
                return False

            task_entry.cancel_event.set()
            return True

    async def _run_broadcast(
        self,
        broadcast_id: int,
        config: BroadcastConfig,
        cancel_event: asyncio.Event,
    ) -> None:
        sent_count = 0
        failed_count = 0

        try:
            if cancel_event.is_set():
                await self._mark_cancelled(broadcast_id, sent_count, failed_count)
                return

            async with AsyncSessionLocal() as session:
                broadcast = await session.get(BroadcastHistory, broadcast_id)
                if not broadcast:
                    logger.error('Запись рассылки %s не найдена в БД', broadcast_id)
                    return

                broadcast.status = 'in_progress'
                broadcast.sent_count = 0
                broadcast.failed_count = 0
                await session.commit()

            recipients = await self._fetch_recipients(config.target)

            async with AsyncSessionLocal() as session:
                broadcast = await session.get(BroadcastHistory, broadcast_id)
                if not broadcast:
                    logger.error('Запись рассылки %s удалена до запуска', broadcast_id)
                    return

                broadcast.total_count = len(recipients)
                await session.commit()

            if cancel_event.is_set():
                await self._mark_cancelled(broadcast_id, sent_count, failed_count)
                return

            if not recipients:
                logger.info('Рассылка %s: получатели не найдены', broadcast_id)
                await self._mark_finished(broadcast_id, sent_count, failed_count, cancelled=False)
                return

            keyboard = self._build_keyboard(config.selected_buttons)

            if len(recipients) > LARGE_BROADCAST_THRESHOLD:
                logger.info('Запускаем стабильный режим рассылки для %s получателей', len(recipients))
                (
                    sent_count,
                    failed_count,
                    cancelled_during_run,
                ) = await self._run_resilient_broadcast(
                    broadcast_id,
                    recipients,
                    config,
                    keyboard,
                    cancel_event,
                )
            else:
                (
                    sent_count,
                    failed_count,
                    cancelled_during_run,
                ) = await self._run_standard_broadcast(
                    broadcast_id,
                    recipients,
                    config,
                    keyboard,
                    cancel_event,
                )

            if cancelled_during_run:
                logger.info(
                    'Рассылка %s была отменена во время выполнения, финальный статус уже установлен',
                    broadcast_id,
                )
                return

            if cancel_event.is_set():
                logger.info(
                    'Запрос на отмену рассылки %s пришел после завершения отправки, фиксируем итоговый статус',
                    broadcast_id,
                )

            await self._mark_finished(
                broadcast_id,
                sent_count,
                failed_count,
                cancelled=False,
            )

        except asyncio.CancelledError:
            await self._mark_cancelled(broadcast_id, sent_count, failed_count)
            raise
        except Exception as exc:
            logger.exception('Критическая ошибка при выполнении рассылки %s: %s', broadcast_id, exc)
            await self._mark_failed(broadcast_id, sent_count, failed_count)

    async def _fetch_recipients(self, target: str):
        async with AsyncSessionLocal() as session:
            if target.startswith('custom_'):
                criteria = target[len('custom_') :]
                return await get_custom_users(session, criteria)
            return await get_target_users(session, target)

    async def _run_standard_broadcast(
        self,
        broadcast_id: int,
        recipients: list,
        config: BroadcastConfig,
        keyboard: InlineKeyboardMarkup | None,
        cancel_event: asyncio.Event,
    ) -> tuple[int, int, bool]:
        """Базовый режим рассылки для небольших списков."""

        sent_count = 0
        failed_count = 0

        # Ограничение на количество одновременных отправок
        semaphore = asyncio.Semaphore(20)

        async def send_single_message(user):
            """Отправляет одно сообщение с семафором ограничения"""
            async with semaphore:
                if cancel_event.is_set():
                    return False

                telegram_id = getattr(user, 'telegram_id', None)
                if telegram_id is None:
                    # Email-пользователи без telegram_id - пропускаем (не считаем ошибкой)
                    return None

                try:
                    await self._deliver_message(telegram_id, config, keyboard)
                    return True
                except Exception as exc:
                    logger.error(
                        'Ошибка отправки рассылки %s пользователю %s: %s',
                        broadcast_id,
                        telegram_id,
                        exc,
                    )
                    return False

        # Отправляем сообщения пакетами для эффективности
        batch_size = 100
        skipped_count = 0
        for i in range(0, len(recipients), batch_size):
            if cancel_event.is_set():
                await self._mark_cancelled(broadcast_id, sent_count, failed_count)
                return sent_count, failed_count, True

            batch = recipients[i : i + batch_size]
            tasks = [send_single_message(user) for user in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if result is True:
                    sent_count += 1
                elif result is None:
                    # Email-пользователи - пропускаем без ошибки
                    skipped_count += 1
                else:
                    failed_count += 1

            # Небольшая задержка между пакетами для снижения нагрузки на API
            await asyncio.sleep(0.1)

        return sent_count, failed_count, False

    async def _run_resilient_broadcast(
        self,
        broadcast_id: int,
        recipients: list,
        config: BroadcastConfig,
        keyboard: InlineKeyboardMarkup | None,
        cancel_event: asyncio.Event,
    ) -> tuple[int, int, bool]:
        """Режим рассылки с периодическим обновлением статуса для больших списков."""

        sent_count = 0
        failed_count = 0

        # Ограничение на количество одновременных отправок
        semaphore = asyncio.Semaphore(15)

        async def send_single_message(user):
            async with semaphore:
                if cancel_event.is_set():
                    return False

                telegram_id = getattr(user, 'telegram_id', None)
                if telegram_id is None:
                    # Email-пользователи без telegram_id - пропускаем (не считаем ошибкой)
                    return None

                try:
                    await self._deliver_message(telegram_id, config, keyboard)
                    return True
                except Exception as exc:
                    logger.error(
                        'Ошибка отправки рассылки %s пользователю %s: %s',
                        broadcast_id,
                        telegram_id,
                        exc,
                    )
                    return False

        batch_size = 100
        for i in range(0, len(recipients), batch_size):
            if cancel_event.is_set():
                await self._mark_cancelled(broadcast_id, sent_count, failed_count)
                return sent_count, failed_count, True

            batch = recipients[i : i + batch_size]
            tasks = [send_single_message(user) for user in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if result is True:
                    sent_count += 1
                elif result is None:
                    # Email-пользователи - пропускаем без ошибки
                    pass
                else:
                    failed_count += 1

            processed = sent_count + failed_count
            if processed % PROGRESS_UPDATE_STEP == 0:
                await self._update_progress(broadcast_id, sent_count, failed_count)

            await asyncio.sleep(0.1)

        return sent_count, failed_count, False

    def _build_keyboard(self, selected_buttons: list[str] | None) -> InlineKeyboardMarkup | None:
        if selected_buttons is None:
            selected_buttons = []
        return create_broadcast_keyboard(selected_buttons)

    async def _deliver_message(
        self,
        telegram_id: int,
        config: BroadcastConfig,
        keyboard: InlineKeyboardMarkup | None,
    ) -> None:
        if not self._bot:
            raise RuntimeError('Телеграм-бот не инициализирован')

        if config.media and config.media.type in VALID_MEDIA_TYPES:
            caption = config.media.caption or config.message_text
            if config.media.type == 'photo':
                await self._bot.send_photo(
                    chat_id=telegram_id,
                    photo=config.media.file_id,
                    caption=caption,
                    reply_markup=keyboard,
                )
            elif config.media.type == 'video':
                await self._bot.send_video(
                    chat_id=telegram_id,
                    video=config.media.file_id,
                    caption=caption,
                    reply_markup=keyboard,
                )
            elif config.media.type == 'document':
                await self._bot.send_document(
                    chat_id=telegram_id,
                    document=config.media.file_id,
                    caption=caption,
                    reply_markup=keyboard,
                )
            return

        await self._bot.send_message(
            chat_id=telegram_id,
            text=config.message_text,
            reply_markup=keyboard,
        )

    async def _mark_finished(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
        *,
        cancelled: bool,
    ) -> None:
        await self._safe_status_update(
            broadcast_id,
            sent_count,
            failed_count,
            status='cancelled' if cancelled else ('completed' if failed_count == 0 else 'partial'),
        )

    async def _mark_cancelled(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
    ) -> None:
        await self._mark_finished(
            broadcast_id,
            sent_count,
            failed_count,
            cancelled=True,
        )

    async def _mark_failed(
        self,
        broadcast_id: int,
        sent_count: int = 0,
        failed_count: int = 0,
    ) -> None:
        await self._safe_status_update(
            broadcast_id,
            sent_count,
            failed_count,
            status='failed',
        )

    async def _update_progress(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
    ) -> None:
        """Периодически обновляет прогресс рассылки, чтобы держать соединение активным."""

        await self._safe_status_update(
            broadcast_id,
            sent_count,
            failed_count,
            status='in_progress',
            update_completed_at=False,
        )

    async def _safe_status_update(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
        *,
        status: str,
        update_completed_at: bool = True,
    ) -> None:
        attempts = 0

        while attempts < 2:
            try:
                async with AsyncSessionLocal() as session:
                    broadcast = await session.get(BroadcastHistory, broadcast_id)
                    if not broadcast:
                        return

                    broadcast.sent_count = sent_count
                    broadcast.failed_count = failed_count
                    broadcast.status = status

                    if update_completed_at:
                        broadcast.completed_at = datetime.utcnow()

                    await session.commit()
                    return
            except InterfaceError as exc:
                attempts += 1
                logger.warning(
                    'Проблемы с соединением при обновлении статуса рассылки %s: %s. Повтор %s/2',
                    broadcast_id,
                    exc,
                    attempts,
                )
                await asyncio.sleep(0.2)
            except SQLAlchemyError:
                logger.exception('Не удалось обновить статус рассылки %s', broadcast_id)
                return


broadcast_service = BroadcastService()


class EmailBroadcastService:
    """Handles email broadcast execution triggered from the admin web API."""

    def __init__(self) -> None:
        self._email_service: EmailService | None = None
        self._tasks: dict[int, _BroadcastTask] = {}
        self._lock = asyncio.Lock()

    def set_email_service(self, email_service: EmailService) -> None:
        """Set email service instance."""
        self._email_service = email_service

    def is_running(self, broadcast_id: int) -> bool:
        """Check if broadcast is currently running."""
        task_entry = self._tasks.get(broadcast_id)
        return bool(task_entry and not task_entry.task.done())

    async def start_broadcast(self, broadcast_id: int, config: EmailBroadcastConfig) -> None:
        """Start email broadcast in background."""
        if self._email_service is None:
            logger.error('Cannot start email broadcast %s: email service not initialized', broadcast_id)
            await self._mark_failed(broadcast_id)
            return

        if not self._email_service.is_configured():
            logger.error('Cannot start email broadcast %s: SMTP not configured', broadcast_id)
            await self._mark_failed(broadcast_id)
            return

        cancel_event = asyncio.Event()

        async with self._lock:
            if broadcast_id in self._tasks and not self._tasks[broadcast_id].task.done():
                logger.warning('Email broadcast %s is already running', broadcast_id)
                return

            task = asyncio.create_task(
                self._run_broadcast(broadcast_id, config, cancel_event),
                name=f'email-broadcast-{broadcast_id}',
            )
            self._tasks[broadcast_id] = _BroadcastTask(task=task, cancel_event=cancel_event)
            task.add_done_callback(lambda _: self._tasks.pop(broadcast_id, None))

    async def request_stop(self, broadcast_id: int) -> bool:
        """Request to stop a running broadcast."""
        async with self._lock:
            task_entry = self._tasks.get(broadcast_id)
            if not task_entry:
                return False

            task_entry.cancel_event.set()
            return True

    async def _run_broadcast(
        self,
        broadcast_id: int,
        config: EmailBroadcastConfig,
        cancel_event: asyncio.Event,
    ) -> None:
        """Execute email broadcast."""
        sent_count = 0
        failed_count = 0

        try:
            if cancel_event.is_set():
                await self._mark_cancelled(broadcast_id, sent_count, failed_count)
                return

            # Update status to in_progress
            async with AsyncSessionLocal() as session:
                broadcast = await session.get(BroadcastHistory, broadcast_id)
                if not broadcast:
                    logger.error('Broadcast record %s not found', broadcast_id)
                    return

                broadcast.status = 'in_progress'
                broadcast.sent_count = 0
                broadcast.failed_count = 0
                await session.commit()

            # Fetch email recipients
            recipients = await self._fetch_email_recipients(config.target)

            # Update total count
            async with AsyncSessionLocal() as session:
                broadcast = await session.get(BroadcastHistory, broadcast_id)
                if not broadcast:
                    logger.error('Broadcast record %s deleted before start', broadcast_id)
                    return

                broadcast.total_count = len(recipients)
                await session.commit()

            if cancel_event.is_set():
                await self._mark_cancelled(broadcast_id, sent_count, failed_count)
                return

            if not recipients:
                logger.info('Email broadcast %s: no recipients found', broadcast_id)
                await self._mark_finished(broadcast_id, sent_count, failed_count, cancelled=False)
                return

            # Send emails with rate limiting
            sent_count, failed_count, was_cancelled = await self._send_emails(
                broadcast_id,
                recipients,
                config,
                cancel_event,
            )

            if was_cancelled:
                logger.info('Email broadcast %s was cancelled during execution', broadcast_id)
                return

            await self._mark_finished(broadcast_id, sent_count, failed_count, cancelled=False)

        except asyncio.CancelledError:
            await self._mark_cancelled(broadcast_id, sent_count, failed_count)
            raise
        except Exception as exc:
            logger.exception('Critical error in email broadcast %s: %s', broadcast_id, exc)
            await self._mark_failed(broadcast_id, sent_count, failed_count)

    async def _fetch_email_recipients(self, target: str) -> list:
        """Fetch email recipients based on target filter."""
        from sqlalchemy import select

        from app.database.models import Subscription, SubscriptionStatus, User

        async with AsyncSessionLocal() as session:
            # Base query: verified email users with active status
            base_conditions = [
                User.email.isnot(None),
                User.email_verified == True,
                User.status == 'active',
            ]

            if target == 'all_email':
                # All users with verified email
                query = select(User).where(*base_conditions)

            elif target == 'email_only':
                # Only email-registered users (no telegram)
                query = select(User).where(
                    *base_conditions,
                    User.auth_type == 'email',
                )

            elif target == 'telegram_with_email':
                # Telegram users who also have email
                query = select(User).where(
                    *base_conditions,
                    User.auth_type == 'telegram',
                    User.telegram_id.isnot(None),
                )

            elif target == 'active_email':
                # Email users with active subscription
                query = (
                    select(User)
                    .join(Subscription, User.id == Subscription.user_id)
                    .where(
                        *base_conditions,
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                    )
                )

            elif target == 'expired_email':
                # Email users with expired subscription
                query = (
                    select(User)
                    .join(Subscription, User.id == Subscription.user_id)
                    .where(
                        *base_conditions,
                        Subscription.status.in_(
                            [
                                SubscriptionStatus.EXPIRED.value,
                                SubscriptionStatus.DISABLED.value,
                            ]
                        ),
                    )
                )

            else:
                logger.warning('Unknown email target filter: %s', target)
                return []

            # Load users in batches
            users: list = []
            offset = 0
            batch_size = 1000

            while True:
                result = await session.execute(query.offset(offset).limit(batch_size))
                batch = result.scalars().all()

                if not batch:
                    break

                users.extend(batch)
                offset += batch_size

            return users

    async def _send_emails(
        self,
        broadcast_id: int,
        recipients: list,
        config: EmailBroadcastConfig,
        cancel_event: asyncio.Event,
    ) -> tuple[int, int, bool]:
        """Send emails with rate limiting."""
        sent_count = 0
        failed_count = 0

        # Semaphore for rate limiting (max EMAIL_RATE_LIMIT concurrent sends)
        semaphore = asyncio.Semaphore(EMAIL_RATE_LIMIT)

        async def send_single_email(user) -> bool | None:
            """Send single email with rate limiting."""
            async with semaphore:
                if cancel_event.is_set():
                    return None

                email = getattr(user, 'email', None)
                if not email:
                    return None

                # Render template with variables
                html_content = self._render_template(config.email_html_content, user)
                subject = self._render_template(config.email_subject, user)

                try:
                    # Run sync email send in executor to not block event loop
                    loop = asyncio.get_event_loop()
                    success = await loop.run_in_executor(
                        None,
                        self._email_service.send_email,
                        email,
                        subject,
                        html_content,
                    )
                    return success
                except Exception as exc:
                    logger.error(
                        'Error sending email broadcast %s to %s: %s',
                        broadcast_id,
                        email,
                        exc,
                    )
                    return False

        # Process in batches
        for i in range(0, len(recipients), EMAIL_BATCH_SIZE):
            if cancel_event.is_set():
                await self._mark_cancelled(broadcast_id, sent_count, failed_count)
                return sent_count, failed_count, True

            batch = recipients[i : i + EMAIL_BATCH_SIZE]
            tasks = [send_single_email(user) for user in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if result is True:
                    sent_count += 1
                elif result is None:
                    # Skipped (cancelled or no email)
                    pass
                else:
                    failed_count += 1

            # Update progress periodically
            processed = sent_count + failed_count
            if processed % PROGRESS_UPDATE_STEP == 0 or i + EMAIL_BATCH_SIZE >= len(recipients):
                await self._update_progress(broadcast_id, sent_count, failed_count)

            # Rate limiting delay between batches (ensure ~8 emails/sec)
            await asyncio.sleep(EMAIL_BATCH_SIZE / EMAIL_RATE_LIMIT)

        return sent_count, failed_count, False

    def _render_template(self, template: str, user) -> str:
        """Render template with user variables."""
        if not template:
            return template

        # Get user name
        user_name = getattr(user, 'username', None)
        if not user_name:
            user_name = getattr(user, 'first_name', None) or ''
            if last_name := getattr(user, 'last_name', None):
                user_name = f'{user_name} {last_name}'.strip()
        if not user_name:
            user_name = getattr(user, 'email', '').split('@')[0] if getattr(user, 'email', None) else 'User'

        email = getattr(user, 'email', '') or ''

        # Replace template variables
        result = template.replace('{{user_name}}', user_name)
        result = result.replace('{{email}}', email)

        return result

    async def _mark_finished(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
        *,
        cancelled: bool,
    ) -> None:
        """Mark broadcast as finished."""
        status = 'cancelled' if cancelled else ('completed' if failed_count == 0 else 'partial')
        await self._safe_status_update(broadcast_id, sent_count, failed_count, status=status)

    async def _mark_cancelled(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
    ) -> None:
        """Mark broadcast as cancelled."""
        await self._mark_finished(broadcast_id, sent_count, failed_count, cancelled=True)

    async def _mark_failed(
        self,
        broadcast_id: int,
        sent_count: int = 0,
        failed_count: int = 0,
    ) -> None:
        """Mark broadcast as failed."""
        await self._safe_status_update(broadcast_id, sent_count, failed_count, status='failed')

    async def _update_progress(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
    ) -> None:
        """Update broadcast progress."""
        await self._safe_status_update(
            broadcast_id,
            sent_count,
            failed_count,
            status='in_progress',
            update_completed_at=False,
        )

    async def _safe_status_update(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
        *,
        status: str,
        update_completed_at: bool = True,
    ) -> None:
        """Safely update broadcast status with retry."""
        attempts = 0

        while attempts < 2:
            try:
                async with AsyncSessionLocal() as session:
                    broadcast = await session.get(BroadcastHistory, broadcast_id)
                    if not broadcast:
                        return

                    broadcast.sent_count = sent_count
                    broadcast.failed_count = failed_count
                    broadcast.status = status

                    if update_completed_at:
                        broadcast.completed_at = datetime.utcnow()

                    await session.commit()
                    return
            except InterfaceError as exc:
                attempts += 1
                logger.warning(
                    'Connection issue updating email broadcast %s: %s. Retry %s/2',
                    broadcast_id,
                    exc,
                    attempts,
                )
                await asyncio.sleep(0.2)
            except SQLAlchemyError:
                logger.exception('Failed to update email broadcast status %s', broadcast_id)
                return


email_broadcast_service = EmailBroadcastService()
