"""
Subscription Monitor Service
Сервис для мониторинга подписок, уведомлений пользователей и предложений продления
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass
import traceback

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import Database, UserSubscription, Subscription, User
from remnawave_api import RemnaWaveAPI
from translations import t
from keyboards import extend_subscription_keyboard, main_menu_keyboard
from utils import format_datetime, log_user_action
from config import Config

# Настройка логирования
logger = logging.getLogger(__name__)

@dataclass
class NotificationResult:
    """Результат отправки уведомления"""
    success: bool
    user_id: int
    message: str
    error: Optional[str] = None

class SubscriptionMonitorService:
    """Сервис мониторинга подписок"""
    
    def __init__(self, bot: Bot, db: Database, config: Config, api: Optional[RemnaWaveAPI] = None):
        self.bot = bot
        self.db = db
        self.config = config
        self.api = api
        self.is_running = False
        self._monitor_task: Optional[asyncio.Task] = None
        
        # Настройки уведомлений
        self.WARNING_DAYS = 2  # За сколько дней предупреждать
        self.CHECK_INTERVAL = 3600  # Интервал проверки (в секундах) - каждый час
        self.DAILY_CHECK_HOUR = 10  # В какой час дня делать основную проверку
        
    async def start(self):
        """Запуск сервиса мониторинга"""
        if self.is_running:
            logger.warning("Subscription monitor service is already running")
            return
            
        self.is_running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Subscription monitor service started")
        
    async def stop(self):
        """Остановка сервиса мониторинга"""
        if not self.is_running:
            return
            
        self.is_running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
                
        logger.info("Subscription monitor service stopped")
        
    async def _monitor_loop(self):
        """Основной цикл мониторинга"""
        logger.info(f"Starting monitor loop with {self.CHECK_INTERVAL}s interval")
        
        while self.is_running:
            try:
                current_time = datetime.utcnow()
                
                # Основная проверка раз в день в определенное время
                if current_time.hour == self.DAILY_CHECK_HOUR:
                    await self._daily_check()
                
                # Дополнительная проверка каждый час для критических случаев
                await self._hourly_check()
                
                # Ожидание до следующей проверки
                await asyncio.sleep(self.CHECK_INTERVAL)
                
            except asyncio.CancelledError:
                logger.info("Monitor loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                logger.error(traceback.format_exc())
                # Продолжаем работу даже при ошибках
                await asyncio.sleep(60)  # Короткая пауза при ошибке
                
    async def _daily_check(self):
        """Ежедневная проверка всех подписок"""
        logger.info("Starting daily subscription check")
        
        try:
            # Получаем все активные подписки пользователей
            all_users = await self.db.get_all_users()
            total_notifications = 0
            
            for user in all_users:
                try:
                    user_subs = await self.db.get_user_subscriptions(user.telegram_id)
                    active_subs = [sub for sub in user_subs if sub.is_active]
                    
                    for user_sub in active_subs:
                        # Проверяем каждую подписку
                        notification_sent = await self._check_and_notify_subscription(user, user_sub)
                        if notification_sent:
                            total_notifications += 1
                            
                        # Небольшая пауза между уведомлениями
                        await asyncio.sleep(0.1)
                        
                except Exception as e:
                    logger.error(f"Error checking subscriptions for user {user.telegram_id}: {e}")
                    continue
                    
            logger.info(f"Daily check completed. Sent {total_notifications} notifications")
            
        except Exception as e:
            logger.error(f"Error in daily check: {e}")
            logger.error(traceback.format_exc())
            
    async def _hourly_check(self):
        """Часовая проверка критических подписок (истекают сегодня)"""
        try:
            now = datetime.utcnow()
            tomorrow = now + timedelta(days=1)
            
            # Получаем подписки, которые истекают в ближайшие 24 часа
            all_users = await self.db.get_all_users()
            
            for user in all_users:
                try:
                    user_subs = await self.db.get_user_subscriptions(user.telegram_id)
                    
                    for user_sub in user_subs:
                        if (user_sub.is_active and 
                            user_sub.expires_at <= tomorrow and 
                            user_sub.expires_at > now):
                            
                            await self._check_and_notify_subscription(user, user_sub, urgent=True)
                            await asyncio.sleep(0.1)
                            
                except Exception as e:
                    logger.error(f"Error in hourly check for user {user.telegram_id}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in hourly check: {e}")
            
    async def _check_and_notify_subscription(self, user: User, user_sub: UserSubscription, urgent: bool = False) -> bool:
        """
        Проверить подписку и отправить уведомление если нужно
        Returns: True если уведомление было отправлено
        """
        try:
            now = datetime.utcnow()
            days_until_expiry = (user_sub.expires_at - now).days
            hours_until_expiry = (user_sub.expires_at - now).total_seconds() / 3600
            
            # Получаем информацию о подписке
            subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
            if not subscription:
                logger.warning(f"Subscription {user_sub.subscription_id} not found")
                return False
            
            notification_type = None
            
            # Определяем тип уведомления
            if user_sub.expires_at <= now:
                # Подписка истекла
                notification_type = "expired"
            elif days_until_expiry <= 0 and hours_until_expiry <= 24:
                # Истекает сегодня
                notification_type = "expires_today"
            elif days_until_expiry == 1:
                # Истекает завтра
                notification_type = "expires_tomorrow"
            elif days_until_expiry == self.WARNING_DAYS:
                # Предупреждение за 2 дня
                notification_type = "warning"
            elif urgent and days_until_expiry <= 1:
                # Срочное уведомление
                notification_type = "urgent"
            
            if notification_type:
                return await self._send_notification(user, user_sub, subscription, notification_type)
                
            return False
            
        except Exception as e:
            logger.error(f"Error checking subscription {user_sub.id}: {e}")
            return False
            
    async def _send_notification(self, user: User, user_sub: UserSubscription, 
                               subscription: Subscription, notification_type: str) -> bool:
        """Отправить уведомление пользователю"""
        try:
            # Проверяем, не является ли подписка тестовой (для тестовых другая логика)
            if subscription.is_trial and notification_type in ["warning", "expires_tomorrow"]:
                # Для тестовых подписок не предлагаем продление
                return await self._send_trial_expiry_notification(user, user_sub, subscription, notification_type)
            
            # Формируем текст уведомления
            message_text = self._format_notification_message(user, user_sub, subscription, notification_type)
            
            # Формируем клавиатуру
            keyboard = self._create_notification_keyboard(user, user_sub, subscription, notification_type)
            
            # Отправляем уведомление
            await self.bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            
            # Логируем действие
            log_user_action(user.telegram_id, f"notification_sent_{notification_type}", f"Sub: {subscription.name}")
            
            logger.info(f"Sent {notification_type} notification to user {user.telegram_id} for subscription {subscription.name}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending notification to user {user.telegram_id}: {e}")
            return False
            
    async def _send_trial_expiry_notification(self, user: User, user_sub: UserSubscription, 
                                            subscription: Subscription, notification_type: str) -> bool:
        """Отправить уведомление об истечении тестовой подписки"""
        try:
            now = datetime.utcnow()
            days_until_expiry = (user_sub.expires_at - now).days
            hours_until_expiry = (user_sub.expires_at - now).total_seconds() / 3600
            
            if notification_type == "expires_today" or hours_until_expiry <= 24:
                message_text = (
                    f"⏰ *Ваша тестовая подписка истекает сегодня!*\n\n"
                    f"📋 Подписка: *{subscription.name}*\n"
                    f"⏳ Осталось: *{int(hours_until_expiry)} часов*\n\n"
                    f"💡 Чтобы продолжить пользоваться сервисом, приобретите полную подписку!"
                )
            elif notification_type == "expires_tomorrow" or days_until_expiry == 1:
                message_text = (
                    f"⚠️ *Ваша тестовая подписка истекает завтра!*\n\n"
                    f"📋 Подписка: *{subscription.name}*\n"
                    f"📅 Истекает: *{format_datetime(user_sub.expires_at, user.language)}*\n\n"
                    f"💡 Не забудьте приобрести полную подписку, чтобы продолжить пользоваться сервисом!"
                )
            else:
                return False
            
            # Клавиатура для тестовой подписки
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="💳 Купить подписку",
                    callback_data="buy_subscription"
                )],
                [InlineKeyboardButton(
                    text="💰 Пополнить баланс",
                    callback_data="topup_balance"
                )],
                [InlineKeyboardButton(
                    text="🏠 Главное меню",
                    callback_data="main_menu"
                )]
            ])
            
            await self.bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            
            log_user_action(user.telegram_id, f"trial_notification_sent_{notification_type}", f"Sub: {subscription.name}")
            logger.info(f"Sent trial {notification_type} notification to user {user.telegram_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending trial notification to user {user.telegram_id}: {e}")
            return False
            
    def _format_notification_message(self, user: User, user_sub: UserSubscription, 
                                   subscription: Subscription, notification_type: str) -> str:
        """Форматировать текст уведомления"""
        now = datetime.utcnow()
        days_until_expiry = (user_sub.expires_at - now).days
        hours_until_expiry = (user_sub.expires_at - now).total_seconds() / 3600
        
        base_info = (
            f"📋 Подписка: *{subscription.name}*\n"
            f"📅 Истекает: *{format_datetime(user_sub.expires_at, user.language)}*\n"
            f"💰 Цена продления: *{subscription.price} руб.*"
        )
        
        if notification_type == "expired":
            return (
                f"❌ *Ваша подписка истекла!*\n\n"
                f"{base_info}\n\n"
                f"🔄 Продлите подписку, чтобы продолжить пользоваться сервисом."
            )
        elif notification_type == "expires_today" or notification_type == "urgent":
            return (
                f"⏰ *Ваша подписка истекает сегодня!*\n\n"
                f"{base_info}\n"
                f"⏳ Осталось: *{int(hours_until_expiry)} часов*\n\n"
                f"🔄 Продлите подписку прямо сейчас!"
            )
        elif notification_type == "expires_tomorrow":
            return (
                f"⚠️ *Ваша подписка истекает завтра!*\n\n"
                f"{base_info}\n\n"
                f"🔄 Рекомендуем продлить подписку заранее."
            )
        elif notification_type == "warning":
            return (
                f"📢 *Напоминание о подписке*\n\n"
                f"{base_info}\n"
                f"⏳ Осталось: *{days_until_expiry} дней*\n\n"
                f"💡 Не забудьте продлить подписку вовремя!"
            )
        else:
            return f"🔔 Уведомление о подписке *{subscription.name}*"
            
    def _create_notification_keyboard(self, user: User, user_sub: UserSubscription, 
                                    subscription: Subscription, notification_type: str) -> InlineKeyboardMarkup:
        """Создать клавиатуру для уведомления"""
        buttons = []
        
        # Кнопка продления (только для не-тестовых подписок)
        if not subscription.is_trial:
            if user.balance >= subscription.price:
                buttons.append([InlineKeyboardButton(
                    text=f"🔄 Продлить за {subscription.price} руб.",
                    callback_data=f"extend_sub_{user_sub.id}"
                )])
            else:
                buttons.append([InlineKeyboardButton(
                    text=f"💰 Пополнить баланс (нужно {subscription.price - user.balance} руб.)",
                    callback_data="topup_balance"
                )])
        
        # Кнопка покупки новой подписки
        buttons.append([InlineKeyboardButton(
            text="💳 Купить подписку",
            callback_data="buy_subscription"
        )])
        
        # Кнопка "Мои подписки"
        buttons.append([InlineKeyboardButton(
            text="📋 Мои подписки",
            callback_data="my_subscriptions"
        )])
        
        # Кнопка главного меню
        buttons.append([InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data="main_menu"
        )])
        
        return InlineKeyboardMarkup(inline_keyboard=buttons)
        
    async def check_single_user(self, user_id: int) -> List[NotificationResult]:
        """Проверить подписки конкретного пользователя (для тестирования)"""
        results = []
        
        try:
            user = await self.db.get_user_by_telegram_id(user_id)
            if not user:
                return [NotificationResult(False, user_id, "User not found")]
                
            user_subs = await self.db.get_user_subscriptions(user_id)
            
            for user_sub in user_subs:
                if user_sub.is_active:
                    try:
                        sent = await self._check_and_notify_subscription(user, user_sub)
                        subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
                        sub_name = subscription.name if subscription else "Unknown"
                        
                        results.append(NotificationResult(
                            success=sent,
                            user_id=user_id,
                            message=f"Subscription: {sub_name}, Sent: {sent}"
                        ))
                    except Exception as e:
                        results.append(NotificationResult(
                            success=False,
                            user_id=user_id,
                            message=f"Error checking subscription {user_sub.id}",
                            error=str(e)
                        ))
                        
        except Exception as e:
            results.append(NotificationResult(
                success=False,
                user_id=user_id,
                message="Error checking user",
                error=str(e)
            ))
            
        return results
        
    async def get_service_status(self) -> dict:
        """Получить статус сервиса"""
        return {
            "is_running": self.is_running,
            "check_interval": self.CHECK_INTERVAL,
            "daily_check_hour": self.DAILY_CHECK_HOUR,
            "warning_days": self.WARNING_DAYS,
            "last_check": datetime.utcnow().isoformat() if self.is_running else None
        }
        
    async def force_daily_check(self):
        """Принудительно запустить ежедневную проверку"""
        logger.info("Force starting daily check")
        await self._daily_check()
        
    async def deactivate_expired_subscriptions(self):
        """Деактивировать истекшие подписки"""
        try:
            now = datetime.utcnow()
            all_users = await self.db.get_all_users()
            deactivated_count = 0
            
            for user in all_users:
                user_subs = await self.db.get_user_subscriptions(user.telegram_id)
                
                for user_sub in user_subs:
                    if user_sub.is_active and user_sub.expires_at <= now:
                        # Деактивируем подписку
                        user_sub.is_active = False
                        await self.db.update_user_subscription(user_sub)
                        
                        # Деактивируем в RemnaWave если API доступно
                        if self.api and user_sub.short_uuid:
                            try:
                                remna_user_details = await self.api.get_user_by_short_uuid(user_sub.short_uuid)
                                if remna_user_details:
                                    user_uuid = remna_user_details.get('uuid')
                                    if user_uuid:
                                        # Блокируем пользователя в RemnaWave
                                        await self.api.update_user(user_uuid, {"enable": False})
                                        logger.info(f"Disabled user {user_uuid} in RemnaWave")
                            except Exception as e:
                                logger.error(f"Failed to disable user in RemnaWave: {e}")
                        
                        deactivated_count += 1
                        log_user_action(user.telegram_id, "subscription_expired", f"SubID: {user_sub.id}")
                        
            logger.info(f"Deactivated {deactivated_count} expired subscriptions")
            return deactivated_count
            
        except Exception as e:
            logger.error(f"Error deactivating expired subscriptions: {e}")
            return 0


# Функция для инициализации и запуска сервиса
async def create_subscription_monitor(bot: Bot, db: Database, config: Config, 
                                    api: Optional[RemnaWaveAPI] = None) -> SubscriptionMonitorService:
    """Создать и настроить сервис мониторинга подписок"""
    service = SubscriptionMonitorService(bot, db, config, api)
    return service


# Пример использования в основном файле бота
"""
from subscription_monitor import create_subscription_monitor

async def main():
    # Инициализация бота, базы данных, конфига
    bot = Bot(token=config.BOT_TOKEN)
    db = Database(config.DATABASE_URL)
    api = RemnaWaveAPI(config.REMNAWAVE_API_URL, config.REMNAWAVE_API_KEY)
    
    # Создание и запуск сервиса мониторинга
    monitor_service = await create_subscription_monitor(bot, db, config, api)
    await monitor_service.start()
    
    try:
        # Запуск бота
        await dp.start_polling(bot)
    finally:
        # Остановка сервиса при завершении
        await monitor_service.stop()

if __name__ == "__main__":
    asyncio.run(main())
"""
