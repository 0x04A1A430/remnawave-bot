"""Очередь повторной отправки писем.

``send_email`` синхронный и выполняется в рабочем потоке (``asyncio.to_thread``),
поэтому положить задачу напрямую в ``asyncio.Queue`` нельзя — она не
потокобезопасна. Кладём через ``call_soon_threadsafe``, как в
``system_error_log_service``.

Массовые рассылки сюда не попадают: на их стороне выставлен
``queue_on_failure=False``. Иначе один обрыв SMTP забил бы очередь тысячами
писем и повторял бы их сутки.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import insert, select, update

from app.database.database import AsyncSessionLocal
from app.database.models import EmailQueueItem


logger = structlog.get_logger(__name__)

# Пауза перед попыткой N (минуты). После исчерпания письмо признаётся мёртвым.
# Суммарно ~24 часа — этого хватило бы с запасом на шестичасовой обрыв 24.08.
BACKOFF_MINUTES = [1, 5, 15, 30, 60, 120, 240, 360, 360, 360]
MAX_ATTEMPTS = len(BACKOFF_MINUTES)

POLL_INTERVAL_SECONDS = 30
BATCH_SIZE = 20

# Вложения складываем в БД base64-строкой, поэтому ограничиваем суммарный
# размер: письмо с большим файлом лучше потерять, чем раздуть таблицу.
MAX_ATTACHMENTS_BYTES = 2 * 1024 * 1024

ENQUEUE_QUEUE_MAX = 500

STATUS_PENDING = 'pending'
STATUS_SENT = 'sent'
STATUS_DEAD = 'dead'


class EmailRetryService:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._writer: asyncio.Task | None = None
        self._worker: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dropped = 0

    # ------------------------------------------------------------------
    # Жизненный цикл
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=ENQUEUE_QUEUE_MAX)
        self._writer = asyncio.create_task(self._run_writer())
        self._worker = asyncio.create_task(self._run_worker())
        logger.info('EmailRetryService запущен', max_attempts=MAX_ATTEMPTS)

    async def stop(self) -> None:
        for task in (self._writer, self._worker):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._writer = None
        self._worker = None

    # ------------------------------------------------------------------
    # Публичный API — вызывается из синхронного send_email в чужом потоке
    # ------------------------------------------------------------------

    def enqueue(
        self,
        *,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str | None = None,
        attachments: list[tuple[str, bytes, str]] | None = None,
        unsubscribe_url: str | None = None,
    ) -> bool:
        """Поставить письмо в очередь повторной отправки.

        Возвращает True, если письмо принято. False означает, что повторов не
        будет — вызывающий код должен сообщить об ошибке как раньше.
        """
        loop = self._loop
        if self._queue is None or loop is None:
            return False

        attachments_json = self._encode_attachments(attachments)
        if attachments_json is False:
            logger.warning(
                'Письмо не поставлено в очередь: вложения превышают лимит',
                to_email=to_email,
                limit_bytes=MAX_ATTACHMENTS_BYTES,
            )
            return False

        payload = {
            'to_email': to_email[:320],
            'subject': subject,
            'body_html': body_html,
            'body_text': body_text,
            'unsubscribe_url': unsubscribe_url,
            'attachments_json': attachments_json,
            'status': STATUS_PENDING,
            'attempts': 0,
            'next_attempt_at': datetime.now(tz=UTC) + timedelta(minutes=BACKOFF_MINUTES[0]),
        }

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is loop:
            return self._put_nowait(payload)
        try:
            loop.call_soon_threadsafe(self._put_nowait, payload)
            return True
        except RuntimeError:
            return False

    # ------------------------------------------------------------------
    # Внутреннее
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_attachments(attachments: list[tuple[str, bytes, str]] | None) -> list[dict[str, str]] | None | bool:
        """Возвращает список для JSON, None если вложений нет, False если великоваты."""
        if not attachments:
            return None
        total = sum(len(content) for _, content, _ in attachments)
        if total > MAX_ATTACHMENTS_BYTES:
            return False
        return [
            {
                'filename': filename,
                'mimetype': mimetype,
                'content_b64': base64.b64encode(content).decode('ascii'),
            }
            for filename, content, mimetype in attachments
        ]

    @staticmethod
    def _decode_attachments(raw: list[dict[str, str]] | None) -> list[tuple[str, bytes, str]] | None:
        if not raw:
            return None
        decoded: list[tuple[str, bytes, str]] = []
        for item in raw:
            try:
                decoded.append(
                    (
                        item['filename'],
                        base64.b64decode(item['content_b64']),
                        item.get('mimetype') or 'application/octet-stream',
                    )
                )
            except Exception:
                continue
        return decoded or None

    def _put_nowait(self, payload: dict[str, Any]) -> bool:
        queue = self._queue
        if queue is None:
            return False
        try:
            queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 50 == 1:
                logger.warning('Очередь писем переполнена, потеряно', dropped=self._dropped)
            return False

    async def _run_writer(self) -> None:
        queue = self._queue
        if queue is None:
            return
        while True:
            payload = await queue.get()
            try:
                async with AsyncSessionLocal() as session:
                    await session.execute(insert(EmailQueueItem).values(**payload))
                    await session.commit()
                logger.info('Письмо отложено в очередь повторной отправки', to_email=payload['to_email'])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning('Не удалось записать письмо в очередь', error=str(e)[:200])
            finally:
                queue.task_done()

    async def _run_worker(self) -> None:
        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                await self._process_due()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning('Сбой цикла повторной отправки писем', error=str(e)[:200])

    async def _process_due(self) -> None:
        now = datetime.now(tz=UTC)
        async with AsyncSessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(EmailQueueItem)
                        .where(
                            EmailQueueItem.status == STATUS_PENDING,
                            EmailQueueItem.next_attempt_at <= now,
                        )
                        .order_by(EmailQueueItem.next_attempt_at)
                        .limit(BATCH_SIZE)
                    )
                )
                .scalars()
                .all()
            )
            items = [
                {
                    'id': row.id,
                    'to_email': row.to_email,
                    'subject': row.subject,
                    'body_html': row.body_html,
                    'body_text': row.body_text,
                    'unsubscribe_url': row.unsubscribe_url,
                    'attachments': self._decode_attachments(row.attachments_json),
                    'attempts': row.attempts,
                }
                for row in rows
            ]

        for item in items:
            await self._attempt(item)

    async def _attempt(self, item: dict[str, Any]) -> None:
        from app.cabinet.services.email_service import email_service

        attempts = item['attempts'] + 1
        try:
            sent = await asyncio.to_thread(
                email_service.send_email,
                to_email=item['to_email'],
                subject=item['subject'],
                body_html=item['body_html'],
                body_text=item['body_text'],
                attachments=item['attachments'],
                unsubscribe_url=item['unsubscribe_url'],
                queue_on_failure=False,  # повтором управляет эта очередь, не send_email
            )
            error_text = None if sent else 'send_email вернул False'
        except Exception as e:
            sent = False
            error_text = str(e)[:1000]

        async with AsyncSessionLocal() as session:
            if sent:
                await session.execute(
                    update(EmailQueueItem)
                    .where(EmailQueueItem.id == item['id'])
                    .values(
                        status=STATUS_SENT,
                        attempts=attempts,
                        sent_at=datetime.now(tz=UTC),
                        last_error=None,
                    )
                )
                await session.commit()
                logger.info(
                    'Письмо доставлено повторной попыткой',
                    to_email=item['to_email'],
                    attempts=attempts,
                )
                return

            if attempts >= MAX_ATTEMPTS:
                await session.execute(
                    update(EmailQueueItem)
                    .where(EmailQueueItem.id == item['id'])
                    .values(status=STATUS_DEAD, attempts=attempts, last_error=error_text)
                )
                await session.commit()
                # Единственное место, где письмо признаётся потерянным —
                # здесь уместен error: он попадёт в админ-чат и в журнал ошибок.
                logger.error(
                    'Письмо потеряно: исчерпаны все попытки отправки',
                    to_email=item['to_email'],
                    attempts=attempts,
                    last_error=error_text,
                )
                return

            delay = BACKOFF_MINUTES[min(attempts, MAX_ATTEMPTS - 1)]
            await session.execute(
                update(EmailQueueItem)
                .where(EmailQueueItem.id == item['id'])
                .values(
                    attempts=attempts,
                    next_attempt_at=datetime.now(tz=UTC) + timedelta(minutes=delay),
                    last_error=error_text,
                )
            )
            await session.commit()
            logger.warning(
                'Повторная отправка письма не удалась, отложена',
                to_email=item['to_email'],
                attempts=attempts,
                retry_in_minutes=delay,
            )


email_retry_service = EmailRetryService()
