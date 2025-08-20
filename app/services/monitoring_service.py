import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.database import get_db
from app.database.crud.subscription import (
    get_expired_subscriptions, get_expiring_subscriptions,
    get_subscriptions_for_autopay, deactivate_subscription,
    extend_subscription
)
from app.database.crud.user import (
    get_user_by_id, get_inactive_users, delete_user,
    subtract_user_balance
)
from app.database.models import MonitoringLog, SubscriptionStatus, Subscription, User
from app.services.subscription_service import SubscriptionService
from app.services.payment_service import PaymentService
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


class MonitoringService:
    
    def __init__(self, bot=None):
        self.is_running = False
        self.subscription_service = SubscriptionService()
        self.payment_service = PaymentService()
        self.bot = bot
        self._notified_users: Set[str] = set()  # Защита от дублирования уведомлений
    
    async def start_monitoring(self):
        if self.is_running:
            logger.warning("Мониторинг уже запущен")
            return
        
        self.is_running = True
        logger.info("🔄 Запуск службы мониторинга")
        
        while self.is_running:
            try:
                await self._monitoring_cycle()
                await asyncio.sleep(settings.MONITORING_INTERVAL * 60) 
                
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
                await asyncio.sleep(60) 
    
    def stop_monitoring(self):
        self.is_running = False
        logger.info("ℹ️ Мониторинг остановлен")
    
    async def _monitoring_cycle(self):
        async for db in get_db():
            try:
                await self._check_expired_subscriptions(db)
                await self._check_expiring_subscriptions(db)
                await self._check_trial_expiring_soon(db)  # Новый метод!
                await self._process_autopayments(db)
                await self._cleanup_inactive_users(db)
                await self._sync_with_remnawave(db)
                
                # Очищаем кеш уведомлений каждые 24 часа
                current_hour = datetime.utcnow().hour
                if current_hour == 0:
                    self._notified_users.clear()
                
                await self._log_monitoring_event(
                    db, "monitoring_cycle_completed", 
                    "Цикл мониторинга успешно завершен", 
                    {"timestamp": datetime.utcnow().isoformat()}
                )
                
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
                await self._log_monitoring_event(
                    db, "monitoring_cycle_error", 
                    f"Ошибка в цикле мониторинга: {str(e)}", 
                    {"error": str(e)},
                    is_success=False
                )
            finally:
                break 
    
    async def _check_expired_subscriptions(self, db: AsyncSession):
        """Проверка истекших подписок"""
        try:
            expired_subscriptions = await get_expired_subscriptions(db)
            
            for subscription in expired_subscriptions:
                await deactivate_subscription(db, subscription)
                
                user = await get_user_by_id(db, subscription.user_id)
                if user and user.remnawave_uuid:
                    await self.subscription_service.disable_remnawave_user(user.remnawave_uuid)
                
                # Отправляем уведомление об истечении
                if user and self.bot:
                    await self._send_subscription_expired_notification(user)
                
                logger.info(f"🔴 Подписка пользователя {subscription.user_id} истекла и деактивирована")
            
            if expired_subscriptions:
                await self._log_monitoring_event(
                    db, "expired_subscriptions_processed",
                    f"Обработано {len(expired_subscriptions)} истекших подписок",
                    {"count": len(expired_subscriptions)}
                )
                
        except Exception as e:
            logger.error(f"Ошибка проверки истекших подписок: {e}")
    
    async def _check_expiring_subscriptions(self, db: AsyncSession):
        """Проверка подписок, истекающих через 2-3 дня (только платные)"""
        try:
            warning_days = settings.get_autopay_warning_days()
            
            for days in warning_days:
                # Получаем только платные подписки
                expiring_subscriptions = await self._get_expiring_paid_subscriptions(db, days)
                
                for subscription in expiring_subscriptions:
                    user = await get_user_by_id(db, subscription.user_id)
                    if not user:
                        continue
                    
                    notification_key = f"expiring_{user.telegram_id}_{days}d"
                    if notification_key in self._notified_users:
                        continue  # Уже уведомляли сегодня
                    
                    if self.bot:
                        await self._send_subscription_expiring_notification(user, subscription, days)
                        self._notified_users.add(notification_key)
                    
                    logger.info(f"⚠️ Пользователю {user.telegram_id} отправлено уведомление об истечении подписки через {days} дней")
                
                if expiring_subscriptions:
                    await self._log_monitoring_event(
                        db, "expiring_notifications_sent",
                        f"Отправлено {len(expiring_subscriptions)} уведомлений об истечении через {days} дней",
                        {"days": days, "count": len(expiring_subscriptions)}
                    )
                    
        except Exception as e:
            logger.error(f"Ошибка проверки истекающих подписок: {e}")
    
    async def _check_trial_expiring_soon(self, db: AsyncSession):
        """Проверка тестовых подписок, истекающих через 2 часа"""
        try:
            # Получаем тестовые подписки, истекающие через 2 часа
            threshold_time = datetime.utcnow() + timedelta(hours=2)
            
            result = await db.execute(
                select(Subscription)
                .options(selectinload(Subscription.user))
                .where(
                    and_(
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                        Subscription.is_trial == True,
                        Subscription.end_date <= threshold_time,
                        Subscription.end_date > datetime.utcnow()
                    )
                )
            )
            trial_expiring = result.scalars().all()
            
            for subscription in trial_expiring:
                user = subscription.user
                if not user:
                    continue
                
                notification_key = f"trial_2h_{user.telegram_id}"
                if notification_key in self._notified_users:
                    continue  # Уже уведомляли
                
                if self.bot:
                    await self._send_trial_ending_notification(user, subscription)
                    self._notified_users.add(notification_key)
                
                logger.info(f"🎁 Пользователю {user.telegram_id} отправлено уведомление об окончании тестовой подписки через 2 часа")
            
            if trial_expiring:
                await self._log_monitoring_event(
                    db, "trial_expiring_notifications_sent",
                    f"Отправлено {len(trial_expiring)} уведомлений об окончании тестовых подписок",
                    {"count": len(trial_expiring)}
                )
                
        except Exception as e:
            logger.error(f"Ошибка проверки истекающих тестовых подписок: {e}")
    
    async def _get_expiring_paid_subscriptions(self, db: AsyncSession, days_before: int) -> List[Subscription]:
        """Получение платных подписок, истекающих через указанное количество дней"""
        threshold_date = datetime.utcnow() + timedelta(days=days_before)
        
        result = await db.execute(
            select(Subscription)
            .options(selectinload(Subscription.user))
            .where(
                and_(
                    Subscription.status == SubscriptionStatus.ACTIVE.value,
                    Subscription.is_trial == False,  # Только платные
                    Subscription.end_date <= threshold_date,
                    Subscription.end_date > datetime.utcnow()
                )
            )
        )
        return result.scalars().all()
    
    async def _process_autopayments(self, db: AsyncSession):
        """Обработка автоплатежей"""
        try:
            # Исправленный запрос с использованием индивидуальных настроек
            current_time = datetime.utcnow()
            
            result = await db.execute(
                select(Subscription)
                .options(selectinload(Subscription.user))
                .where(
                    and_(
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                        Subscription.autopay_enabled == True,
                        Subscription.is_trial == False  # Автооплата только для платных
                    )
                )
            )
            all_autopay_subscriptions = result.scalars().all()
            
            # Фильтруем по времени с учетом индивидуальных настроек
            autopay_subscriptions = []
            for sub in all_autopay_subscriptions:
                days_before_expiry = (sub.end_date - current_time).days
                if days_before_expiry <= sub.autopay_days_before:
                    autopay_subscriptions.append(sub)
            
            processed_count = 0
            failed_count = 0
            
            for subscription in autopay_subscriptions:
                user = subscription.user
                if not user:
                    continue
                
                renewal_cost = settings.PRICE_30_DAYS
                
                # Проверяем, не списывали ли уже сегодня
                autopay_key = f"autopay_{user.telegram_id}_{subscription.id}"
                if autopay_key in self._notified_users:
                    continue
                
                if user.balance_kopeks >= renewal_cost:
                    # Списываем средства
                    success = await subtract_user_balance(
                        db, user, renewal_cost,
                        "Автопродление подписки"
                    )
                    
                    if success:
                        # Продлеваем подписку
                        await extend_subscription(db, subscription, 30)
                        await self.subscription_service.update_remnawave_user(db, subscription)
                        
                        # Уведомляем об успешном автоплатеже
                        if self.bot:
                            await self._send_autopay_success_notification(user, renewal_cost, 30)
                        
                        processed_count += 1
                        self._notified_users.add(autopay_key)
                        logger.info(f"💳 Автопродление подписки пользователя {user.telegram_id} успешно")
                    else:
                        failed_count += 1
                        if self.bot:
                            await self._send_autopay_failed_notification(user, user.balance_kopeks, renewal_cost)
                        logger.warning(f"💳 Ошибка списания средств для автопродления пользователя {user.telegram_id}")
                else:
                    failed_count += 1
                    # Уведомляем о недостатке средств
                    if self.bot:
                        await self._send_autopay_failed_notification(user, user.balance_kopeks, renewal_cost)
                    logger.warning(f"💳 Недостаточно средств для автопродления у пользователя {user.telegram_id}")
            
            if processed_count > 0 or failed_count > 0:
                await self._log_monitoring_event(
                    db, "autopayments_processed",
                    f"Автоплатежи: успешно {processed_count}, неудачно {failed_count}",
                    {"processed": processed_count, "failed": failed_count}
                )
                
        except Exception as e:
            logger.error(f"Ошибка обработки автоплатежей: {e}")
    
    # Методы отправки уведомлений
    async def _send_subscription_expired_notification(self, user: User):
        """Уведомление об истечении подписки"""
        try:
            texts = get_texts(user.language)
            message = texts.SUBSCRIPTION_EXPIRED
            await self.bot.send_message(user.telegram_id, message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об истечении подписки пользователю {user.telegram_id}: {e}")
    
    async def _send_subscription_expiring_notification(self, user: User, subscription: Subscription, days: int):
        """Уведомление об истечении подписки через N дней"""
        try:
            texts = get_texts(user.language)
            message = texts.SUBSCRIPTION_EXPIRING.format(days=days)
            await self.bot.send_message(user.telegram_id, message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об истечении подписки пользователю {user.telegram_id}: {e}")
    
    async def _send_trial_ending_notification(self, user: User, subscription: Subscription):
        """Уведомление об окончании тестовой подписки через 2 часа"""
        try:
            texts = get_texts(user.language)
            
            # Создаем специальное сообщение для тестовой подписки
            message = f"""
🎁 <b>Тестовая подписка скоро закончится!</b>

Ваша тестовая подписка истекает через 2 часа.

💎 <b>Не хотите остаться без VPN?</b>
Переходите на полную подписку со скидкой!

🔥 <b>Специальное предложение:</b>
• 30 дней всего за {settings.format_price(settings.PRICE_30_DAYS)}
• Безлимитный трафик
• Все серверы доступны
• Поддержка до 3 устройств

⚡️ Успейте оформить до окончания тестового периода!
"""
            
            # Добавляем inline клавиатуру с кнопкой покупки
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy_subscription")],
                [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="balance_top_up")]
            ])
            
            await self.bot.send_message(
                user.telegram_id, 
                message, 
                parse_mode="HTML",
                reply_markup=keyboard
            )
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об окончании тестовой подписки пользователю {user.telegram_id}: {e}")
    
    async def _send_autopay_success_notification(self, user: User, amount: int, days: int):
        """Уведомление об успешном автоплатеже"""
        try:
            texts = get_texts(user.language)
            message = texts.AUTOPAY_SUCCESS.format(
                days=days,
                amount=settings.format_price(amount)
            )
            await self.bot.send_message(user.telegram_id, message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об автоплатеже пользователю {user.telegram_id}: {e}")
    
    async def _send_autopay_failed_notification(self, user: User, balance: int, required: int):
        """Уведомление о неудачном автоплатеже"""
        try:
            texts = get_texts(user.language)
            message = texts.AUTOPAY_FAILED.format(
                balance=settings.format_price(balance),
                required=settings.format_price(required)
            )
            
            # Добавляем кнопку пополнения баланса
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="balance_top_up")]
            ])
            
            await self.bot.send_message(
                user.telegram_id, 
                message, 
                parse_mode="HTML",
                reply_markup=keyboard
            )
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о неудачном автоплатеже пользователю {user.telegram_id}: {e}")
    
    # Остальные методы остаются без изменений...
    async def _cleanup_inactive_users(self, db: AsyncSession):
        try:
            now = datetime.utcnow()
            if now.hour != 3: 
                return
            
            inactive_users = await get_inactive_users(db, settings.INACTIVE_USER_DELETE_MONTHS)
            deleted_count = 0
            
            for user in inactive_users:
                if not user.subscription or not user.subscription.is_active:
                    success = await delete_user(db, user)
                    if success:
                        deleted_count += 1
            
            if deleted_count > 0:
                await self._log_monitoring_event(
                    db, "inactive_users_cleanup",
                    f"Удалено {deleted_count} неактивных пользователей",
                    {"deleted_count": deleted_count}
                )
                logger.info(f"🗑️ Удалено {deleted_count} неактивных пользователей")
                
        except Exception as e:
            logger.error(f"Ошибка очистки неактивных пользователей: {e}")
    
    async def _sync_with_remnawave(self, db: AsyncSession):
        try:
            now = datetime.utcnow()
            if now.minute != 0:
                return
            
            async with self.subscription_service.api as api:
                system_stats = await api.get_system_stats()
                
                await self._log_monitoring_event(
                    db, "remnawave_sync",
                    "Синхронизация с RemnaWave завершена",
                    {"stats": system_stats}
                )
                
        except Exception as e:
            logger.error(f"Ошибка синхронизации с RemnaWave: {e}")
            await self._log_monitoring_event(
                db, "remnawave_sync_error",
                f"Ошибка синхронизации с RemnaWave: {str(e)}",
                {"error": str(e)},
                is_success=False
            )
    
    async def _log_monitoring_event(
        self,
        db: AsyncSession,
        event_type: str,
        message: str,
        data: Dict[str, Any] = None,
        is_success: bool = True
    ):
        try:
            log_entry = MonitoringLog(
                event_type=event_type,
                message=message,
                data=data or {},
                is_success=is_success
            )
            
            db.add(log_entry)
            await db.commit()
            
        except Exception as e:
            logger.error(f"Ошибка логирования события мониторинга: {e}")
    
    async def get_monitoring_status(self, db: AsyncSession) -> Dict[str, Any]:
        try:
            from sqlalchemy import select, desc
            
            recent_events_result = await db.execute(
                select(MonitoringLog)
                .order_by(desc(MonitoringLog.created_at))
                .limit(10)
            )
            recent_events = recent_events_result.scalars().all()
            
            yesterday = datetime.utcnow() - timedelta(days=1)
            
            events_24h_result = await db.execute(
                select(MonitoringLog)
                .where(MonitoringLog.created_at >= yesterday)
            )
            events_24h = events_24h_result.scalars().all()
            
            successful_events = sum(1 for event in events_24h if event.is_success)
            failed_events = sum(1 for event in events_24h if not event.is_success)
            
            return {
                "is_running": self.is_running,
                "last_update": datetime.utcnow(),
                "recent_events": [
                    {
                        "type": event.event_type,
                        "message": event.message,
                        "success": event.is_success,
                        "created_at": event.created_at
                    }
                    for event in recent_events
                ],
                "stats_24h": {
                    "total_events": len(events_24h),
                    "successful": successful_events,
                    "failed": failed_events,
                    "success_rate": round(successful_events / len(events_24h) * 100, 1) if events_24h else 0
                }
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения статуса мониторинга: {e}")
            return {
                "is_running": self.is_running,
                "last_update": datetime.utcnow(),
                "recent_events": [],
                "stats_24h": {
                    "total_events": 0,
                    "successful": 0,
                    "failed": 0,
                    "success_rate": 0
                }
            }
    
    async def force_check_subscriptions(self, db: AsyncSession) -> Dict[str, int]:
        try:
            # Проверяем истекшие
            expired_subscriptions = await get_expired_subscriptions(db)
            expired_count = 0
            
            for subscription in expired_subscriptions:
                await deactivate_subscription(db, subscription)
                expired_count += 1
            
            expiring_subscriptions = await get_expiring_subscriptions(db, 1)
            expiring_count = len(expiring_subscriptions)
            
            autopay_subscriptions = await get_subscriptions_for_autopay(db)
            autopay_processed = 0
            
            for subscription in autopay_subscriptions:
                user = await get_user_by_id(db, subscription.user_id)
                if user and user.balance_kopeks >= settings.PRICE_30_DAYS:
                    autopay_processed += 1
            
            await self._log_monitoring_event(
                db, "manual_check_subscriptions",
                f"Принудительная проверка: истекло {expired_count}, истекает {expiring_count}, автоплатежей {autopay_processed}",
                {
                    "expired": expired_count,
                    "expiring": expiring_count,
                    "autopay_ready": autopay_processed
                }
            )
            
            return {
                "expired": expired_count,
                "expiring": expiring_count,
                "autopay_ready": autopay_processed
            }
            
        except Exception as e:
            logger.error(f"Ошибка принудительной проверки подписок: {e}")
            return {"expired": 0, "expiring": 0, "autopay_ready": 0}
    
    async def get_monitoring_logs(
        self,
        db: AsyncSession,
        limit: int = 50,
        event_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            from sqlalchemy import select, desc
            
            query = select(MonitoringLog).order_by(desc(MonitoringLog.created_at))
            
            if event_type:
                query = query.where(MonitoringLog.event_type == event_type)
            
            query = query.limit(limit)
            
            result = await db.execute(query)
            logs = result.scalars().all()
            
            return [
                {
                    "id": log.id,
                    "event_type": log.event_type,
                    "message": log.message,
                    "data": log.data,
                    "is_success": log.is_success,
                    "created_at": log.created_at
                }
                for log in logs
            ]
            
        except Exception as e:
            logger.error(f"Ошибка получения логов мониторинга: {e}")
            return []
    
    async def cleanup_old_logs(self, db: AsyncSession, days: int = 30) -> int:
        try:
            from sqlalchemy import delete
            
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            result = await db.execute(
                delete(MonitoringLog).where(MonitoringLog.created_at < cutoff_date)
            )
            
            deleted_count = result.rowcount
            await db.commit()
            
            logger.info(f"Удалено {deleted_count} старых записей логов")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Ошибка очистки логов: {e}")
            return 0


monitoring_service = MonitoringService()