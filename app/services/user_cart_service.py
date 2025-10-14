import json
import logging
from typing import Optional, Dict, Any
from datetime import timedelta

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

class UserCartService:
    """
    Сервис для работы с корзиной пользователя через Redis
    """
    
    def __init__(self):
        self.redis_client = None
        self._setup_redis()
    
    def _setup_redis(self):
        """Инициализация Redis клиента"""
        try:
            self.redis_client = redis.from_url(settings.REDIS_URL)
        except Exception as e:
            logger.error(f"Ошибка подключения к Redis: {e}")
            raise
    
    async def save_user_cart(self, user_id: int, cart_data: Dict[str, Any], ttl: int = 3600) -> bool:
        """
        Сохранить корзину пользователя в Redis
        
        Args:
            user_id: ID пользователя
            cart_data: Данные корзины (параметры подписки)
            ttl: Время жизни ключа в секундах (по умолчанию 1 час)
        
        Returns:
            bool: Успешность сохранения
        """
        try:
            key = f"user_cart:{user_id}"
            json_data = json.dumps(cart_data, ensure_ascii=False)
            await self.redis_client.setex(key, ttl, json_data)
            logger.info(f"Корзина пользователя {user_id} сохранена в Redis")
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения корзины пользователя {user_id}: {e}")
            return False
    
    async def get_user_cart(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Получить корзину пользователя из Redis
        
        Args:
            user_id: ID пользователя
        
        Returns:
            dict: Данные корзины или None
        """
        try:
            key = f"user_cart:{user_id}"
            json_data = await self.redis_client.get(key)
            if json_data:
                cart_data = json.loads(json_data)
                logger.info(f"Корзина пользователя {user_id} загружена из Redis")
                return cart_data
            return None
        except Exception as e:
            logger.error(f"Ошибка получения корзины пользователя {user_id}: {e}")
            return None
    
    async def delete_user_cart(self, user_id: int) -> bool:
        """
        Удалить корзину пользователя из Redis
        
        Args:
            user_id: ID пользователя
        
        Returns:
            bool: Успешность удаления
        """
        try:
            key = f"user_cart:{user_id}"
            result = await self.redis_client.delete(key)
            if result:
                logger.info(f"Корзина пользователя {user_id} удалена из Redis")
            return bool(result)
        except Exception as e:
            logger.error(f"Ошибка удаления корзины пользователя {user_id}: {e}")
            return False
    
    async def has_user_cart(self, user_id: int) -> bool:
        """
        Проверить наличие корзины у пользователя
        
        Args:
            user_id: ID пользователя
        
        Returns:
            bool: Наличие корзины
        """
        try:
            key = f"user_cart:{user_id}"
            exists = await self.redis_client.exists(key)
            return bool(exists)
        except Exception as e:
            logger.error(f"Ошибка проверки наличия корзины пользователя {user_id}: {e}")
            return False

# Глобальный экземпляр сервиса
user_cart_service = UserCartService()