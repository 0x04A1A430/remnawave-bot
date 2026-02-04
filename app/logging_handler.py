"""Кастомный logging handler для отправки ERROR/CRITICAL в админский чат Telegram.

Перехватывает все log records уровня ERROR и CRITICAL и отправляет их
в админский чат через существующий механизм send_error_to_admin_chat()
из app.middlewares.global_error.

Дедупликация:
- Записи, уже обработанные GlobalErrorMiddleware или @error_handler,
  помечаются атрибутом _admin_notified = True и пропускаются.
- Хеши недавних сообщений хранятся в LRU-кеше для предотвращения
  дублирования одинаковых ошибок за короткий период.

Async bridge:
- logging.Handler.emit() -- синхронный. Мы используем
  asyncio.get_running_loop().call_soon_threadsafe() для планирования
  asyncio.Task из любого потока (sync или async).

Deferred init:
- Bot instance создаётся позже в main.py. Метод set_bot() позволяет
  передать его после создания. До этого записи молча пропускаются.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Final

from aiogram import Bot


# Константы
RECENT_HASHES_MAX_SIZE: Final[int] = 256
RECENT_HASH_TTL_SECONDS: Final[float] = 300.0  # 5 минут -- совпадает с cooldown в global_error

# Логгеры, от которых мы гарантированно не хотим получать уведомления,
# даже если они вдруг выдадут ERROR (шум от транспортного уровня).
IGNORED_LOGGER_PREFIXES: Final[tuple[str, ...]] = (
    'aiohttp.access',
    'aiohttp.client',
    'aiohttp.internal',
    'uvicorn.access',
    'uvicorn.error',
    'uvicorn.protocols',
    'websockets',
    'asyncio',
)


class TelegramErrorHandler(logging.Handler):
    """Logging handler, отправляющий ERROR/CRITICAL записи в админский Telegram-чат.

    Использует существующий механизм троттлинга и буферизации из
    ``app.middlewares.global_error.send_error_to_admin_chat``.

    Usage::

        handler = TelegramErrorHandler()
        handler.setLevel(logging.ERROR)
        logging.getLogger().addHandler(handler)

        # Позже, когда Bot создан:
        handler.set_bot(bot)
    """

    def __init__(self, level: int = logging.ERROR) -> None:
        super().__init__(level=level)
        self._bot: Bot | None = None
        # LRU-подобный кеш хешей недавних сообщений: hash -> timestamp
        self._recent_hashes: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_bot(self, bot: Bot) -> None:
        """Устанавливает Bot instance для отправки сообщений.

        Вызывается из main.py после создания бота.
        """
        self._bot = bot

    # ------------------------------------------------------------------
    # logging.Handler interface
    # ------------------------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        """Обрабатывает log record.

        Синхронный метод (требование logging). Планирует async-отправку
        через event loop.
        """
        # 1. Фильтр по уровню (на случай если кто-то обойдёт setLevel)
        if record.levelno < logging.ERROR:
            return

        # 2. Уже отправлено через GlobalErrorMiddleware / @error_handler
        if getattr(record, '_admin_notified', False):
            return

        # 3. Фильтруем шумные логгеры
        if any(record.name.startswith(prefix) for prefix in IGNORED_LOGGER_PREFIXES):
            return

        # 4. Бот ещё не инициализирован -- пропускаем
        bot = self._bot
        if bot is None:
            return

        # 5. Дедупликация по хешу (logger_name + message)
        msg_hash = self._compute_hash(record)
        now = time.monotonic()

        # Чистим просроченные записи (ленивая очистка)
        self._evict_stale(now)

        if msg_hash in self._recent_hashes:
            return
        self._recent_hashes[msg_hash] = now

        # 6. Планируем отправку через event loop
        self._schedule_send(bot, record)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_hash(record: logging.LogRecord) -> str:
        """Вычисляет короткий хеш для дедупликации.

        Хешируем имя логгера + сообщение (без timestamp).
        """
        raw = f'{record.name}:{record.getMessage()}'
        return hashlib.md5(raw.encode('utf-8', errors='replace')).hexdigest()

    def _evict_stale(self, now: float) -> None:
        """Удаляет устаревшие записи из кеша хешей."""
        if not self._recent_hashes:
            return
        stale_keys = [k for k, ts in self._recent_hashes.items() if (now - ts) > RECENT_HASH_TTL_SECONDS]
        for k in stale_keys:
            self._recent_hashes.pop(k, None)
        # Принудительная очистка при переполнении — удаляем самые старые
        if len(self._recent_hashes) > RECENT_HASHES_MAX_SIZE:
            sorted_keys = sorted(self._recent_hashes, key=self._recent_hashes.get)
            for k in sorted_keys[: len(self._recent_hashes) - RECENT_HASHES_MAX_SIZE]:
                self._recent_hashes.pop(k, None)

    def _schedule_send(self, bot: Bot, record: logging.LogRecord) -> None:
        """Планирует асинхронную отправку в event loop.

        Работает из любого потока:
        - Если вызов из async-контекста -- создаём Task напрямую.
        - Если из другого потока -- используем call_soon_threadsafe.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Нет running loop -- мы в стороннем потоке без loop.
            # Пытаемся получить loop, привязанный к основному потоку.
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    return
                loop.call_soon_threadsafe(self._create_send_task, bot, record, loop)
            except RuntimeError:
                return
        else:
            # Мы в async-контексте -- создаём task напрямую
            self._create_send_task(bot, record, loop)

    def _create_send_task(self, bot: Bot, record: logging.LogRecord, loop: asyncio.AbstractEventLoop) -> None:
        """Создаёт asyncio.Task для отправки уведомления."""
        loop.create_task(self._send(bot, record))

    @staticmethod
    async def _send(bot: Bot, record: logging.LogRecord) -> None:
        """Отправляет log record в админский чат через существующую инфраструктуру."""
        try:
            # Ленивый импорт -- избегаем циклических зависимостей при старте
            from app.middlewares.global_error import send_error_to_admin_chat

            # Формируем pseudo-Exception из log record
            error = _make_log_record_error(record)

            context_parts: list[str] = [f'Logger: {record.name}']
            if record.funcName:
                context_parts.append(f'Function: {record.funcName}')
            if record.pathname and record.lineno:
                context_parts.append(f'Location: {record.pathname}:{record.lineno}')

            context = '\n'.join(context_parts)

            # Извлекаем traceback из log record (если есть exc_info)
            tb_override: str | None = None
            if record.exc_info and record.exc_info[2] is not None:
                import traceback

                tb_override = ''.join(traceback.format_exception(*record.exc_info))
            elif record.exc_text:
                tb_override = record.exc_text

            await send_error_to_admin_chat(bot, error, context, tb_override=tb_override)

        except Exception:
            # Ни в коем случае не даём исключению утечь -- это logging handler,
            # рекурсия убьёт приложение.
            pass


def _make_log_record_error(record: logging.LogRecord) -> Exception:
    """Создаёт Exception-обёртку для LogRecord.

    send_error_to_admin_chat использует type(error).__name__ как error_type.
    Мы динамически создаём класс с правильным именем, чтобы не мутировать
    общий класс между вызовами.
    """
    class_name = f'Log{record.levelname.capitalize()}'
    error_cls = type(
        class_name,
        (Exception,),
        {
            '__str__': lambda self: self.args[0] if self.args else '',
        },
    )
    error = error_cls(record.getMessage())
    error.record = record  # type: ignore[attr-defined]
    return error
