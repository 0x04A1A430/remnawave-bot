import pytest

from app.services.platega_recurrent import resolve_platega_interval


@pytest.mark.parametrize(
    ('period_days', 'is_daily', 'expected'),
    [
        (1, True, (1, 1)),  # daily tariff -> day
        (30, False, (3, 30)),  # exact month
        (7, False, (2, 7)),  # week
        (360, False, (4, 360)),  # year
        (365, False, (4, 365)),  # yearly range 350-380
        (31, False, (3, 31)),  # monthly range 28-31
        (14, False, (3, 30)),  # non-mapping -> month @ 30
        (60, False, (3, 30)),
        (90, False, (3, 30)),
        (180, False, (3, 30)),
    ],
)
def test_resolve_platega_interval(period_days, is_daily, expected):
    assert resolve_platega_interval(period_days, is_daily) == expected


def test_is_daily_wins_over_period_days():
    # a daily tariff is always daily regardless of a stray period value
    assert resolve_platega_interval(30, True) == (1, 1)
