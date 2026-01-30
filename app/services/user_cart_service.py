import json
import logging
from typing import Any

import redis.asyncio as redis

from app.config import settings


logger = logging.getLogger(__name__)


class UserCartService:
    """
    Сервис для работы с корзиной пользователя через Redis.

    Использует ленивую инициализацию Redis-клиента для graceful fallback
    при недоступности Redis.
    """

    def __init__(self):
        self._redis_client: redis.Redis | None = None
        self._initialized: bool = False

    def _get_redis_client(self) -> redis.Redis | None:
        """Ленивая инициализация Redis клиента."""
        if self._initialized:
            return self._redis_client

        try:
            self._redis_client = redis.from_url(settings.REDIS_URL)
            self._initialized = True
            logger.debug('Redis клиент для корзины инициализирован')
        except Exception as e:
            logger.warning(f'Не удалось подключиться к Redis для корзины: {e}')
            self._redis_client = None
            self._initialized = True

        return self._redis_client

    async def save_user_cart(self, user_id: int, cart_data: dict[str, Any], ttl: int | None = None) -> bool:
        """
        Сохранить корзину пользователя в Redis.

        Args:
            user_id: ID пользователя
            cart_data: Данные корзины (параметры подписки)
            ttl: Время жизни ключа в секундах (по умолчанию из settings.CART_TTL_SECONDS)

        Returns:
            bool: Успешность сохранения
        """
        client = self._get_redis_client()
        if client is None:
            logger.warning(f'🛒 Redis недоступен, корзина пользователя {user_id} НЕ сохранена')
            return False

        try:
            key = f'user_cart:{user_id}'
            json_data = json.dumps(cart_data, ensure_ascii=False)
            effective_ttl = ttl if ttl is not None else settings.CART_TTL_SECONDS
            await client.setex(key, effective_ttl, json_data)
            cart_mode = cart_data.get('cart_mode', 'unknown')
            logger.info(f'🛒 Корзина пользователя {user_id} сохранена в Redis (mode={cart_mode}, ttl={effective_ttl}s)')
            return True
        except Exception as e:
            logger.error(f'🛒 Ошибка сохранения корзины пользователя {user_id}: {e}')
            return False

    async def get_user_cart(self, user_id: int) -> dict[str, Any] | None:
        """
        Получить корзину пользователя из Redis.

        Args:
            user_id: ID пользователя

        Returns:
            dict: Данные корзины или None
        """
        client = self._get_redis_client()
        if client is None:
            return None

        try:
            key = f'user_cart:{user_id}'
            json_data = await client.get(key)
            if json_data:
                cart_data = json.loads(json_data)
                logger.debug(f'Корзина пользователя {user_id} загружена из Redis')
                return cart_data
            return None
        except Exception as e:
            logger.error(f'Ошибка получения корзины пользователя {user_id}: {e}')
            return None

    async def delete_user_cart(self, user_id: int) -> bool:
        """
        Удалить корзину пользователя из Redis.

        Args:
            user_id: ID пользователя

        Returns:
            bool: Успешность удаления
        """
        client = self._get_redis_client()
        if client is None:
            return False

        try:
            key = f'user_cart:{user_id}'
            result = await client.delete(key)
            if result:
                logger.debug(f'Корзина пользователя {user_id} удалена из Redis')
            return bool(result)
        except Exception as e:
            logger.error(f'Ошибка удаления корзины пользователя {user_id}: {e}')
            return False

    async def has_user_cart(self, user_id: int) -> bool:
        """
        Проверить наличие корзины у пользователя.

        Args:
            user_id: ID пользователя

        Returns:
            bool: Наличие корзины
        """
        client = self._get_redis_client()
        if client is None:
            logger.warning(f'🛒 Redis недоступен, проверка корзины пользователя {user_id} невозможна')
            return False

        try:
            key = f'user_cart:{user_id}'
            exists = await client.exists(key)
            result = bool(exists)
            logger.info(f'🛒 Проверка корзины пользователя {user_id}: {"найдена" if result else "не найдена"}')
            return result
        except Exception as e:
            logger.error(f'🛒 Ошибка проверки наличия корзины пользователя {user_id}: {e}')
            return False


# Глобальный экземпляр сервиса (инициализация Redis отложена)
user_cart_service = UserCartService()
