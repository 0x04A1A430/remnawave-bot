"""Transient Telegram API failures (5xx / Bad Gateway / network) must not be
forwarded to the admin chat: aiogram retries polling itself, and the failed
channel is the very one the notification would be sent through.
"""

from __future__ import annotations

from app.logging_handler import _is_transient_telegram_error


def _fake_exc(name: str, message: str) -> Exception:
    """Exception whose class name matches an aiogram Telegram error type."""
    cls = type(name, (Exception,), {})
    return cls(message)


def test_server_error_by_class_name_is_transient():
    error = _fake_exc('TelegramServerError', 'Telegram server says - Bad Gateway')
    assert _is_transient_telegram_error({'exc_info': (type(error), error, None)})


def test_network_error_by_class_name_is_transient():
    error = _fake_exc('TelegramNetworkError', 'Timeout')
    assert _is_transient_telegram_error({'exc_info': (type(error), error, None)})


def test_transient_detected_through_cause_chain():
    root = _fake_exc('TelegramServerError', 'Bad Gateway')
    wrapper = RuntimeError('request failed')
    wrapper.__cause__ = root
    assert _is_transient_telegram_error({'error': wrapper})


def test_polling_fetch_marker_is_transient_without_exception():
    assert _is_transient_telegram_error(
        {'event': 'Failed to fetch updates - TelegramServerError: Telegram server says - Bad Gateway'}
    )


def test_regular_error_is_not_transient():
    assert not _is_transient_telegram_error({'event': 'Обычная ошибка', 'error': ValueError('boom')})
    assert not _is_transient_telegram_error({'event': 'Ошибка отправки уведомления в админ-чат'})
