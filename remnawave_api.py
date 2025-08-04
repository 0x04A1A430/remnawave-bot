import aiohttp
import asyncio
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import json

logger = logging.getLogger(__name__)

class RemnaWaveAPI:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.session = None
        
    async def _get_session(self):
        if self.session is None or self.session.closed:
            headers = {
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json'
            }
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(
                headers=headers,
                timeout=timeout
            )
        return self.session
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Optional[Dict]:
        url = f"{self.base_url}{endpoint}"
        session = await self._get_session()
        
        try:
            async with session.request(method, url, json=data) as response:
                response_text = await response.text()
                logger.debug(f"API {method} {endpoint} -> {response.status}: {response_text}")
                
                if response.status == 200:
                    return await response.json() if response_text else None
                elif response.status == 201:
                    return await response.json() if response_text else None
                elif response.status == 404:
                    logger.warning(f"API 404 for {endpoint}")
                    return None
                else:
                    logger.error(f"API error: {response.status}, {response_text}")
                    return None
        except Exception as e:
            logger.error(f"Request error for {endpoint}: {e}")
            return None
    
    # User management
    async def create_user(self, username: str, password: str = None, 
                         traffic_limit: int = 0, expiry_time: str = None,
                         telegram_id: int = None, email: str = None,
                         internal_squads: List[str] = None, activeInternalSquads: List[str] = None):
        if expiry_time is None:
            expiry_time = (datetime.now() + timedelta(days=30)).isoformat() + 'Z'
        
        data = {
            'username': username,
            'trafficLimitBytes': traffic_limit,
            'expireAt': expiry_time,
            'status': 'ACTIVE'
        }
        
        if password:
            data['trojanPassword'] = password
        if telegram_id:
            data['telegramId'] = telegram_id
        if email:
            data['email'] = email
        if internal_squads:
            data['internalSquads'] = internal_squads
        if activeInternalSquads:
            data['activeInternalSquads'] = activeInternalSquads

        return await self._make_request('POST', '/api/users', data)
    
    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[Dict]:
        result = await self._make_request('GET', f'/api/users/by-telegram-id/{telegram_id}')
        if result:
            # Обрабатываем разные структуры ответа
            if 'data' in result:
                return result['data']
            elif 'response' in result:
                return result['response']
            else:
                # Возможно данные находятся в корне
                return result
        return None
    
    async def get_user_by_uuid(self, uuid: str) -> Optional[Dict]:
        logger.debug(f"Getting user by UUID: {uuid}")
        result = await self._make_request('GET', f'/api/users/{uuid}')
        
        if result:
            logger.debug(f"Raw API response for get_user_by_uuid: {result}")
            # Обрабатываем разные структуры ответа
            if 'data' in result:
                user_data = result['data']
                logger.debug(f"Found user data in 'data' field: {user_data}")
                return user_data
            elif 'response' in result:
                user_data = result['response']
                logger.debug(f"Found user data in 'response' field: {user_data}")
                return user_data
            else:
                # Возможно данные находятся в корне ответа
                logger.debug(f"Using root response as user data: {result}")
                return result
        else:
            logger.error(f"No result from API for user UUID: {uuid}")
        
        return None
    
    async def get_user_by_short_uuid(self, short_uuid: str) -> Optional[Dict]:
        """Get user data by short UUID"""
        logger.debug(f"Getting user by short UUID: {short_uuid}")
        result = await self._make_request('GET', f'/api/users/by-short-uuid/{short_uuid}')
        
        if result:
            logger.debug(f"Raw API response for get_user_by_short_uuid: {result}")
            # Обрабатываем разные структуры ответа
            if 'data' in result:
                user_data = result['data']
                logger.debug(f"Found user data in 'data' field: {user_data}")
                return user_data
            elif 'response' in result:
                user_data = result['response']
                logger.debug(f"Found user data in 'response' field: {user_data}")
                return user_data
            else:
                # Возможно данные находятся в корне ответа
                logger.debug(f"Using root response as user data: {result}")
                return result
        else:
            logger.error(f"No result from API for user short UUID: {short_uuid}")
        
        return None
    
    async def update_user(self, uuid: str, data: Dict) -> Optional[Dict]:
        update_data = {'uuid': uuid, **data}
        logger.debug(f"Updating user {uuid} with data: {update_data}")
        result = await self._make_request('PATCH', '/api/users', update_data)
        logger.debug(f"Update user result: {result}")
        return result
    
    async def update_user_expiry(self, short_uuid: str, new_expiry: str) -> Optional[Dict]:
        """Update user expiry date by short UUID"""
        # Сначала получаем пользователя по short_uuid чтобы получить его UUID
        user_data = await self.get_user_by_short_uuid(short_uuid)
        if not user_data:
            logger.error(f"Could not find user with short UUID: {short_uuid}")
            return None
        
        user_uuid = user_data.get('uuid')
        if not user_uuid:
            logger.error(f"Could not get UUID from user data: {user_data}")
            return None
        
        # Обновляем пользователя по UUID
        update_data = {
            'expireAt': new_expiry
        }
        
        logger.info(f"Updating user {user_uuid} expiry to: {new_expiry}")
        return await self.update_user(user_uuid, update_data)
    
    async def get_user_accessible_nodes(self, uuid: str) -> Optional[List]:
        result = await self._make_request('GET', f'/api/users/{uuid}/accessible-nodes')
        if result and 'data' in result:
            return result['data']
        return []
    
    async def get_subscription_url(self, short_uuid: str) -> str:
        try:
            logger.info(f"Getting subscription URL for shortUuid: {short_uuid}")
            # Проверяем доступность подписки через API
            result = await self._make_request('GET', f'/api/sub/{short_uuid}')
            logger.debug(f"API /api/sub/{short_uuid} returned: {result}")
            
            # Формируем правильную ссылку на основе base_url
            # Заменяем adminka на sub в URL
            if 'adminka.' in self.base_url:
                sub_url = self.base_url.replace('adminka.', 'sub.')
            else:
                sub_url = self.base_url
                
            subscription_url = f"{sub_url}/sub/{short_uuid}"
            logger.info(f"Generated subscription URL: {subscription_url}")
            
            return subscription_url

        except Exception as e:
            logger.error(f"Failed to get subscription URL: {e}")
            # Fallback с заменой домена
            if 'adminka.' in self.base_url:
                sub_url = self.base_url.replace('adminka.', 'sub.')
            else:
                sub_url = self.base_url
            return f"{sub_url}/sub/{short_uuid}"

    # Subscription management
    async def get_subscription_by_short_uuid(self, short_uuid: str) -> Optional[str]:
        result = await self._make_request('GET', f'/api/sub/{short_uuid}')
        return result
    
    async def get_subscription_info(self, short_uuid: str) -> Optional[Dict]:
        """Get subscription info by short UUID"""
        result = await self._make_request('GET', f'/api/sub/{short_uuid}/info')
        if result and 'response' in result:
            return result['response']
        return None
    
    async def get_raw_subscription(self, short_uuid: str) -> Optional[str]:
        result = await self._make_request('GET', f'/api/sub/{short_uuid}/raw')
        return result
    
    # Internal squads (subscription plans)
    async def get_internal_squads(self) -> Optional[List]:
        result = await self._make_request('GET', '/api/internal-squads')
        if result and 'data' in result:
            return result['data']
        return []
    
    async def create_internal_squad(self, name: str, description: str = None) -> Optional[Dict]:
        data = {'name': name}
        if description:
            data['description'] = description
        return await self._make_request('POST', '/api/internal-squads', data)
    
    async def add_user_to_squad(self, squad_uuid: str, user_uuids: List[str]) -> Optional[Dict]:
        data = {'userUuids': user_uuids}
        return await self._make_request('POST', f'/api/internal-squads/{squad_uuid}/bulk-actions/add-users', data)
    
    # Usage statistics
    async def get_user_usage_by_range(self, uuid: str, start_date: str, end_date: str) -> Optional[Dict]:
        params = f"?startDate={start_date}&endDate={end_date}"
        result = await self._make_request('GET', f'/api/users/stats/usage/{uuid}/range{params}')
        if result and 'data' in result:
            return result['data']
        return None
    
    # System stats
    async def get_system_stats(self) -> Optional[Dict]:
        result = await self._make_request('GET', '/api/system/stats')
        if result and 'data' in result:
            return result['data']
        return None
    
    async def get_nodes_statistics(self) -> Optional[Dict]:
        result = await self._make_request('GET', '/api/system/stats/nodes')
        if result and 'data' in result:
            return result['data']
        return None
    
    # Nodes management
    async def get_all_nodes(self) -> Optional[List]:
        result = await self._make_request('GET', '/api/nodes')
        if result and 'data' in result:
            return result['data']
        return []
    
    async def restart_all_nodes(self) -> Optional[Dict]:
        return await self._make_request('POST', '/api/nodes/actions/restart-all')
    
    # User actions
    async def revoke_user_subscription(self, uuid: str) -> Optional[Dict]:
        """Revoke user subscription"""
        return await self._make_request('POST', f'/api/users/{uuid}/actions/revoke')
    
    async def disable_user(self, uuid: str) -> Optional[Dict]:
        """Disable user"""
        return await self._make_request('POST', f'/api/users/{uuid}/actions/disable')
    
    async def enable_user(self, uuid: str) -> Optional[Dict]:
        """Enable user"""
        return await self._make_request('POST', f'/api/users/{uuid}/actions/enable')
    
    async def reset_user_traffic(self, uuid: str) -> Optional[Dict]:
        """Reset user traffic"""
        return await self._make_request('POST', f'/api/users/{uuid}/actions/reset-traffic')
    
    # Bulk operations
    async def bulk_delete_users(self, user_uuids: List[str]) -> Optional[Dict]:
        """Bulk delete users by UUID list"""
        data = {'userUuids': user_uuids}
        return await self._make_request('POST', '/api/users/bulk/delete', data)
    
    async def bulk_update_users(self, updates: List[Dict]) -> Optional[Dict]:
        """Bulk update users"""
        data = {'updates': updates}
        return await self._make_request('POST', '/api/users/bulk/update', data)
    
    async def bulk_reset_traffic(self, user_uuids: List[str]) -> Optional[Dict]:
        """Bulk reset traffic for users"""
        data = {'userUuids': user_uuids}
        return await self._make_request('POST', '/api/users/bulk/reset-traffic', data)
    
    # Search methods
    async def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Get user by username"""
        result = await self._make_request('GET', f'/api/users/by-username/{username}')
        if result and 'data' in result:
            return result['data']
        return None
    
    async def get_user_by_email(self, email: str) -> Optional[Dict]:
        """Get user by email"""
        result = await self._make_request('GET', f'/api/users/by-email/{email}')
        if result and 'data' in result:
            return result['data']
        return None
    
    async def get_user_by_tag(self, tag: str) -> Optional[Dict]:
        """Get user by tag"""
        result = await self._make_request('GET', f'/api/users/by-tag/{tag}')
        if result and 'data' in result:
            return result['data']
        return None
    
    # Config profiles
    async def get_config_profiles(self) -> Optional[List]:
        """Get all config profiles"""
        result = await self._make_request('GET', '/api/config-profiles')
        if result and 'data' in result:
            return result['data']
        return []
    
    # Subscription templates
    async def get_subscription_template(self, template_type: str) -> Optional[Dict]:
        """Get subscription template by type"""
        result = await self._make_request('GET', f'/api/subscription-templates/{template_type}')
        return result
    
    async def update_subscription_template(self, template_data: Dict) -> Optional[Dict]:
        """Update subscription template"""
        return await self._make_request('PUT', '/api/subscription-templates', template_data)

    async def get_internal_squads_list(self) -> Optional[List[Dict]]:
        """Get list of internal squads with details"""
        logger.info("Fetching internal squads list")
        result = await self._make_request('GET', '/api/internal-squads')
        
        if result:
            logger.debug(f"Raw squads API response: {result}")
            
            # Проверяем разные возможные структуры ответа RemnaWave API
            if 'response' in result and 'internalSquads' in result['response']:
                # Структура: {"response": {"total": N, "internalSquads": [...]}}
                squads = result['response']['internalSquads']
                logger.info(f"Found {len(squads)} squads in response.internalSquads")
                return squads
            elif 'data' in result:
                # Структура: {"data": [...]}
                squads = result['data']
                logger.info(f"Found {len(squads)} squads in data")
                return squads
            elif isinstance(result, list):
                # Прямой массив: [...]
                logger.info(f"Found {len(result)} squads as direct array")
                return result
            elif 'internalSquads' in result:
                # Структура: {"internalSquads": [...]}
                squads = result['internalSquads']
                logger.info(f"Found {len(squads)} squads in internalSquads")
                return squads
            else:
                logger.warning(f"Unexpected API response structure: {result}")
                return []
        else:
            logger.error("No result from squads API")
            return []
