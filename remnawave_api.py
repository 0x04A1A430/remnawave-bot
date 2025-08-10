import aiohttp
import asyncio
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import json

logger = logging.getLogger(__name__)

class RemnaWaveAPI:
    def __init__(self, base_url: str, token: str, subscription_base_url: str = None):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.subscription_base_url = subscription_base_url 
        self.session = None
        
    async def _get_session(self):
        if self.session is None or self.session.closed:
            headers = {
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
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
    
    async def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, params: Optional[Dict] = None) -> Optional[Dict]:
        if not endpoint.startswith('/api/'):
            endpoint = '/api' + endpoint
            
        url = f"{self.base_url}{endpoint}"
        session = await self._get_session()
        
        try:
            logger.debug(f"Making {method} request to {url}")
            async with session.request(method, url, json=data, params=params) as response:
                content_type = response.headers.get('Content-Type', '')
                logger.debug(f"Response Content-Type: {content_type}, Status: {response.status}")
                
                response_text = await response.text()
                
                if 'text/html' in content_type:
                    logger.error(f"Got HTML response instead of JSON from {url}")
                    logger.debug(f"HTML Response (first 500 chars): {response_text[:500]}")
                    return None
                
                if response.status in [200, 201, 204]:
                    if response_text:
                        try:
                            return await response.json()
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to decode JSON from {url}: {e}")
                            logger.debug(f"Raw response: {response_text[:500]}")
                            return None
                    return {'success': True}  # Для статуса 204 (No Content)
                elif response.status == 404:
                    logger.warning(f"API 404 for {endpoint}")
                    return None
                else:
                    logger.error(f"API error: {response.status}, {response_text[:200]}")
                    return None
        except aiohttp.ContentTypeError as e:
            logger.error(f"Content type error for {endpoint}: {e}")
            return None
        except Exception as e:
            logger.error(f"Request error for {endpoint}: {e}")
            return None

    async def delete_user(self, uuid: str) -> Optional[Dict]:
        """
        Удаляет пользователя по полному UUID
        """
        try:
            logger.info(f"Deleting user with UUID: {uuid}")
            result = await self._make_request('DELETE', f'/api/users/{uuid}')
            
            if result is not None:
                logger.info(f"Successfully deleted user {uuid}")
                return result
            else:
                logger.error(f"Failed to delete user {uuid}")
                return None
                
        except Exception as e:
            logger.error(f"Error deleting user {uuid}: {e}")
            return None

    async def delete_user_by_short_uuid(self, short_uuid: str) -> Optional[Dict]:
        """
        Удаляет пользователя по short_uuid (сначала получает полный UUID, затем удаляет)
        """
        try:
            logger.info(f"Deleting user by short UUID: {short_uuid}")
            
            # Сначала получаем полную информацию о пользователе
            user_data = await self.get_user_by_short_uuid(short_uuid)
            
            if not user_data:
                logger.warning(f"User with short_uuid {short_uuid} not found")
                return None
            
            full_uuid = user_data.get('uuid')
            if not full_uuid:
                logger.error(f"Could not get full UUID for short_uuid {short_uuid}")
                return None
            
            # Удаляем пользователя по полному UUID
            result = await self.delete_user(full_uuid)
            
            if result:
                logger.info(f"Successfully deleted user {short_uuid} (UUID: {full_uuid})")
                return {
                    'success': True,
                    'short_uuid': short_uuid,
                    'full_uuid': full_uuid,
                    'username': user_data.get('username', 'unknown')
                }
            else:
                logger.error(f"Failed to delete user {short_uuid}")
                return None
                
        except Exception as e:
            logger.error(f"Error deleting user by short UUID {short_uuid}: {e}")
            return None

    async def bulk_delete_users_by_short_uuids(self, short_uuids: List[str]) -> Dict[str, Any]:
        """
        Массовое удаление пользователей по списку short_uuid
        """
        try:
            logger.info(f"Bulk deleting {len(short_uuids)} users by short UUIDs")
            
            results = {
                'total': len(short_uuids),
                'success': 0,
                'failed': 0,
                'errors': [],
                'deleted_users': []
            }
            
            for short_uuid in short_uuids:
                try:
                    result = await self.delete_user_by_short_uuid(short_uuid)
                    
                    if result and result.get('success'):
                        results['success'] += 1
                        results['deleted_users'].append({
                            'short_uuid': short_uuid,
                            'full_uuid': result.get('full_uuid'),
                            'username': result.get('username')
                        })
                        logger.debug(f"Successfully deleted user {short_uuid}")
                    else:
                        results['failed'] += 1
                        results['errors'].append(f"Failed to delete {short_uuid}")
                        logger.warning(f"Failed to delete user {short_uuid}")
                    
                    # Небольшая пауза между запросами, чтобы не перегружать API
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    results['failed'] += 1
                    results['errors'].append(f"Error deleting {short_uuid}: {str(e)}")
                    logger.error(f"Error deleting user {short_uuid}: {e}")
            
            logger.info(f"Bulk deletion completed: {results['success']} success, {results['failed']} failed")
            return results
            
        except Exception as e:
            logger.error(f"Error in bulk_delete_users_by_short_uuids: {e}")
            return {
                'total': len(short_uuids),
                'success': 0,
                'failed': len(short_uuids),
                'errors': [f"Critical error: {str(e)}"],
                'deleted_users': []
            }

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

        result = await self._make_request('POST', '/api/users', data)
        
        if result and 'response' in result:
            return {'data': result['response']}
        return result
    
    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[Dict]:
        logger.debug(f"Getting user by Telegram ID: {telegram_id}")
    
        result = await self._make_request('GET', f'/api/users/by-telegram-id/{telegram_id}')
    
        if not result:
            logger.debug(f"No result for Telegram ID {telegram_id}")
            return None
    
        logger.debug(f"Raw API response type: {type(result)}")
        logger.debug(f"Raw API response: {result}")
    
        user_data = None
    
        if isinstance(result, dict):
            if 'response' in result:
                response_data = result['response']
                if isinstance(response_data, dict):
                    user_data = response_data
                elif isinstance(response_data, list) and response_data:
                    user_data = response_data[0]  # Берем первого пользователя
            elif 'data' in result:
                data = result['data']
                if isinstance(data, dict):
                    user_data = data
                elif isinstance(data, list) and data:
                    user_data = data[0]
            elif 'user' in result:
                user_data = result['user']
            else:
                if 'telegramId' in result or 'username' in result:
                    user_data = result
        elif isinstance(result, list):
            if result:
                user_data = result[0]
    
        if user_data and isinstance(user_data, dict):
            if user_data.get('telegramId') == telegram_id:
                logger.info(f"Found user: {user_data.get('username')} for Telegram ID {telegram_id}")
                
                if user_data.get('shortUuid') and 'subscriptionUrl' not in user_data:
                    try:
                        subscription_url = await self.get_subscription_url(user_data['shortUuid'])
                        user_data['subscriptionUrl'] = subscription_url
                        logger.debug(f"Added subscription URL to user data: {subscription_url}")
                    except Exception as e:
                        logger.warning(f"Could not get subscription URL for user: {e}")
                
                return user_data
            else:
                logger.warning(f"Telegram ID mismatch: expected {telegram_id}, got {user_data.get('telegramId')}")
    
        logger.warning(f"User with Telegram ID {telegram_id} not found or invalid response structure")
        return None
    
    async def get_user_by_uuid(self, uuid: str) -> Optional[Dict]:
        logger.debug(f"Getting user by UUID: {uuid}")
        result = await self._make_request('GET', f'/api/users/{uuid}')
        
        if result:
            user_data = None
            if 'response' in result:
                user_data = result['response']
            elif 'data' in result:
                user_data = result['data']
            else:
                user_data = result
                
            if user_data and user_data.get('shortUuid') and 'subscriptionUrl' not in user_data:
                try:
                    subscription_url = await self.get_subscription_url(user_data['shortUuid'])
                    user_data['subscriptionUrl'] = subscription_url
                except Exception as e:
                    logger.warning(f"Could not get subscription URL for user: {e}")
                    
            return user_data
        return None

    async def get_subscription_info(self, short_uuid: str) -> Optional[Dict]:
        try:
            logger.info(f"Getting subscription info for short_uuid: {short_uuid}")
            
            endpoints_to_try = [
                f'/api/subscriptions/{short_uuid}',
                f'/api/sub/{short_uuid}',
                f'/api/subscription/{short_uuid}'
            ]
            
            for endpoint in endpoints_to_try:
                logger.debug(f"Trying endpoint: {endpoint}")
                result = await self._make_request('GET', endpoint)
                
                if result:
                    logger.info(f"Successfully got subscription info from {endpoint}")
                    
                    subscription_data = None
                    
                    if 'response' in result:
                        subscription_data = result['response']
                    elif 'data' in result:
                        subscription_data = result['data']
                    elif 'subscription' in result:
                        subscription_data = result['subscription']
                    else:
                        subscription_data = result
                    
                    if subscription_data and (
                        'subscriptionUrl' in subscription_data or 
                        'url' in subscription_data or 
                        'link' in subscription_data
                    ):
                        return subscription_data
            
            logger.warning(f"Could not get subscription info for {short_uuid} from any endpoint")
            return None
            
        except Exception as e:
            logger.error(f"Error getting subscription info for {short_uuid}: {e}")
            return None

    async def get_subscription_url(self, short_uuid: str) -> str:
        try:
            logger.info(f"Getting subscription URL for short_uuid: {short_uuid}")
            
            subscription_info = await self.get_subscription_info(short_uuid)
            
            if subscription_info:
                subscription_url = (
                    subscription_info.get('subscriptionUrl') or
                    subscription_info.get('url') or
                    subscription_info.get('link') or
                    subscription_info.get('subscription_url')
                )
                
                if subscription_url:
                    logger.info(f"Got subscription URL from API: {subscription_url}")
                    return subscription_url
            
            user_data = await self.get_user_by_short_uuid(short_uuid)
            if user_data and 'subscriptionUrl' in user_data:
                logger.info(f"Got subscription URL from user data: {user_data['subscriptionUrl']}")
                return user_data['subscriptionUrl']
            
            if self.subscription_base_url:
                fallback_url = f"{self.subscription_base_url.rstrip('/')}/sub/{short_uuid}"
                logger.warning(f"Using fallback URL: {fallback_url}")
                return fallback_url
            else:
                fallback_url = f"{self.base_url.rstrip('/')}/sub/{short_uuid}"
                logger.warning(f"Using base_url fallback: {fallback_url}")
                return fallback_url
                
        except Exception as e:
            logger.error(f"Failed to get subscription URL for {short_uuid}: {e}")
            fallback_url = f"{self.base_url.rstrip('/')}/sub/{short_uuid}"
            return fallback_url

    async def get_user_by_short_uuid(self, short_uuid: str) -> Optional[Dict]:
        logger.debug(f"Getting user by short UUID: {short_uuid}")
        result = await self._make_request('GET', f'/api/users/by-short-uuid/{short_uuid}')
        
        if result:
            user_data = None
            if 'response' in result:
                user_data = result['response']
            elif 'data' in result:
                user_data = result['data']
            else:
                user_data = result
                
            if user_data and 'subscriptionUrl' not in user_data:
                try:
                    subscription_url = await self.get_subscription_url(short_uuid)
                    user_data['subscriptionUrl'] = subscription_url
                except Exception as e:
                    logger.warning(f"Could not get subscription URL: {e}")
                    
            return user_data
        return None

    async def get_all_subscriptions_with_urls(self) -> Optional[List]:
        try:
            logger.info("Fetching all subscriptions with URLs from API")
            result = await self._make_request('GET', '/api/subscriptions')
            
            if not result:
                logger.error("Empty response from subscriptions API")
                return []
            
            subscriptions_list = []
            
            if 'response' in result and 'subscriptions' in result['response']:
                subscriptions_list = result['response']['subscriptions']
            elif 'subscriptions' in result:
                subscriptions_list = result['subscriptions']
            elif 'data' in result:
                subscriptions_list = result['data']
            elif isinstance(result, list):
                subscriptions_list = result
            
            processed_subscriptions = []
            for subscription in subscriptions_list:
                if subscription.get('isFound') and 'user' in subscription:
                    user_data = subscription['user']
                    
                    if 'subscriptionUrl' not in subscription and user_data.get('shortUuid'):
                        subscription['subscriptionUrl'] = await self.get_subscription_url(user_data['shortUuid'])
                    
                    processed_subscriptions.append(subscription)
            
            logger.info(f"Processed {len(processed_subscriptions)} subscriptions with URLs")
            return processed_subscriptions
                
        except Exception as e:
            logger.error(f"Exception in get_all_subscriptions_with_urls: {e}", exc_info=True)
            return []
    
    async def update_user(self, uuid: str, data: Dict) -> Optional[Dict]:
        update_data = {'uuid': uuid}
        
        if 'enable' in data:
            update_data['status'] = 'ACTIVE' if data['enable'] else 'DISABLED'
        if 'expireAt' in data:
            update_data['expireAt'] = data['expireAt']
        if 'expiryTime' in data:
            update_data['expireAt'] = data['expiryTime']
        if 'trafficLimitBytes' in data:
            update_data['trafficLimitBytes'] = data['trafficLimitBytes']
        if 'status' in data:
            update_data['status'] = data['status']
            
        for key in ['activeInternalSquads', 'telegramId', 'email']:
            if key in data:
                update_data[key] = data[key]
        
        logger.debug(f"Updating user {uuid} with data: {update_data}")
        result = await self._make_request('PATCH', '/api/users', update_data)
        logger.debug(f"Update user result: {result}")
        return result
    
    async def update_user_expiry(self, short_uuid: str, new_expiry: str) -> Optional[Dict]:
        try:
            user_data = await self.get_user_by_short_uuid(short_uuid)
            if not user_data:
                logger.error(f"Could not find user with short UUID: {short_uuid}")
                return None
        
            user_uuid = user_data.get('uuid')
            if not user_uuid:
                logger.error(f"Could not get UUID from user data")
                return None
        
            update_data = {
                'status': 'ACTIVE',
                'expireAt': new_expiry
            }
        
            logger.info(f"Updating user {user_uuid} expiry to {new_expiry}")
            return await self.update_user(user_uuid, update_data)
        
        except Exception as e:
            logger.error(f"Exception in update_user_expiry: {e}")
            return None
    
    async def update_user_traffic_limit(self, uuid: str, traffic_limit_gb: int) -> Optional[Dict]:
        traffic_bytes = traffic_limit_gb * 1024 * 1024 * 1024 if traffic_limit_gb > 0 else 0
        update_data = {
            'trafficLimitBytes': traffic_bytes
        }
        return await self.update_user(uuid, update_data)

    async def get_all_nodes(self) -> Optional[List]:
        try:
            logger.debug("Requesting nodes from /api/nodes")
            result = await self._make_request('GET', '/api/nodes')
        
            if not result:
                logger.error("Empty response from nodes API")
                return []
        
            nodes_list = []
        
            if 'response' in result:
                nodes_list = result['response'] if isinstance(result['response'], list) else []
            elif 'data' in result:
                nodes_list = result['data'] if isinstance(result['data'], list) else []
            elif isinstance(result, list):
                nodes_list = result
        
            processed_nodes = []
            for node in nodes_list:
                processed_node = {
                    'id': node.get('uuid', node.get('id')),
                    'uuid': node.get('uuid', node.get('id')),
                    'name': node.get('name', 'Unknown Node'),
                    'address': node.get('address', node.get('url', '')),
                    'status': self._determine_node_status(node),
                    'isConnected': node.get('isConnected', False),
                    'isDisabled': node.get('isDisabled', True),
                    'isNodeOnline': node.get('isNodeOnline', False),
                    'isXrayRunning': node.get('isXrayRunning', False),
                    'cpuUsage': node.get('cpuUsage', 0),
                    'memUsage': node.get('memUsage', 0),
                    'usersCount': node.get('usersOnline', node.get('usersCount', 0)),
                    'xrayVersion': node.get('xrayVersion'),
                    'nodeVersion': node.get('nodeVersion'),
                    'xrayUptime': node.get('xrayUptime'),
                    'cpuModel': node.get('cpuModel'),
                    'totalRam': node.get('totalRam'),
                    'trafficUsedBytes': node.get('trafficUsedBytes', 0),
                    'viewPosition': node.get('viewPosition'),
                    'countryCode': node.get('countryCode'),
                    'region': node.get('region'),
                    'city': node.get('city'),
                    'provider': node.get('provider'),
                }
                processed_nodes.append(processed_node)
        
            logger.info(f"Processed {len(processed_nodes)} nodes with enhanced info")
            return processed_nodes
            
        except Exception as e:
            logger.error(f"Exception in get_all_nodes: {e}", exc_info=True)
            return []
    
    def _determine_node_status(self, node: Dict) -> str:
        is_connected = node.get('isConnected', False)
        is_disabled = node.get('isDisabled', False)
        is_node_online = node.get('isNodeOnline', False)
        is_xray_running = node.get('isXrayRunning', False)
    
        logger.debug(f"Node {node.get('name', 'unknown')}: connected={is_connected}, disabled={is_disabled}, online={is_node_online}, xray={is_xray_running}")
    
        if is_disabled:
            return 'disabled'
        elif not is_connected:
            return 'disconnected'
        elif is_connected and is_node_online and is_xray_running:
            return 'online'
        elif is_connected and is_node_online and not is_xray_running:
           return 'xray_stopped'
        else:
            return 'offline'
    
    async def restart_node(self, node_id: str) -> Optional[Dict]:
        try:
            logger.info(f"Attempting to restart node: {node_id}")
            result = await self._make_request('POST', f'/api/nodes/{node_id}/actions/restart')
            
            if result:
                logger.info(f"Successfully sent restart command for node {node_id}")
                return result
            
            logger.error(f"Failed to restart node {node_id}")
            return None
                
        except Exception as e:
            logger.error(f"Error restarting node {node_id}: {e}")
            return None
    
    async def restart_all_nodes(self) -> Optional[Dict]:
        try:
            logger.info("Attempting to restart all nodes")
            
            nodes = await self.get_all_nodes()
            if not nodes:
                return None
            
            success_count = 0
            for node in nodes:
                node_id = node.get('uuid', node.get('id'))
                if node_id:
                    result = await self.restart_node(node_id)
                    if result:
                        success_count += 1
                    await asyncio.sleep(0.5) 
            
            return {
                'success': success_count > 0,
                'restarted_nodes': success_count,
                'total_nodes': len(nodes)
            }
                
        except Exception as e:
            logger.error(f"Error restarting all nodes: {e}")
            return None
    
    async def enable_node(self, node_id: str) -> Optional[Dict]:
        return await self._make_request('POST', f'/api/nodes/{node_id}/actions/enable')
    
    async def disable_node(self, node_id: str) -> Optional[Dict]:
        return await self._make_request('POST', f'/api/nodes/{node_id}/actions/disable')

    async def get_system_stats(self) -> Optional[Dict]:
        try:
            logger.info("Fetching enhanced system stats from /api/system/stats...")
        
            system_result = await self._make_request('GET', '/api/system/stats')
        
            if not system_result or 'response' not in system_result:
                logger.error("Invalid response from /api/system/stats")
                return None
        
            system_data = system_result['response']
            logger.info(f"System stats received: {list(system_data.keys())}")
            users_stats = system_data.get('users', {})
            status_counts = users_stats.get('statusCounts', {})
            total_users = users_stats.get('totalUsers', 0)
            active_users = status_counts.get('ACTIVE', 0)
            disabled_users = status_counts.get('DISABLED', 0)
            limited_users = status_counts.get('LIMITED', 0)
            expired_users = status_counts.get('EXPIRED', 0)
            online_stats = system_data.get('onlineStats', {})
            online_now = online_stats.get('onlineNow', 0)
            last_day = online_stats.get('lastDay', 0)
            last_week = online_stats.get('lastWeek', 0)
            never_online = online_stats.get('neverOnline', 0)
            cpu_info = system_data.get('cpu', {})
            memory_info = system_data.get('memory', {})
        
            def bytes_to_gb(bytes_value):
                return round(bytes_value / (1024**3), 2) if bytes_value else 0
        
            memory_total_gb = bytes_to_gb(memory_info.get('total', 0))
            memory_active_gb = bytes_to_gb(memory_info.get('active', 0))
            memory_available_gb = bytes_to_gb(memory_info.get('available', 0))
            memory_free_gb = bytes_to_gb(memory_info.get('free', 0))
            all_nodes = await self.get_all_nodes()
            total_nodes = len(all_nodes) if all_nodes else 0
        
            nodes_online = 0
            if all_nodes:
                nodes_online = len([n for n in all_nodes if n.get('status') == 'online'])
        
            if total_nodes == 0:
                nodes_info = system_data.get('nodes', {})
                nodes_online = nodes_info.get('totalOnline', 0)
                total_nodes = nodes_online
        
            bandwidth_result = await self._make_request('GET', '/api/system/stats/bandwidth')
            enhanced_bandwidth = {}
            if bandwidth_result and 'response' in bandwidth_result:
                enhanced_bandwidth = bandwidth_result['response']
                logger.info(f"Enhanced bandwidth stats received")
        
            stats = {
                'users': active_users,
                'active_users': active_users,
                'total_users': total_users,
                'disabled_users': disabled_users,
                'limited_users': limited_users,
                'expired_users': expired_users,
                'online_stats': {
                    'online_now': online_now,
                    'last_day': last_day,
                    'last_week': last_week,
                    'never_online': never_online
                },
                'system_resources': {
                    'cpu': {
                        'cores': cpu_info.get('cores', 0),
                        'physical_cores': cpu_info.get('physicalCores', 0)
                    },
                    'memory': {
                        'total_gb': memory_total_gb,
                        'active_gb': memory_active_gb, 
                        'available_gb': memory_available_gb,
                        'free_gb': memory_free_gb,
                        'usage_percent': round((memory_active_gb / memory_total_gb * 100), 1) if memory_total_gb > 0 else 0
                    },
                    'uptime': system_data.get('uptime', 0)
                },
                'nodes': {
                    'total': total_nodes,
                    'online': nodes_online,
                    'offline': total_nodes - nodes_online
                },
                'bandwidth': enhanced_bandwidth,
                'total_traffic_bytes': users_stats.get('totalTrafficBytes', '0')
            }
        
            logger.info(f"Enhanced system stats compiled successfully")
            logger.info(f"Users: {total_users} total, {active_users} active, {online_now} online now")
            logger.info(f"Memory: {memory_active_gb}/{memory_total_gb} GB active, {memory_available_gb} GB available")
            logger.info(f"Nodes: {nodes_online}/{total_nodes} online")
        
            return stats
        
        except Exception as e:
            logger.error(f"Error getting enhanced system stats: {e}", exc_info=True)
            return None

    async def get_bandwidth_stats(self) -> Optional[Dict]:
        try:
            logger.info("Fetching detailed bandwidth statistics")
            result = await self._make_request('GET', '/api/system/stats/bandwidth')
        
            if result and 'response' in result:
                return result['response']
        
            return None
        
        except Exception as e:
            logger.error(f"Error getting bandwidth stats: {e}")
            return None

    async def get_node_detailed_info(self, node_id: str) -> Optional[Dict]:
        try:
            logger.info(f"Getting detailed info for node: {node_id}")
            result = await self._make_request('GET', f'/api/nodes/{node_id}')
        
            if result:
                node_data = None
                if 'response' in result:
                    node_data = result['response']
                elif 'data' in result:
                    node_data = result['data']
                else:
                    node_data = result
            
                if node_data:
                    enhanced_node = {
                        'id': node_data.get('uuid', node_data.get('id')),
                        'uuid': node_data.get('uuid', node_data.get('id')),
                        'name': node_data.get('name', 'Unknown Node'),
                        'address': node_data.get('address', ''),
                        'status': self._determine_node_status(node_data),
                        'isConnected': node_data.get('isConnected', False),
                        'isDisabled': node_data.get('isDisabled', True),
                        'isNodeOnline': node_data.get('isNodeOnline', False),
                        'isXrayRunning': node_data.get('isXrayRunning', False),
                        'cpuUsage': node_data.get('cpuUsage', 0),
                        'memUsage': node_data.get('memUsage', 0),
                        'usersCount': node_data.get('usersOnline', node_data.get('usersCount', 0)),
                        'xrayVersion': node_data.get('xrayVersion'),
                        'nodeVersion': node_data.get('nodeVersion'),
                        'xrayUptime': node_data.get('xrayUptime'),
                        'cpuModel': node_data.get('cpuModel'),
                        'totalRam': node_data.get('totalRam'),
                        'trafficUsedBytes': node_data.get('trafficUsedBytes', 0),
                        'viewPosition': node_data.get('viewPosition'),
                        'countryCode': node_data.get('countryCode'),
                        'region': node_data.get('region'),
                        'city': node_data.get('city'),
                        'provider': node_data.get('provider'),
                    }
                
                    return enhanced_node
        
            return None
        
        except Exception as e:
            logger.error(f"Error getting detailed node info: {e}")
            return None

    async def get_all_system_users_full(self) -> Optional[List]:
        try:
            all_users = []
            offset = 0
            limit = 100
        
            logger.info("Starting to fetch all system users with URLs")
        
            while True:
                logger.debug(f"Fetching users batch: offset={offset}, limit={limit}")
                result = await self._make_request('GET', '/api/users', 
                                                params={'offset': offset, 'limit': limit})
            
                if not result:
                    logger.warning(f"Empty result at offset {offset}")
                    break
            
                logger.debug(f"Raw API response structure: {list(result.keys()) if isinstance(result, dict) else 'not dict'}")
            
                batch_users = []
                total_count = None
            
                if isinstance(result, dict):
                    if 'total' in result:
                        total_count = result['total']
                        logger.info(f"Total users in system: {total_count}")
                
                    if 'users' in result:
                        batch_users = result['users'] if isinstance(result['users'], list) else []
                    elif 'data' in result:
                        batch_users = result['data'] if isinstance(result['data'], list) else []
                    elif 'response' in result:
                        if isinstance(result['response'], dict):
                            if 'users' in result['response']:
                                batch_users = result['response']['users'] if isinstance(result['response']['users'], list) else []
                            elif 'data' in result['response']:
                                batch_users = result['response']['data'] if isinstance(result['response']['data'], list) else []
                        elif isinstance(result['response'], list):
                            batch_users = result['response']
                    elif 'items' in result:
                        batch_users = result['items'] if isinstance(result['items'], list) else []
                elif isinstance(result, list):
                    batch_users = result
            
                logger.info(f"Found {len(batch_users)} users in batch at offset {offset}")
            
                if not batch_users:
                    logger.info(f"No users in batch at offset {offset}, stopping")
                    break
                
                enriched_users = []
                for user in batch_users:
                    try:
                        if user.get('shortUuid') and 'subscriptionUrl' not in user:
                            subscription_url = await self.get_subscription_url(user['shortUuid'])
                            user['subscriptionUrl'] = subscription_url
                            logger.debug(f"Added subscription URL for user {user.get('username', 'unknown')}")
                        
                        enriched_users.append(user)
                    except Exception as e:
                        logger.warning(f"Could not enrich user {user.get('username', 'unknown')} with URL: {e}")
                        enriched_users.append(user)
                
                all_users.extend(enriched_users)
            
                if len(batch_users) < limit:
                    logger.info(f"Last batch (got {len(batch_users)} < {limit})")
                    break
            
                if total_count and len(all_users) >= total_count:
                    logger.info(f"Reached total count: {total_count}")
                    break
            
                offset += limit
            
                if offset > 10000:
                    logger.warning("Offset limit reached, stopping")
                    break
        
            if all_users:
                active_users = len([u for u in all_users if str(u.get('status', '')).upper() == 'ACTIVE'])
                users_with_urls = len([u for u in all_users if u.get('subscriptionUrl')])
                logger.info(f"Successfully fetched {len(all_users)} users (Active: {active_users}, With URLs: {users_with_urls})")
            else:
                logger.warning("No users found in system")
        
            return all_users
        
        except Exception as e:
            logger.error(f"Error getting all system users: {e}", exc_info=True)
            return []
    
    async def get_internal_squads_list(self) -> Optional[List[Dict]]:
        logger.info("Fetching internal squads list")
        result = await self._make_request('GET', '/api/internal-squads')
        
        if result:
            if 'response' in result:
                if 'internalSquads' in result['response']:
                    return result['response']['internalSquads']
                return result['response'] if isinstance(result['response'], list) else []
            elif 'data' in result:
                return result['data'] if isinstance(result['data'], list) else []
            elif isinstance(result, list):
                return result
        return []

    async def get_users_count(self) -> Optional[int]:
        try:
            result = await self._make_request('GET', '/api/users', params={'limit': 1})
        
            if result:
                if 'total' in result:
                    return result['total']
                elif 'totalCount' in result:
                    return result['totalCount']
                elif 'count' in result:
                    return result['count']
        
            all_users = await self.get_all_system_users_full()
            return len(all_users) if all_users else 0
        
        except Exception as e:
            logger.error(f"Error getting users count: {e}")
            return 0

    async def debug_users_api(self) -> Dict:
        try:
            logger.info("=== DEBUGGING USERS API ===")
        
            result = await self._make_request('GET', '/api/users', params={'limit': 1, 'offset': 0})
        
            debug_info = {
                'api_response_type': type(result).__name__,
                'api_response_keys': list(result.keys()) if isinstance(result, dict) else None,
                'has_users': False,
                'users_location': None,
                'first_user_structure': None,
                'total_count': None
            }
        
            if isinstance(result, dict):
                if 'users' in result:
                    debug_info['users_location'] = 'root.users'
                    debug_info['has_users'] = True
                    if isinstance(result['users'], list) and result['users']:
                        debug_info['first_user_structure'] = list(result['users'][0].keys())
                elif 'data' in result:
                    debug_info['users_location'] = 'root.data'
                    debug_info['has_users'] = True
                    if isinstance(result['data'], list) and result['data']:
                        debug_info['first_user_structure'] = list(result['data'][0].keys())
                elif 'response' in result:
                    if isinstance(result['response'], dict):
                        if 'users' in result['response']:
                            debug_info['users_location'] = 'root.response.users'
                            debug_info['has_users'] = True
                            if isinstance(result['response']['users'], list) and result['response']['users']:
                                debug_info['first_user_structure'] = list(result['response']['users'][0].keys())
                        elif 'data' in result['response']:
                            debug_info['users_location'] = 'root.response.data'
                            debug_info['has_users'] = True
                            if isinstance(result['response']['data'], list) and result['response']['data']:
                                debug_info['first_user_structure'] = list(result['response']['data'][0].keys())
                    elif isinstance(result['response'], list):
                        debug_info['users_location'] = 'root.response (list)'
                        debug_info['has_users'] = True
                        if result['response']:
                            debug_info['first_user_structure'] = list(result['response'][0].keys())
            
                for key in ['total', 'totalCount', 'count', 'totalUsers']:
                    if key in result:
                        debug_info['total_count'] = result[key]
                        debug_info['total_count_field'] = key
                        break
            elif isinstance(result, list):
                debug_info['users_location'] = 'root (list)'
                debug_info['has_users'] = True
                if result:
                    debug_info['first_user_structure'] = list(result[0].keys())
        
            logger.info(f"Debug info: {debug_info}")
            return debug_info
        
        except Exception as e:
            logger.error(f"Error in debug_users_api: {e}")
            return {'error': str(e)}
    
    async def get_user_by_username(self, username: str) -> Optional[Dict]:
        result = await self._make_request('GET', f'/api/users/by-username/{username}')
        if result:
            user_data = None
            if 'response' in result:
                user_data = result['response']
            elif 'data' in result:
                user_data = result['data']
            else:
                user_data = result
                
            if user_data and user_data.get('shortUuid') and 'subscriptionUrl' not in user_data:
                try:
                    subscription_url = await self.get_subscription_url(user_data['shortUuid'])
                    user_data['subscriptionUrl'] = subscription_url
                except Exception as e:
                    logger.warning(f"Could not get subscription URL: {e}")
                    
            return user_data
        return None
    
    async def get_user_by_email(self, email: str) -> Optional[Dict]:
        result = await self._make_request('GET', f'/api/users/by-email/{email}')
        if result:
            user_data = None
            if 'response' in result:
                user_data = result['response']
            elif 'data' in result:
                user_data = result['data']
            else:
                user_data = result
                
            if user_data and user_data.get('shortUuid') and 'subscriptionUrl' not in user_data:
                try:
                    subscription_url = await self.get_subscription_url(user_data['shortUuid'])
                    user_data['subscriptionUrl'] = subscription_url
                except Exception as e:
                    logger.warning(f"Could not get subscription URL: {e}")
                    
            return user_data
        return None
    
    async def get_user_by_tag(self, tag: str) -> Optional[Dict]:
        result = await self._make_request('GET', f'/api/users/by-tag/{tag}')
        if result:
            user_data = None
            if 'response' in result:
                user_data = result['response']
            elif 'data' in result:
                user_data = result['data']
            else:
                user_data = result
                
            if user_data and user_data.get('shortUuid') and 'subscriptionUrl' not in user_data:
                try:
                    subscription_url = await self.get_subscription_url(user_data['shortUuid'])
                    user_data['subscriptionUrl'] = subscription_url
                except Exception as e:
                    logger.warning(f"Could not get subscription URL: {e}")
                    
            return user_data
        return None

    async def disable_user(self, uuid: str) -> Optional[Dict]:
        result = await self._make_request('POST', f'/api/users/{uuid}/actions/disable')
        if not result:
            return await self.update_user(uuid, {'status': 'DISABLED'})
        return result
    
    async def enable_user(self, uuid: str) -> Optional[Dict]:
        result = await self._make_request('POST', f'/api/users/{uuid}/actions/enable')
        if not result:
            return await self.update_user(uuid, {'status': 'ACTIVE'})
        return result
    
    async def reset_user_traffic(self, uuid: str) -> Optional[Dict]:
        return await self._make_request('POST', f'/api/users/{uuid}/actions/reset-traffic')
    
    async def revoke_user_subscription(self, uuid: str) -> Optional[Dict]:
        return await self._make_request('POST', f'/api/users/{uuid}/actions/revoke')

    async def bulk_reset_traffic(self, user_uuids: List[str]) -> Optional[Dict]:
        data = {'uuids': user_uuids}
        return await self._make_request('POST', '/api/users/bulk/reset-traffic', data)
    
    async def bulk_update_users(self, user_uuids: List[str], fields: Dict) -> Optional[Dict]:
        data = {
            'uuids': user_uuids,
            'fields': fields
        }
        return await self._make_request('POST', '/api/users/bulk/update', data)
    
    async def bulk_delete_users(self, user_uuids: List[str]) -> Optional[Dict]:
        data = {'uuids': user_uuids}
        return await self._make_request('POST', '/api/users/bulk/delete', data)
    
    async def debug_api_response(self, endpoint: str, method: str = 'GET', data: Optional[Dict] = None) -> Dict:
        try:
            if not endpoint.startswith('/api/'):
                endpoint = '/api' + endpoint if not endpoint.startswith('/') else '/api/' + endpoint
                
            url = f"{self.base_url}{endpoint}"
            session = await self._get_session()
        
            logger.info(f"DEBUG: Making {method} request to {url}")
            if data:
                logger.info(f"DEBUG: Request data: {data}")
        
            async with session.request(method, url, json=data) as response:
                response_text = await response.text()
                headers = dict(response.headers)
                content_type = headers.get('Content-Type', '')
            
                debug_info = {
                    'status': response.status,
                    'headers': headers,
                    'content_type': content_type,
                    'raw_text': response_text[:500] + '...' if len(response_text) > 500 else response_text,
                    'url': url,
                    'method': method,
                    'success': response.status in [200, 201] and 'application/json' in content_type
                }
            
                logger.info(f"DEBUG API Response: Status={response.status}, Content-Type={content_type}, Length={len(response_text)}")
            
                if 'application/json' in content_type:
                    try:
                        json_data = json.loads(response_text) if response_text else None
                        debug_info['json'] = json_data
                        debug_info['parsed_successfully'] = True
                        
                        if isinstance(json_data, dict):
                            debug_info['response_keys'] = list(json_data.keys())
                            if 'data' in json_data:
                                debug_info['data_type'] = type(json_data['data']).__name__
                                if isinstance(json_data['data'], list):
                                    debug_info['data_count'] = len(json_data['data'])
                        elif isinstance(json_data, list):
                            debug_info['data_type'] = 'list'
                            debug_info['data_count'] = len(json_data)
                        
                    except Exception as parse_error:
                        debug_info['json'] = None
                        debug_info['parsed_successfully'] = False
                        debug_info['parse_error'] = str(parse_error)
                else:
                    debug_info['error'] = f"Wrong content type: {content_type}"
                    debug_info['parsed_successfully'] = False
                    
                return debug_info
                
        except Exception as e:
            logger.error(f"DEBUG API Error: {e}")
            return {
                'error': str(e),
                'success': False,
                'url': f"{self.base_url}{endpoint}",
                'method': method
            }

    async def get_system_health(self) -> Optional[Dict]:
        try:
            nodes = await self.get_all_nodes()
            if not nodes:
                return {
                    'status': 'error',
                    'nodes_online': 0,
                    'nodes_total': 0,
                    'message': 'No nodes data available'
                }
            
            online_nodes = len([n for n in nodes if n.get('status') == 'online'])
            total_nodes = len(nodes)
            
            if total_nodes == 0:
                status = 'no_nodes'
            elif online_nodes == 0:
                status = 'critical'
            elif online_nodes < total_nodes / 2:
                status = 'warning'  
            else:
                status = 'healthy'
            
            return {
                'status': status,
                'nodes_online': online_nodes,
                'nodes_total': total_nodes
            }
        except Exception as e:
            logger.error(f"Error getting system health: {e}")
            return {
                'status': 'error',
                'nodes_online': 0,
                'nodes_total': 0,
                'message': str(e)
            }

    async def get_nodes_statistics(self) -> Optional[Dict]:
        try:
            nodes = await self.get_all_nodes()
            return {'data': nodes if nodes else []}
        except Exception as e:
            logger.error(f"Error getting nodes statistics: {e}")
            return {'data': []}

    async def get_all_subscriptions(self) -> Optional[List]:
        try:
            logger.info("Fetching all subscriptions from API")
            result = await self._make_request('GET', '/api/subscriptions')
            
            if not result:
                logger.error("Empty response from subscriptions API")
                return []
            
            subscriptions_list = []
            
            if 'response' in result and 'subscriptions' in result['response']:
                subscriptions_list = result['response']['subscriptions']
            elif 'subscriptions' in result:
                subscriptions_list = result['subscriptions']
            elif 'data' in result:
                subscriptions_list = result['data']
            elif isinstance(result, list):
                subscriptions_list = result
            
            processed_users = []
            for subscription in subscriptions_list:
                if subscription.get('isFound') and 'user' in subscription:
                    user_data = subscription['user']
                    
                    processed_user = {
                        'shortUuid': user_data.get('shortUuid'),
                        'username': user_data.get('username'),
                        'expireAt': user_data.get('expiresAt'),
                        'isActive': user_data.get('isActive', False),
                        'status': user_data.get('userStatus', 'UNKNOWN'),
                        'trafficUsed': user_data.get('trafficUsed', '0'),
                        'trafficLimit': user_data.get('trafficLimit', '0'),
                        'daysLeft': user_data.get('daysLeft', 0),
                        'subscriptionUrl': subscription.get('subscriptionUrl') or subscription.get('url'),
                        'links': subscription.get('links', [])
                    }
                    
                    if not processed_user.get('subscriptionUrl') and processed_user.get('shortUuid'):
                        try:
                            subscription_url = await self.get_subscription_url(processed_user['shortUuid'])
                            processed_user['subscriptionUrl'] = subscription_url
                        except Exception as e:
                            logger.warning(f"Could not get subscription URL for {processed_user.get('username')}: {e}")
                    
                    processed_users.append(processed_user)
            
            logger.info(f"Processed {len(processed_users)} active subscriptions")
            return processed_users
                
        except Exception as e:
            logger.error(f"Exception in get_all_subscriptions: {e}", exc_info=True)
            return []
    
    async def bulk_reset_all_traffic(self) -> Optional[Dict]:
        try:
            logger.info("Attempting bulk traffic reset for all users")
            
            all_users = await self.get_all_system_users_full()
            if not all_users:
                logger.warning("No users found for bulk traffic reset")
                return {'success': False, 'message': 'No users found'}
            
            user_uuids = []
            for user in all_users:
                user_uuid = user.get('uuid') or user.get('id') or user.get('shortUuid')
                if user_uuid:
                    user_uuids.append(user_uuid)
            
            if not user_uuids:
                logger.warning("No valid UUIDs found for bulk operation")
                return {'success': False, 'message': 'No valid user UUIDs'}
            
            logger.info(f"Resetting traffic for {len(user_uuids)} users")
            
            bulk_result = await self.bulk_reset_traffic(user_uuids)
            if bulk_result:
                return {'success': True, 'affected_users': len(user_uuids)}
            
            success_count = 0
            for user_uuid in user_uuids:
                try:
                    result = await self.reset_user_traffic(user_uuid)
                    if result:
                        success_count += 1
                except Exception as e:
                    logger.error(f"Failed to reset traffic for user {user_uuid}: {e}")
                
                await asyncio.sleep(0.1)
            
            return {
                'success': success_count > 0,
                'affected_users': success_count,
                'total_users': len(user_uuids)
            }
            
        except Exception as e:
            logger.error(f"Error in bulk_reset_all_traffic: {e}")
            return {'success': False, 'error': str(e)}
