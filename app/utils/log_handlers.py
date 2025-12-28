"""Кастомные хэндлеры для системы логирования с ротацией.

Модуль предоставляет:
- LevelFilterHandler: фильтрация логов по диапазону уровней
- PaymentLogFilter: перехват логов из платежных модулей
- ExcludePaymentFilter: исключение платежей из основных логов
"""

from __future__ import annotations

import logging
from typing import Optional


class LevelFilterHandler(logging.Handler):
    """Хэндлер, фильтрующий логи по диапазону уровней.

    Используется для разделения логов:
    - info.log: только INFO (min_level=INFO, max_level=INFO)
    - warning.log: WARNING и выше (min_level=WARNING)
    - error.log: ERROR и CRITICAL (min_level=ERROR)

    Args:
        filename: Путь к файлу лога
        min_level: Минимальный уровень логирования
        max_level: Максимальный уровень (по умолчанию CRITICAL)
        encoding: Кодировка файла
    """

    def __init__(
        self,
        filename: str,
        min_level: int,
        max_level: Optional[int] = None,
        encoding: str = "utf-8",
    ):
        super().__init__(level=min_level)
        self.min_level = min_level
        self.max_level = max_level if max_level is not None else logging.CRITICAL
        self._file_handler = logging.FileHandler(filename, encoding=encoding)

    def emit(self, record: logging.LogRecord) -> None:
        """Записать лог только если уровень в заданном диапазоне."""
        if self.min_level <= record.levelno <= self.max_level:
            self._file_handler.emit(record)

    def setFormatter(self, fmt: logging.Formatter) -> None:
        """Установить форматтер для внутреннего хэндлера."""
        super().setFormatter(fmt)
        self._file_handler.setFormatter(fmt)

    def close(self) -> None:
        """Закрыть файловый хэндлер."""
        self._file_handler.close()
        super().close()

    def flush(self) -> None:
        """Сбросить буфер файлового хэндлера."""
        self._file_handler.flush()


class PaymentLogFilter(logging.Filter):
    """Фильтр для логов платежей.

    Пропускает записи из модулей:
    - app.services.payment.*
    - app.payments (выделенный логгер)
    - Связанные платежные сервисы
    """

    PAYMENT_MODULES = (
        "app.payments",
        "app.services.payment",
        "app.services.yookassa_service",
        "app.services.tribute_service",
        "app.services.mulenpay_service",
        "app.services.cloudpayments_service",
        "app.services.platega_service",
        "app.services.pal24_service",
        "app.services.wata_service",
        "app.external.cryptobot",
        "app.external.heleket",
        "app.external.tribute",
        "app.external.yookassa_webhook",
        "app.external.pal24_webhook",
        "app.external.wata_webhook",
        "app.external.heleket_webhook",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        """Пропустить только записи из платежных модулей."""
        return any(record.name.startswith(module) for module in self.PAYMENT_MODULES)


class ExcludePaymentFilter(logging.Filter):
    """Исключает платежные логи из основных файлов.

    Используется для bot.log, info.log, warning.log, error.log
    чтобы платежные записи шли только в payments.log.
    """

    PAYMENT_MODULES = PaymentLogFilter.PAYMENT_MODULES

    def filter(self, record: logging.LogRecord) -> bool:
        """Пропустить записи НЕ из платежных модулей."""
        return not any(
            record.name.startswith(module) for module in self.PAYMENT_MODULES
        )
