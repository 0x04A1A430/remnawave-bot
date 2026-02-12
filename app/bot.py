import logging

import redis.asyncio as redis
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from app.config import settings
from app.handlers import (
    balance,
    common,
    contests as user_contests,
    menu,
    polls as user_polls,
    promocode,
    referral,
    server_status,
    simple_subscription,
    start,
    subscription,
    support,
    tickets,
)
from app.handlers.admin import (
    backup as admin_backup,
    blacklist as admin_blacklist,
    blocked_users as admin_blocked_users,
    bot_configuration as admin_bot_configuration,
    bulk_ban as admin_bulk_ban,
    campaigns as admin_campaigns,
    contests as admin_contests,
    daily_contests as admin_daily_contests,
    faq as admin_faq,
    main as admin_main,
    maintenance as admin_maintenance,
    messages as admin_messages,
    monitoring as admin_monitoring,
    payments as admin_payments,
    polls as admin_polls,
    pricing as admin_pricing,
    privacy_policy as admin_privacy_policy,
    promo_groups as admin_promo_groups,
    promo_offers as admin_promo_offers,
    promocodes as admin_promocodes,
    public_offer as admin_public_offer,
    referrals as admin_referrals,
    remnawave as admin_remnawave,
    reports as admin_reports,
    rules as admin_rules,
    servers as admin_servers,
    statistics as admin_statistics,
    subscriptions as admin_subscriptions,
    system_logs as admin_system_logs,
    tariffs as admin_tariffs,
    tickets as admin_tickets,
    trials as admin_trials,
    updates as admin_updates,
    user_messages as admin_user_messages,
    users as admin_users,
    welcome_text as admin_welcome_text,
)
from app.handlers.stars_payments import register_stars_handlers
from app.middlewares.auth import AuthMiddleware
from app.middlewares.blacklist import BlacklistMiddleware
from app.middlewares.button_stats import ButtonStatsMiddleware
from app.middlewares.global_error import GlobalErrorMiddleware
from app.middlewares.logging import LoggingMiddleware
from app.middlewares.maintenance import MaintenanceMiddleware
from app.middlewares.subscription_checker import SubscriptionStatusMiddleware
from app.middlewares.throttling import ThrottlingMiddleware
from app.services.maintenance_service import maintenance_service
from app.utils.cache import cache
from app.utils.message_patch import patch_message_methods


patch_message_methods()

logger = logging.getLogger(__name__)


async def debug_callback_handler(callback: types.CallbackQuery):
    logger.info('🔍 DEBUG CALLBACK:')
    logger.info(f'  - Data: {callback.data}')
    logger.info(f'  - User: {callback.from_user.id}')
    logger.info(f'  - Username: {callback.from_user.username}')


