import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Subscription, User
from app.external.remnawave_api import (
    RemnaWaveAPI, RemnaWaveUser, UserStatus, 
    TrafficLimitStrategy, RemnaWaveAPIError
)
from app.database.crud.user import get_user_by_id

logger = logging.getLogger(__name__)


class SubscriptionService:
    
    def __init__(self):
        self.api = RemnaWaveAPI(
            base_url=settings.REMNAWAVE_API_URL,
            api_key=settings.REMNAWAVE_API_KEY
        )
    
    async def create_remnawave_user(
        self, 
        db: AsyncSession, 
        subscription: Subscription
    ) -> Optional[RemnaWaveUser]:
        
        try:
            user = await get_user_by_id(db, subscription.user_id)
            if not user:
                logger.error(f"Пользователь {subscription.user_id} не найден")
                return None
            
            async with self.api as api:
                existing_users = await api.get_user_by_telegram_id(user.telegram_id)
                if existing_users:
                    remnawave_user = existing_users[0]
                    updated_user = await api.update_user(
                        uuid=remnawave_user.uuid,
                        status=UserStatus.ACTIVE,
                        expire_at=subscription.end_date,
                        traffic_limit_bytes=self._gb_to_bytes(subscription.traffic_limit_gb),
                        traffic_limit_strategy=TrafficLimitStrategy.MONTH,
                        hwid_device_limit=subscription.device_limit,
                        active_internal_squads=subscription.connected_squads
                    )
                else:
                    username = f"user_{user.telegram_id}"
                    updated_user = await api.create_user(
                        username=username,
                        expire_at=subscription.end_date,
                        status=UserStatus.ACTIVE,
                        traffic_limit_bytes=self._gb_to_bytes(subscription.traffic_limit_gb),
                        traffic_limit_strategy=TrafficLimitStrategy.MONTH,
                        telegram_id=user.telegram_id,
                        hwid_device_limit=subscription.device_limit,
                        description=f"Bot user: {user.full_name}",
                        active_internal_squads=subscription.connected_squads
                    )
                
                subscription.remnawave_short_uuid = updated_user.short_uuid
                subscription.subscription_url = updated_user.subscription_url 
                user.remnawave_uuid = updated_user.uuid
                
                await db.commit()
                
                logger.info(f"✅ Создан RemnaWave пользователь для подписки {subscription.id}")
                logger.info(f"🔗 Ссылка на подписку: {updated_user.subscription_url}")
                logger.info(f"📊 Стратегия сброса трафика: MONTH") 
                return updated_user
                
        except RemnaWaveAPIError as e:
            logger.error(f"Ошибка RemnaWave API: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка создания RemnaWave пользователя: {e}")
            return None
    
    async def update_remnawave_user(
        self,
        db: AsyncSession,
        subscription: Subscription
    ) -> Optional[RemnaWaveUser]:
        
        try:
            user = await get_user_by_id(db, subscription.user_id)
            if not user or not user.remnawave_uuid:
                logger.error(f"RemnaWave UUID не найден для пользователя {subscription.user_id}")
                return None
            
            async with self.api as api:
                updated_user = await api.update_user(
                    uuid=user.remnawave_uuid,
                    status=UserStatus.ACTIVE if subscription.is_active else UserStatus.EXPIRED,
                    expire_at=subscription.end_date,
                    traffic_limit_bytes=self._gb_to_bytes(subscription.traffic_limit_gb),
                    traffic_limit_strategy=TrafficLimitStrategy.MONTH, 
                    hwid_device_limit=subscription.device_limit,
                    active_internal_squads=subscription.connected_squads
                )
                
                subscription.subscription_url = updated_user.subscription_url
                await db.commit()
                
                logger.info(f"✅ Обновлен RemnaWave пользователь {user.remnawave_uuid}")
                logger.info(f"📊 Стратегия сброса трафика: MONTH") 
                return updated_user
                
        except RemnaWaveAPIError as e:
            logger.error(f"Ошибка обновления RemnaWave пользователя: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка обновления RemnaWave пользователя: {e}")
            return None
    
    async def disable_remnawave_user(self, user_uuid: str) -> bool:
        
        try:
            async with self.api as api:
                await api.disable_user(user_uuid)
                logger.info(f"✅ Отключен RemnaWave пользователь {user_uuid}")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка отключения RemnaWave пользователя: {e}")
            return False
    
    async def revoke_subscription(
        self,
        db: AsyncSession,
        subscription: Subscription
    ) -> Optional[str]:
        
        try:
            user = await get_user_by_id(db, subscription.user_id)
            if not user or not user.remnawave_uuid:
                return None
            
            async with self.api as api:
                updated_user = await api.revoke_user_subscription(user.remnawave_uuid)
                
                subscription.remnawave_short_uuid = updated_user.short_uuid
                subscription.subscription_url = updated_user.subscription_url
                await db.commit()
                
                logger.info(f"✅ Обновлена ссылка подписки для пользователя {user.telegram_id}")
                return updated_user.subscription_url
                
        except Exception as e:
            logger.error(f"Ошибка обновления ссылки подписки: {e}")
            return None
    
    async def get_subscription_info(self, short_uuid: str) -> Optional[dict]:
        
        try:
            async with self.api as api:
                info = await api.get_subscription_info(short_uuid)
                return info
                
        except Exception as e:
            logger.error(f"Ошибка получения информации о подписке: {e}")
            return None
    
    async def sync_subscription_usage(
        self,
        db: AsyncSession,
        subscription: Subscription
    ) -> bool:
        
        try:
            user = await get_user_by_id(db, subscription.user_id)
            if not user or not user.remnawave_uuid:
                return False
            
            async with self.api as api:
                remnawave_user = await api.get_user_by_uuid(user.remnawave_uuid)
                if not remnawave_user:
                    return False
                
                used_gb = self._bytes_to_gb(remnawave_user.used_traffic_bytes)
                subscription.traffic_used_gb = used_gb
                
                await db.commit()
                
                logger.debug(f"Синхронизирован трафик для подписки {subscription.id}: {used_gb} ГБ")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка синхронизации трафика: {e}")
            return False
    
    async def calculate_subscription_price(
        self,
        period_days: int,
        traffic_gb: int,
        server_squad_ids: List[int], 
        devices: int,
        db: AsyncSession 
    ) -> Tuple[int, List[int]]:
    
        from app.config import PERIOD_PRICES, TRAFFIC_PRICES
        from app.database.crud.server_squad import get_server_squad_by_id
    
        base_price = PERIOD_PRICES.get(period_days, 0)
        traffic_price = TRAFFIC_PRICES.get(traffic_gb, 0)
    
        server_prices = []
        total_servers_price = 0
    
        for server_id in server_squad_ids:
            server = await get_server_squad_by_id(db, server_id)
            if server and server.is_available and not server.is_full:
                server_prices.append(server.price_kopeks)
                total_servers_price += server.price_kopeks
            else:
                server_prices.append(0)
    
        devices_price = max(0, devices - 1) * settings.PRICE_PER_DEVICE
        
        total_price = base_price + traffic_price + total_servers_price + devices_price
        return total_price, server_prices
    
    async def _get_countries_price(self, country_uuids: List[str]) -> int:
        # TODO: Реализовать получение цен из базы данных сквадов
        # Пока возвращаем базовую логику
        price_per_country = 1000 
        return len(country_uuids) * price_per_country
    
    def _gb_to_bytes(self, gb: int) -> int:
        if gb == 0: 
            return 0
        return gb * 1024 * 1024 * 1024
    
    def _bytes_to_gb(self, bytes_value: int) -> float:
        if bytes_value == 0:
            return 0.0
        return bytes_value / (1024 * 1024 * 1024)