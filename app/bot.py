import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.memory import MemoryStorage
import redis.asyncio as redis

from app.config import settings
from app.middlewares.auth import AuthMiddleware
from app.middlewares.logging import LoggingMiddleware
from app.middlewares.throttling import ThrottlingMiddleware
from app.middlewares.subscription_checker import SubscriptionStatusMiddleware
from app.middlewares.maintenance import MaintenanceMiddleware  # Новый middleware
from app.services.maintenance_service import maintenance_service  # Новый сервис
from app.utils.cache import cache 

from app.handlers import (
    start, menu, subscription, balance, promocode, 
    referral, support, common
)
from app.handlers.admin import (
    main as admin_main, users as admin_users, subscriptions as admin_subscriptions,
    promocodes as admin_promocodes, messages as admin_messages,
    monitoring as admin_monitoring, referrals as admin_referrals,
    rules as admin_rules, remnawave as admin_remnawave,
    statistics as admin_statistics, servers as admin_servers,
    maintenance as admin_maintenance  
)

logger = logging.getLogger(__name__)


async def debug_callback_handler(callback: types.CallbackQuery):
    logger.info(f"🔍 DEBUG CALLBACK:")
    logger.info(f"  - Data: {callback.data}")
    logger.info(f"  - User: {callback.from_user.id}")
    logger.info(f"  - Username: {callback.from_user.username}")


async def setup_bot() -> tuple[Bot, Dispatcher]:
    
    try:
        await cache.connect()
        logger.info("Кеш инициализирован")
    except Exception as e:
        logger.warning(f"Кеш не инициализирован: {e}")
    
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    bot = Bot(
        token=settings.BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    try:
        redis_client = redis.from_url(settings.REDIS_URL)
        await redis_client.ping()
        storage = RedisStorage(redis_client)
        logger.info("Подключено к Redis для FSM storage")
    except Exception as e:
        logger.warning(f"Не удалось подключиться к Redis: {e}")
        logger.info("Используется MemoryStorage для FSM")
        storage = MemoryStorage()
    
    dp = Dispatcher(storage=storage)
    
    # Порядок middleware важен!
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())
    
    # AuthMiddleware должен быть раньше MaintenanceMiddleware
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    
    # MaintenanceMiddleware проверяет режим техработ после авторизации
    dp.message.middleware(MaintenanceMiddleware())
    dp.callback_query.middleware(MaintenanceMiddleware())
    
    dp.message.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())
    dp.message.middleware(SubscriptionStatusMiddleware())
    dp.callback_query.middleware(SubscriptionStatusMiddleware())
    
    # Регистрация обработчиков
    start.register_handlers(dp)
    menu.register_handlers(dp)
    subscription.register_handlers(dp)
    balance.register_handlers(dp)
    promocode.register_handlers(dp)
    referral.register_handlers(dp)
    support.register_handlers(dp)
    
    # Админские обработчики
    admin_main.register_handlers(dp)
    admin_users.register_handlers(dp)
    admin_subscriptions.register_handlers(dp)
    admin_servers.register_handlers(dp)  
    admin_promocodes.register_handlers(dp)
    admin_messages.register_handlers(dp)
    admin_monitoring.register_handlers(dp)
    admin_referrals.register_handlers(dp)
    admin_rules.register_handlers(dp)
    admin_remnawave.register_handlers(dp)
    admin_statistics.register_handlers(dp)
    admin_maintenance.register_handlers(dp)  # Новые обработчики техработ

    common.register_handlers(dp)
    
    # Запуск мониторинга техработ
    try:
        await maintenance_service.start_monitoring()
        logger.info("Мониторинг техработ запущен")
    except Exception as e:
        logger.error(f"Ошибка запуска мониторинга техработ: {e}")
    
    logger.info("Бот успешно настроен")
    
    return bot, dp


async def shutdown_bot():
    """Корректное завершение работы бота"""
    try:
        await maintenance_service.stop_monitoring()
        logger.info("Мониторинг техработ остановлен")
    except Exception as e:
        logger.error(f"Ошибка остановки мониторинга: {e}")
    
    try:
        await cache.close()
        logger.info("Соединения с кешем закрыты")
    except Exception as e:
        logger.error(f"Ошибка закрытия кеша: {e}")
