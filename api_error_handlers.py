import logging
from typing import Optional, Dict, Any, Callable
from aiogram.types import CallbackQuery
from aiogram import Router
from remnawave_api import RemnaWaveAPI
from translations import t

logger = logging.getLogger(__name__)

class APIErrorHandler:
    
    @staticmethod
    async def handle_api_error(callback: CallbackQuery, error: Exception, 
                             operation: str, user_language: str = 'ru',
                             fallback_keyboard=None) -> bool:
        error_message = str(error).lower()
        
        if "timeout" in error_message or "connection" in error_message:
            text = "⏱ Таймаут подключения к API\n\n"
            text += "Возможные причины:\n"
            text += "• Медленный интернет\n"
            text += "• Перегрузка сервера RemnaWave\n"
            text += "• Временные проблемы с сетью\n\n"
            text += "🔄 Попробуйте повторить операцию через несколько секунд"
            
        elif "401" in error_message or "unauthorized" in error_message:
            text = "🔐 Ошибка авторизации API\n\n"
            text += "Токен доступа недействителен или истек.\n"
            text += "Обратитесь к администратору для обновления токена."
            
        elif "404" in error_message or "not found" in error_message:
            text = f"❌ Ресурс не найден\n\n"
            text += f"Операция: {operation}\n"
            text += "Возможно, запрашиваемый объект был удален или не существует."
            
        elif "500" in error_message or "internal server error" in error_message:
            text = "🔥 Внутренняя ошибка сервера RemnaWave\n\n"
            text += "Сервер временно недоступен.\n"
            text += "Попробуйте повторить операцию позже."
            
        else:
            text = f"❌ Ошибка API операции: {operation}\n\n"
            text += f"Детали: {str(error)[:100]}{'...' if len(str(error)) > 100 else ''}\n\n"
            text += "Обратитесь к администратору если проблема повторяется."
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=fallback_keyboard or error_recovery_keyboard(operation, user_language)
            )
            return True
        except Exception as edit_error:
            logger.error(f"Failed to edit message with error info: {edit_error}")
            try:
                await callback.answer(f"❌ Ошибка: {operation}", show_alert=True)
                return True
            except:
                return False

    @staticmethod
    async def safe_api_call(api_method: Callable, *args, **kwargs) -> tuple[bool, Any]:
        try:
            result = await api_method(*args, **kwargs)
            return True, result
        except Exception as e:
            logger.error(f"API call failed: {api_method.__name__} - {e}")
            return False, str(e)

def create_error_recovery_keyboard(error_context: str, language: str = 'ru'):
    from keyboards import error_recovery_keyboard
    return error_recovery_keyboard(error_context, language)

async def safe_get_nodes(api: RemnaWaveAPI) -> tuple[bool, list]:
    try:
        logger.info("Attempting to fetch nodes from API...")
        nodes = await api.get_all_nodes()
        
        if nodes is None:
            logger.warning("API returned None for nodes")
            return False, []
        
        if not isinstance(nodes, list):
            logger.warning(f"API returned non-list for nodes: {type(nodes)}")
            return False, []
        
        logger.info(f"Successfully fetched {len(nodes)} nodes")
        return True, nodes
        
    except Exception as e:
        logger.error(f"Error fetching nodes: {e}")
        return False, []

async def safe_get_system_users(api: RemnaWaveAPI) -> tuple[bool, list]:
    try:
        logger.info("Attempting to fetch system users from API...")
        users = await api.get_all_system_users_full()
        
        if users is None:
            logger.warning("API returned None for users")
            return False, []
        
        if not isinstance(users, list):
            logger.warning(f"API returned non-list for users: {type(users)}")
            return False, []
        
        logger.info(f"Successfully fetched {len(users)} users")
        return True, users
        
    except Exception as e:
        logger.error(f"Error fetching system users: {e}")
        return False, []

async def safe_restart_nodes(api: RemnaWaveAPI, all_nodes: bool = True, node_id: str = None) -> tuple[bool, str]:
    try:
        if all_nodes:
            logger.info("Attempting to restart all nodes...")
            result = await api.restart_all_nodes()
        else:
            logger.info(f"Attempting to restart node {node_id}...")
            result = await api.restart_node(node_id)
        
        if result:
            message = "Команда перезагрузки отправлена успешно"
            logger.info(f"Restart command sent successfully")
            return True, message
        else:
            message = "API вернул отрицательный результат"
            logger.warning("API returned negative result for restart")
            return False, message
            
    except Exception as e:
        logger.error(f"Error restarting nodes: {e}")
        return False, str(e)

async def check_api_health(api: RemnaWaveAPI) -> Dict[str, Any]:
    health_info = {
        'api_available': False,
        'nodes_accessible': False,
        'users_accessible': False,
        'system_stats_accessible': False,
        'errors': []
    }
    
    if api is None:
        health_info['errors'].append("API instance is None")
        return health_info
    
    try:
        success, nodes = await safe_get_nodes(api)
        if success:
            health_info['api_available'] = True
            health_info['nodes_accessible'] = True
        else:
            health_info['errors'].append("Cannot fetch nodes")
    except Exception as e:
        health_info['errors'].append(f"Nodes check failed: {e}")
    
    try:
        success, users = await safe_get_system_users(api)
        if success:
            health_info['users_accessible'] = True
        else:
            health_info['errors'].append("Cannot fetch users")
    except Exception as e:
        health_info['errors'].append(f"Users check failed: {e}")
    
    try:
        stats = await api.get_system_stats()
        if stats:
            health_info['system_stats_accessible'] = True
        else:
            health_info['errors'].append("Cannot fetch system stats")
    except Exception as e:
        health_info['errors'].append(f"System stats check failed: {e}")
    
    return health_info

def handle_api_errors(operation_name: str):
    def decorator(func):
        async def wrapper(callback: CallbackQuery, user, *args, **kwargs):
            try:
                return await func(callback, user, *args, **kwargs)
            except Exception as e:
                logger.error(f"Error in {func.__name__}: {e}")
                
                api = kwargs.get('api')
                fallback_keyboard = None
                
                if 'nodes' in operation_name.lower():
                    from keyboards import admin_system_keyboard
                    fallback_keyboard = admin_system_keyboard(user.language)
                elif 'users' in operation_name.lower():
                    from keyboards import system_users_keyboard
                    fallback_keyboard = system_users_keyboard(user.language)
                
                await APIErrorHandler.handle_api_error(
                    callback, e, operation_name, user.language, fallback_keyboard
                )
        
        return wrapper
    return decorator
