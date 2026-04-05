"""Specialized logger for payment operations.

Provides a dedicated structlog logger for all payment-related events.
The underlying stdlib logger backend writes to a separate ``payments.log``
file with ``propagate=False`` to avoid duplicating payment entries in the
general log.

Usage::

    from app.utils.payment_logger import payment_logger

    payment_logger.info('Created YooKassa payment', payment_id=payment_id, amount=amount)
    payment_logger.error('Webhook processing error', error=error)
"""

from __future__ import annotations

import logging

import structlog


# The stdlib backend logger — keeps propagate=False so payment events
# only go to the dedicated file handler, not the root logger.
_stdlib_payment_logger = logging.getLogger('app.payments')

# Structlog frontend wrapping the stdlib backend.
# All structlog processors (timestamper, notifier, etc.) apply normally.
payment_logger: structlog.stdlib.BoundLogger = structlog.wrap_logger(
    _stdlib_payment_logger,
    wrapper_class=structlog.stdlib.BoundLogger,
)


def configure_payment_logger(
    handler: logging.Handler,
    formatter: logging.Formatter | None = None,
    level: int = logging.INFO,
) -> None:
    """Configure the payment logger with the given handler.

    Args:
        handler: Handler for writing logs (FileHandler, StreamHandler, etc.)
        formatter: Formatter for log output (optional)
        level: Logging level (default INFO)
    """
    _stdlib_payment_logger.setLevel(level)

    if formatter:
        handler.setFormatter(formatter)

    _stdlib_payment_logger.addHandler(handler)

    # Prevent duplication in parent loggers
    _stdlib_payment_logger.propagate = False


def get_payment_logger() -> structlog.stdlib.BoundLogger:
    """Return the payment logger instance.

    Alternative way to obtain the logger for modules that prefer
    an explicit function call.
    """
    return payment_logger