async def setup_bot() -> tuple[Bot, Dispatcher]:
    try:
        await cache.connect()
        logger.info('Кеш инициализирован')
    except Exception as e:
        logger.warning(f'Кеш не инициализирован: {e}')

    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    maintenance_service.set_bot(bot)
    logger.info('Бот установлен в maintenance_service')

    try:
        redis_client = redis.from_url(settings.REDIS_URL)
        await redis_client.ping()
        storage = RedisStorage(redis_client)
        logger.info('Подключено к Redis для FSM storage')
    except Exception as e:
        logger.warning(f'Не удалось подключиться к Redis: {e}')
        logger.info('Используется MemoryStorage для FSM')
        storage = MemoryStorage()

    dp = Dispatcher(storage=storage)

    dp.message.middleware(GlobalErrorMiddleware())
    dp.callback_query.middleware(GlobalErrorMiddleware())
    dp.pre_checkout_query.middleware(GlobalErrorMiddleware())
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())
    dp.message.middleware(MaintenanceMiddleware())
    dp.callback_query.middleware(MaintenanceMiddleware())
    blacklist_middleware = BlacklistMiddleware()
    dp.message.middleware(blacklist_middleware)
    dp.callback_query.middleware(blacklist_middleware)
    dp.pre_checkout_query.middleware(blacklist_middleware)
    dp.message.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())

    # Middleware для автоматического логирования кликов по кнопкам
    if settings.MENU_LAYOUT_ENABLED:
        button_stats_middleware = ButtonStatsMiddleware()
        dp.callback_query.middleware(button_stats_middleware)
        logger.info('📊 ButtonStatsMiddleware активирован')

    if settings.CHANNEL_IS_REQUIRED_SUB:
        from app.middlewares.channel_checker import ChannelCheckerMiddleware

        channel_checker_middleware = ChannelCheckerMiddleware()
        dp.message.middleware(channel_checker_middleware)
        dp.callback_query.middleware(channel_checker_middleware)
        logger.info('🔒 Обязательная подписка включена - ChannelCheckerMiddleware активирован')
    else:
        logger.info('🔓 Обязательная подписка отключена - ChannelCheckerMiddleware не зарегистрирован')
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.pre_checkout_query.middleware(AuthMiddleware())
    dp.message.middleware(SubscriptionStatusMiddleware())
    dp.callback_query.middleware(SubscriptionStatusMiddleware())
    start.register_handlers(dp)
    menu.register_handlers(dp)
    subscription.register_handlers(dp)
    balance.register_balance_handlers(dp)
    promocode.register_handlers(dp)
    referral.register_handlers(dp)
    support.register_handlers(dp)
    server_status.register_handlers(dp)
    tickets.register_handlers(dp)
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
    admin_polls.register_handlers(dp)
    admin_promo_groups.register_handlers(dp)
    admin_campaigns.register_handlers(dp)
    admin_contests.register_handlers(dp)
    admin_daily_contests.register_handlers(dp)
    admin_promo_offers.register_handlers(dp)
    admin_maintenance.register_handlers(dp)
    admin_user_messages.register_handlers(dp)
    admin_updates.register_handlers(dp)
    admin_backup.register_handlers(dp)
    admin_system_logs.register_handlers(dp)
    admin_welcome_text.register_welcome_text_handlers(dp)
    admin_tickets.register_handlers(dp)
    admin_reports.register_handlers(dp)
    admin_bot_configuration.register_handlers(dp)
    admin_pricing.register_handlers(dp)
    admin_privacy_policy.register_handlers(dp)
    admin_public_offer.register_handlers(dp)
    admin_faq.register_handlers(dp)
    admin_payments.register_handlers(dp)
    admin_trials.register_handlers(dp)
    admin_tariffs.register_handlers(dp)
    admin_bulk_ban.register_bulk_ban_handlers(dp)
    admin_blacklist.register_blacklist_handlers(dp)
    admin_blocked_users.register_handlers(dp)
    common.register_handlers(dp)
    register_stars_handlers(dp)
    user_contests.register_handlers(dp)
    user_polls.register_handlers(dp)
    simple_subscription.register_simple_subscription_handlers(dp)
    logger.info('⭐ Зарегистрированы обработчики Telegram Stars платежей')
    logger.info('⚡ Зарегистрированы обработчики простой покупки')
    logger.info('⚡ Зарегистрированы обработчики простой подписки')

    if settings.is_maintenance_monitoring_enabled():
        try:
            await maintenance_service.start_monitoring()
            logger.info('Мониторинг техработ запущен')
        except Exception as e:
            logger.error(f'Ошибка запуска мониторинга техработ: {e}')
    else:
        logger.info('Мониторинг техработ отключен настройками')

    logger.info('🛡️ GlobalErrorMiddleware активирован - бот защищен от устаревших callback queries')
    logger.info('Бот успешно настроен')

    return bot, dp


async def shutdown_bot():
    try:
        await maintenance_service.stop_monitoring()
        logger.info('Мониторинг техработ остановлен')
    except Exception as e:
        logger.error(f'Ошибка остановки мониторинга: {e}')

    try:
        await cache.close()
        logger.info('Соединения с кешем закрыты')
    except Exception as e:
        logger.error(f'Ошибка закрытия кеша: {e}')
