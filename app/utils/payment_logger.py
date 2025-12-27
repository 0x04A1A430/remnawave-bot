"""Специальный логгер для платежей.

Выделенный логгер для всех платежных операций.
Записи идут в отдельный файл payments.log.

Использование:
    from app.utils.payment_logger import payment_logger

    payment_logger.info("Создан YooKassa платеж %s на %s", payment_id, amount)
    payment_logger.error("Ошибка обработки webhook: %s", error)
"""

from __future__ import annotations

import logging
from typing import Optional

# Выделенный логгер для всех платежных операций
payment_logger = logging.getLogger("app.payments")


def configure_payment_logger(
    handler: logging.Handler,
    formatter: Optional[logging.Formatter] = None,
    level: int = logging.INFO,
) -> None:
    """Настроить payment_logger с указанным хэндлером.

    Args:
        handler: Хэндлер для записи логов (FileHandler, StreamHandler и т.д.)
        formatter: Форматтер для логов (опционально)
        level: Уровень логирования (по умолчанию INFO)
    """
    payment_logger.setLevel(level)

    if formatter:
        handler.setFormatter(formatter)

    payment_logger.addHandler(handler)

    # Предотвращаем дублирование в родительских логгерах
    payment_logger.propagate = False


def get_payment_logger() -> logging.Logger:
    """Получить экземпляр payment_logger.

    Альтернативный способ получения логгера для модулей,
    которые предпочитают явный вызов функции.

    Returns:
        Настроенный логгер платежей
    """
    return payment_logger
