from datetime import datetime
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.remnawave_service import RemnaWaveService


def _create_service() -> RemnaWaveService:
    service = RemnaWaveService.__new__(RemnaWaveService)
    service._panel_timezone = ZoneInfo("UTC")
    service._utc_timezone = ZoneInfo("UTC")
    return service


def _make_panel_user(telegram_id: int, expire_at: str, status: str = "ACTIVE") -> dict:
    return {
        "telegramId": telegram_id,
        "expireAt": expire_at,
        "status": status,
    }


def test_deduplicate_prefers_latest_expire_date():
    service = _create_service()

    telegram_id = 100
    older = _make_panel_user(telegram_id, datetime(2025, 1, 1, 0, 0, 0).isoformat())
    newer = _make_panel_user(telegram_id, datetime(2025, 2, 1, 0, 0, 0).isoformat())

    deduplicated = service._deduplicate_panel_users_by_telegram_id([older, newer])

    assert deduplicated[telegram_id] is newer


def test_deduplicate_prefers_active_status_on_same_expire():
    service = _create_service()

    telegram_id = 200
    expire = datetime(2025, 1, 1, 0, 0, 0).isoformat()
    disabled = _make_panel_user(telegram_id, expire, status="DISABLED")
    active = _make_panel_user(telegram_id, expire, status="ACTIVE")

    deduplicated = service._deduplicate_panel_users_by_telegram_id([disabled, active])

    assert deduplicated[telegram_id] is active


def test_deduplicate_ignores_records_without_expire_date():
    service = _create_service()

    telegram_id = 300
    missing_expire = _make_panel_user(telegram_id, "")
    valid = _make_panel_user(telegram_id, datetime(2025, 3, 1, 0, 0, 0).isoformat())

    deduplicated = service._deduplicate_panel_users_by_telegram_id([missing_expire, valid])

    assert deduplicated[telegram_id] is valid


async def test_get_or_create_user_handles_unique_violation(monkeypatch):
    service = _create_service()
    db = AsyncMock()

    panel_user = {"telegramId": 555, "username": "existing"}
    existing_user = object()

    create_user_mock = AsyncMock(
        side_effect=IntegrityError("stmt", "params", Exception("unique"))
    )
    get_user_mock = AsyncMock(return_value=existing_user)
    rollback_mock = AsyncMock()

    db.rollback = rollback_mock

    monkeypatch.setattr("app.services.remnawave_service.create_user_no_commit", create_user_mock)
    monkeypatch.setattr(
        "app.services.remnawave_service.get_user_by_telegram_id",
        get_user_mock,
    )

    user, created = await service._get_or_create_bot_user_from_panel(db, panel_user)

    assert user is existing_user
    assert created is False
    create_user_mock.assert_awaited_once()
    get_user_mock.assert_awaited_once_with(db, 555)
    rollback_mock.assert_awaited()


async def test_get_or_create_user_creates_new(monkeypatch):
    service = _create_service()
    db = AsyncMock()

    panel_user = {"telegramId": 777, "username": "new_user"}
    new_user = object()

    create_user_mock = AsyncMock(return_value=new_user)

    monkeypatch.setattr("app.services.remnawave_service.create_user_no_commit", create_user_mock)

    user, created = await service._get_or_create_bot_user_from_panel(db, panel_user)

    assert user is new_user
    assert created is True
    create_user_mock.assert_awaited_once_with(
        db=db,
        telegram_id=777,
        username="new_user",
        first_name="User 777",
        last_name=None,
        language="ru",
    )
