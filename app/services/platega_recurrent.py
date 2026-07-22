"""Чистая логика рекуррентных СБП-подписок Platega (без сети и БД)."""

from __future__ import annotations


# Platega paymentMethod для подписки
PLATEGA_SUBSCRIPTION_METHOD = 6

# interval: 1=day, 2=week, 3=month, 4=year
INTERVAL_DAY = 1
INTERVAL_WEEK = 2
INTERVAL_MONTH = 3
INTERVAL_YEAR = 4

# Статусы коллбеков
CHARGE_SUCCESS = {'CONFIRMED'}
CHARGE_FAILED = {'CANCELED'}
SUB_ACTIVATED = 'SUBSCRIPTION_ACTIVATED'
SUB_PAST_DUE = 'SUBSCRIPTION_PAST_DUE'
SUB_CANCELLED = 'SUBSCRIPTION_CANCELLED'
SUB_FAILED = 'SUBSCRIPTION_FAILED'


def resolve_platega_interval(period_days: int, is_daily: bool) -> tuple[int, int]:
    """Возвращает (interval, charge_days) для подписки Platega.

    Platega умеет только day/week/month/year (count=1). Каденс выводится из
    числа дней тарифа; неровные периоды приклеиваются к месяцу по 30-дневной
    цене (см. спеку §3). charge_days задаёт и сумму, и шаг продления.
    """
    if is_daily:
        return INTERVAL_DAY, 1
    if period_days == 7:
        return INTERVAL_WEEK, 7
    if 28 <= period_days <= 31:
        return INTERVAL_MONTH, period_days
    if 350 <= period_days <= 380:
        return INTERVAL_YEAR, period_days
    return INTERVAL_MONTH, 30
