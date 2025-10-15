from datetime import datetime, time as time_cls
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.remnawave_sync_service import RemnaWaveAutoSyncService


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("03:00, 15:30 03:00; 07:05", [time_cls(3, 0), time_cls(7, 5), time_cls(15, 30)]),
        ("", []),
        (None, []),
        ("25:00, 10:70, test, 09:15", [time_cls(9, 15)]),
    ],
)
def test_parse_daily_time_list(raw, expected):
    assert settings.parse_daily_time_list(raw) == expected


def _patch_datetime(monkeypatch, current):
    real_datetime = datetime

    monkeypatch.setattr(
        "app.services.remnawave_sync_service.datetime",
        SimpleNamespace(
            utcnow=lambda: current,
            combine=lambda date_obj, time_obj: real_datetime.combine(date_obj, time_obj),
        ),
    )


def test_calculate_next_run_same_day(monkeypatch):
    service = RemnaWaveAutoSyncService()
    current = datetime(2024, 1, 1, 2, 30)
    _patch_datetime(monkeypatch, current)

    next_run = service._calculate_next_run([time_cls(1, 0), time_cls(3, 0)])

    assert next_run == datetime(2024, 1, 1, 3, 0)


def test_calculate_next_run_rollover(monkeypatch):
    service = RemnaWaveAutoSyncService()
    current = datetime(2024, 1, 1, 23, 45)
    _patch_datetime(monkeypatch, current)

    next_run = service._calculate_next_run([time_cls(1, 0), time_cls(10, 0)])

    assert next_run == datetime(2024, 1, 2, 1, 0)
