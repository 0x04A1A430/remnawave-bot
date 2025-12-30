import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.user_cart_service import UserCartService
from app.config import settings

# Мок для Redis клиента
class MockRedis:
    def __init__(self):
        self.storage = {}
    
    async def setex(self, key, ttl, value):
        self.storage[key] = value
        return True
    
    async def get(self, key):
        return self.storage.get(key)
    
    async def delete(self, key):
        if key in self.storage:
            del self.storage[key]
            return 1
        return 0
    
    async def exists(self, key):
        return 1 if key in self.storage else 0

@pytest.fixture
def mock_redis():
    return MockRedis()

@pytest.fixture
def user_cart_service(mock_redis):
    service = UserCartService()
    service._redis_client = mock_redis
    service._initialized = True
    return service

async def test_save_user_cart(user_cart_service, mock_redis):
    """Тест сохранения корзины пользователя"""
    user_id = 12345
    cart_data = {
        'period_days': 30,
        'countries': ['ru', 'us'],
        'devices': 3,
        'traffic_gb': 10,
        'total_price': 50000
    }
    
    result = await user_cart_service.save_user_cart(user_id, cart_data)
    
    assert result is True
    assert f"user_cart:{user_id}" in mock_redis.storage
    assert cart_data == eval(mock_redis.storage[f"user_cart:{user_id}"])

async def test_get_user_cart(user_cart_service, mock_redis):
    """Тест получения корзины пользователя"""
    user_id = 12345
    cart_data = {
        'period_days': 30,
        'countries': ['ru', 'us'],
        'devices': 3,
        'traffic_gb': 10,
        'total_price': 50000
    }
    
    # Сохраняем данные
    await user_cart_service.save_user_cart(user_id, cart_data)
    
    # Получаем данные
    result = await user_cart_service.get_user_cart(user_id)
    
    assert result == cart_data

async def test_get_user_cart_not_found(user_cart_service):
    """Тест получения несуществующей корзины пользователя"""
    user_id = 99999
    
    result = await user_cart_service.get_user_cart(user_id)
    
    assert result is None

async def test_delete_user_cart(user_cart_service, mock_redis):
    """Тест удаления корзины пользователя"""
    user_id = 12345
    cart_data = {
        'period_days': 30,
        'countries': ['ru', 'us'],
        'devices': 3,
        'traffic_gb': 10,
        'total_price': 50000
    }
    
    # Сохраняем данные
    await user_cart_service.save_user_cart(user_id, cart_data)
    assert f"user_cart:{user_id}" in mock_redis.storage
    
    # Удаляем данные
    result = await user_cart_service.delete_user_cart(user_id)
    
    assert result is True
    assert f"user_cart:{user_id}" not in mock_redis.storage

async def test_delete_user_cart_not_found(user_cart_service):
    """Тест удаления несуществующей корзины пользователя"""
    user_id = 99999
    
    result = await user_cart_service.delete_user_cart(user_id)
    
    assert result is False


async def test_has_user_cart(user_cart_service, mock_redis):
    """Тест проверки наличия корзины пользователя"""
    user_id = 12345
    cart_data = {
        'period_days': 30,
        'countries': ['ru', 'us'],
        'devices': 3,
        'traffic_gb': 10,
        'total_price': 50000
    }
    
    # Проверяем, что корзины нет
    result = await user_cart_service.has_user_cart(user_id)
    assert result is False
    
    # Сохраняем данные
    await user_cart_service.save_user_cart(user_id, cart_data)
    
    # Проверяем, что корзина есть
    result = await user_cart_service.has_user_cart(user_id)
    assert result is True

async def test_has_user_cart_not_found(user_cart_service):
    """Тест проверки отсутствия корзины пользователя"""
    user_id = 99999
    
    result = await user_cart_service.has_user_cart(user_id)
    
    assert result is False