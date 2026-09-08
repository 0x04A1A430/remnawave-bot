"""Дроссель регистрации по email с одного IP.

Одного минутного окна мало: скрипт ждёт минуту и продолжает — из отчёта про волну
одноразовых почт за триалами. Поверх него часовое и суточное окна; все три
настраиваются (0 выключает окно). Сработавшее окно отдаёт 429 и Retry-After на
свою длину. Redis недоступен — блокируем (fail_closed): регистрация security-critical.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.config import settings
from app.utils.cache import RateLimitCache


MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR


def _windows() -> tuple[tuple[str, int, int], ...]:
    return (
        ('email_register', settings.CABINET_EMAIL_REGISTER_LIMIT_PER_MINUTE, MINUTE),
        ('email_register_hour', settings.CABINET_EMAIL_REGISTER_LIMIT_PER_HOUR, HOUR),
        ('email_register_day', settings.CABINET_EMAIL_REGISTER_LIMIT_PER_DAY, DAY),
    )


async def enforce_email_registration_throttle(client_ip: str) -> None:
    for action, limit, window in _windows():
        if limit <= 0:
            continue
        if await RateLimitCache.is_ip_rate_limited(client_ip, action, limit=limit, window=window, fail_closed=True):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail='Too many requests',
                headers={'Retry-After': str(window)},
            )
