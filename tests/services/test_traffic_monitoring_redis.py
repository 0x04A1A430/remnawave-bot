"""
Тесты для хранения snapshot трафика в Redis.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.traffic_monitoring_service import (
    TrafficMonitoringServiceV2,
    TRAFFIC_SNAPSHOT_KEY,
    TRAFFIC_SNAPSHOT_TIME_KEY,
    TRAFFIC_NOTIFICATION_CACHE_KEY,
)


@pytest.fixture
def service():
    """Создаёт экземпляр сервиса для тестов."""
    return TrafficMonitoringServiceV2()


@pytest.fixture
def mock_cache():
    """Мок для cache сервиса."""
    with patch('app.services.traffic_monitoring_service.cache') as mock:
        mock.set = AsyncMock(return_value=True)
        mock.get = AsyncMock(return_value=None)
        yield mock


@pytest.fixture
def sample_snapshot():
    """Пример snapshot данных."""
    return {
        "uuid-1": 1073741824.0,  # 1 GB
        "uuid-2": 2147483648.0,  # 2 GB
        "uuid-3": 5368709120.0,  # 5 GB
    }


# ============== Тесты сохранения snapshot в Redis ==============

async def test_save_snapshot_to_redis_success(service, mock_cache, sample_snapshot):
    """Тест успешного сохранения snapshot в Redis."""
    mock_cache.set = AsyncMock(return_value=True)

    result = await service._save_snapshot_to_redis(sample_snapshot)

    assert result is True
    assert mock_cache.set.call_count == 2  # snapshot + time

    # Проверяем что сохранён snapshot
    first_call = mock_cache.set.call_args_list[0]
    assert first_call[0][0] == TRAFFIC_SNAPSHOT_KEY
    assert first_call[0][1] == sample_snapshot


async def test_save_snapshot_to_redis_failure(service, mock_cache, sample_snapshot):
    """Тест неудачного сохранения snapshot в Redis."""
    mock_cache.set = AsyncMock(return_value=False)

    result = await service._save_snapshot_to_redis(sample_snapshot)

    assert result is False


async def test_save_snapshot_to_redis_exception(service, mock_cache, sample_snapshot):
    """Тест обработки исключения при сохранении."""
    mock_cache.set = AsyncMock(side_effect=Exception("Redis error"))

    result = await service._save_snapshot_to_redis(sample_snapshot)

    assert result is False


# ============== Тесты загрузки snapshot из Redis ==============

async def test_load_snapshot_from_redis_success(service, mock_cache, sample_snapshot):
    """Тест успешной загрузки snapshot из Redis."""
    mock_cache.get = AsyncMock(return_value=sample_snapshot)

    result = await service._load_snapshot_from_redis()

    assert result == sample_snapshot
    mock_cache.get.assert_called_once_with(TRAFFIC_SNAPSHOT_KEY)


async def test_load_snapshot_from_redis_empty(service, mock_cache):
    """Тест загрузки когда snapshot отсутствует."""
    mock_cache.get = AsyncMock(return_value=None)

    result = await service._load_snapshot_from_redis()

    assert result is None


async def test_load_snapshot_from_redis_invalid_data(service, mock_cache):
    """Тест загрузки невалидных данных."""
    mock_cache.get = AsyncMock(return_value="not a dict")

    result = await service._load_snapshot_from_redis()

    assert result is None


async def test_load_snapshot_from_redis_exception(service, mock_cache):
    """Тест обработки исключения при загрузке."""
    mock_cache.get = AsyncMock(side_effect=Exception("Redis error"))

    result = await service._load_snapshot_from_redis()

    assert result is None


# ============== Тесты времени snapshot ==============

async def test_get_snapshot_time_from_redis_success(service, mock_cache):
    """Тест получения времени snapshot."""
    test_time = datetime(2024, 1, 15, 12, 30, 0)
    mock_cache.get = AsyncMock(return_value=test_time.isoformat())

    result = await service._get_snapshot_time_from_redis()

    assert result == test_time
    mock_cache.get.assert_called_once_with(TRAFFIC_SNAPSHOT_TIME_KEY)


async def test_get_snapshot_time_from_redis_empty(service, mock_cache):
    """Тест когда время отсутствует."""
    mock_cache.get = AsyncMock(return_value=None)

    result = await service._get_snapshot_time_from_redis()

    assert result is None


# ============== Тесты has_snapshot ==============

async def test_has_snapshot_redis_exists(service, mock_cache, sample_snapshot):
    """Тест has_snapshot когда snapshot есть в Redis."""
    mock_cache.get = AsyncMock(return_value=sample_snapshot)

    result = await service.has_snapshot()

    assert result is True


async def test_has_snapshot_memory_fallback(service, mock_cache):
    """Тест has_snapshot с fallback на память."""
    mock_cache.get = AsyncMock(return_value=None)

    # Устанавливаем данные в память
    service._memory_snapshot = {"uuid-1": 1000.0}
    service._memory_snapshot_time = datetime.utcnow()

    result = await service.has_snapshot()

    assert result is True


async def test_has_snapshot_none(service, mock_cache):
    """Тест has_snapshot когда snapshot нет нигде."""
    mock_cache.get = AsyncMock(return_value=None)
    service._memory_snapshot = {}
    service._memory_snapshot_time = None

    result = await service.has_snapshot()

    assert result is False


# ============== Тесты get_snapshot_age_minutes ==============

async def test_get_snapshot_age_minutes_from_redis(service, mock_cache):
    """Тест возраста snapshot из Redis."""
    # Snapshot создан 30 минут назад
    past_time = datetime.utcnow() - timedelta(minutes=30)
    mock_cache.get = AsyncMock(return_value=past_time.isoformat())

    result = await service.get_snapshot_age_minutes()

    assert 29 <= result <= 31  # Допуск на время выполнения


async def test_get_snapshot_age_minutes_memory_fallback(service, mock_cache):
    """Тест возраста snapshot из памяти."""
    mock_cache.get = AsyncMock(return_value=None)
    service._memory_snapshot_time = datetime.utcnow() - timedelta(minutes=15)

    result = await service.get_snapshot_age_minutes()

    assert 14 <= result <= 16


async def test_get_snapshot_age_minutes_no_snapshot(service, mock_cache):
    """Тест возраста когда snapshot нет."""
    mock_cache.get = AsyncMock(return_value=None)
    service._memory_snapshot_time = None

    result = await service.get_snapshot_age_minutes()

    assert result == float('inf')


# ============== Тесты _save_snapshot (с fallback) ==============

async def test_save_snapshot_redis_success(service, mock_cache, sample_snapshot):
    """Тест сохранения snapshot в Redis успешно."""
    mock_cache.set = AsyncMock(return_value=True)

    # Заполняем память чтобы проверить что она очистится
    service._memory_snapshot = {"old": 123.0}
    service._memory_snapshot_time = datetime.utcnow()

    result = await service._save_snapshot(sample_snapshot)

    assert result is True
    assert service._memory_snapshot == {}  # Память очищена
    assert service._memory_snapshot_time is None


async def test_save_snapshot_fallback_to_memory(service, mock_cache, sample_snapshot):
    """Тест fallback на память когда Redis недоступен."""
    mock_cache.set = AsyncMock(return_value=False)

    result = await service._save_snapshot(sample_snapshot)

    assert result is True
    assert service._memory_snapshot == sample_snapshot
    assert service._memory_snapshot_time is not None


# ============== Тесты _get_current_snapshot ==============

async def test_get_current_snapshot_from_redis(service, mock_cache, sample_snapshot):
    """Тест получения snapshot из Redis."""
    mock_cache.get = AsyncMock(return_value=sample_snapshot)

    result = await service._get_current_snapshot()

    assert result == sample_snapshot


async def test_get_current_snapshot_fallback_to_memory(service, mock_cache, sample_snapshot):
    """Тест fallback на память."""
    mock_cache.get = AsyncMock(return_value=None)
    service._memory_snapshot = sample_snapshot

    result = await service._get_current_snapshot()

    assert result == sample_snapshot


# ============== Тесты уведомлений ==============

async def test_save_notification_to_redis(service, mock_cache):
    """Тест сохранения времени уведомления."""
    mock_cache.set = AsyncMock(return_value=True)

    result = await service._save_notification_to_redis("uuid-123")

    assert result is True
    mock_cache.set.assert_called_once()
    call_args = mock_cache.set.call_args
    assert "traffic:notifications:uuid-123" in call_args[0][0]


async def test_get_notification_time_from_redis(service, mock_cache):
    """Тест получения времени уведомления."""
    test_time = datetime(2024, 1, 15, 10, 0, 0)
    mock_cache.get = AsyncMock(return_value=test_time.isoformat())

    result = await service._get_notification_time_from_redis("uuid-123")

    assert result == test_time


async def test_should_send_notification_no_previous(service, mock_cache):
    """Тест should_send_notification когда уведомлений не было."""
    mock_cache.get = AsyncMock(return_value=None)
    service._memory_notification_cache = {}

    result = await service.should_send_notification("uuid-123")

    assert result is True


async def test_should_send_notification_cooldown_active(service, mock_cache):
    """Тест should_send_notification когда кулдаун активен."""
    # Уведомление было 5 минут назад, кулдаун 60 минут
    recent_time = datetime.utcnow() - timedelta(minutes=5)
    mock_cache.get = AsyncMock(return_value=recent_time.isoformat())

    result = await service.should_send_notification("uuid-123")

    assert result is False


async def test_should_send_notification_cooldown_expired(service, mock_cache):
    """Тест should_send_notification когда кулдаун истёк."""
    # Уведомление было 120 минут назад, кулдаун 60 минут
    old_time = datetime.utcnow() - timedelta(minutes=120)
    mock_cache.get = AsyncMock(return_value=old_time.isoformat())

    result = await service.should_send_notification("uuid-123")

    assert result is True


async def test_record_notification_redis(service, mock_cache):
    """Тест record_notification сохраняет в Redis."""
    mock_cache.set = AsyncMock(return_value=True)

    await service.record_notification("uuid-123")

    mock_cache.set.assert_called_once()


async def test_record_notification_fallback_to_memory(service, mock_cache):
    """Тест record_notification с fallback на память."""
    mock_cache.set = AsyncMock(return_value=False)

    await service.record_notification("uuid-123")

    assert "uuid-123" in service._memory_notification_cache


# ============== Тесты create_initial_snapshot ==============

async def test_create_initial_snapshot_uses_existing_redis(service, mock_cache, sample_snapshot):
    """Тест что create_initial_snapshot использует существующий snapshot из Redis."""
    mock_cache.get = AsyncMock(side_effect=[
        sample_snapshot,  # _load_snapshot_from_redis
        (datetime.utcnow() - timedelta(minutes=10)).isoformat(),  # _get_snapshot_time_from_redis
    ])

    with patch.object(service, 'get_all_users_with_traffic', new_callable=AsyncMock) as mock_get_users:
        result = await service.create_initial_snapshot()

        # Не должен вызывать API - используем существующий snapshot
        mock_get_users.assert_not_called()
        assert result == len(sample_snapshot)


async def test_create_initial_snapshot_creates_new(service, mock_cache):
    """Тест создания нового snapshot когда в Redis пусто."""
    mock_cache.get = AsyncMock(return_value=None)
    mock_cache.set = AsyncMock(return_value=True)

    # Мокаем пользователей из API
    mock_user = MagicMock()
    mock_user.uuid = "uuid-1"
    mock_user.user_traffic = MagicMock()
    mock_user.user_traffic.used_traffic_bytes = 1073741824  # 1 GB

    with patch.object(service, 'get_all_users_with_traffic', new_callable=AsyncMock) as mock_get_users:
        mock_get_users.return_value = [mock_user]

        result = await service.create_initial_snapshot()

        mock_get_users.assert_called_once()
        assert result == 1


# ============== Тесты cleanup_notification_cache ==============

async def test_cleanup_notification_cache_removes_old(service, mock_cache):
    """Тест очистки старых записей из памяти."""
    old_time = datetime.utcnow() - timedelta(hours=25)
    recent_time = datetime.utcnow() - timedelta(hours=1)

    service._memory_notification_cache = {
        "uuid-old": old_time,
        "uuid-recent": recent_time,
    }

    await service.cleanup_notification_cache()

    assert "uuid-old" not in service._memory_notification_cache
    assert "uuid-recent" in service._memory_notification_cache
