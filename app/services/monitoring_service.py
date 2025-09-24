import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
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
from app.database.crud.notification import (
    notification_sent,
    record_notification,
)
from app.database.models import MonitoringLog, SubscriptionStatus, Subscription, User, Ticket, TicketStatus
from app.services.subscription_service import SubscriptionService
from app.services.payment_service import PaymentService
from app.services.notification_settings_service import NotificationSettingsService
from app.localization.texts import get_texts

from app.external.remnawave_api import (
    RemnaWaveUser, UserStatus, TrafficLimitStrategy, RemnaWaveAPIError
)

logger = logging.getLogger(__name__)


class MonitoringService:
    
    def __init__(self, bot=None):
        self.is_running = False
        self.subscription_service = SubscriptionService()
        self.payment_service = PaymentService()
        self.bot = bot
        self._notified_users: Set[str] = set() 
        self._last_cleanup = datetime.utcnow()
        self._sla_task = None
    
    async def start_monitoring(self):
        if self.is_running:
            logger.warning("Мониторинг уже запущен")
            return
        
        self.is_running = True
        logger.info("🔄 Запуск службы мониторинга")
        # Start dedicated SLA loop with its own interval for timely 5-min checks
        try:
            if not self._sla_task or self._sla_task.done():
                self._sla_task = asyncio.create_task(self._sla_loop())
        except Exception as e:
            logger.error(f"Не удалось запустить SLA-мониторинг: {e}")
        
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
        try:
            if self._sla_task and not self._sla_task.done():
                self._sla_task.cancel()
        except Exception:
            pass
    
    async def _monitoring_cycle(self):
        async for db in get_db():
            try:
                await self._cleanup_notification_cache()

                await self._check_expired_subscriptions(db)
                await self._check_expired_followups(db)
                await self._check_expiring_subscriptions(db)
                await self._check_trial_expiring_soon(db)
                await self._check_trial_inactive_users(db)
                await self._process_autopayments(db)
                await self._cleanup_inactive_users(db)
                await self._sync_with_remnawave(db)
                
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
    
    async def _cleanup_notification_cache(self):
        current_time = datetime.utcnow()
        
        if (current_time - self._last_cleanup).total_seconds() >= 3600:
            old_count = len(self._notified_users)
            self._notified_users.clear()
            self._last_cleanup = current_time
            logger.info(f"🧹 Очищен кеш уведомлений ({old_count} записей)")
    
    async def _check_expired_subscriptions(self, db: AsyncSession):
        try:
            expired_subscriptions = await get_expired_subscriptions(db)

            for subscription in expired_subscriptions:
                from app.database.crud.subscription import expire_subscription
                await expire_subscription(db, subscription)
                
                user = await get_user_by_id(db, subscription.user_id)
                if user and user.remnawave_uuid:
                    await self.subscription_service.disable_remnawave_user(user.remnawave_uuid)
                
                if user and self.bot:
                    await self._send_subscription_expired_notification(user)
                
                logger.info(f"🔴 Подписка пользователя {subscription.user_id} истекла и статус изменен на 'expired'")
            
            if expired_subscriptions:
                await self._log_monitoring_event(
                    db, "expired_subscriptions_processed",
                    f"Обработано {len(expired_subscriptions)} истёкших подписок",
                    {"count": len(expired_subscriptions)}
                )
                
        except Exception as e:
            logger.error(f"Ошибка проверки истёкших подписок: {e}")

    async def _check_expired_followups(self, db: AsyncSession):
        try:
            if not self.bot:
                return

            day1_enabled = NotificationSettingsService.is_expired_day1_enabled()
            day23_enabled = NotificationSettingsService.is_expired_day23_enabled()
            dayn_enabled = NotificationSettingsService.is_expired_dayn_enabled()

            if not any([day1_enabled, day23_enabled, dayn_enabled]):
                return

            result = await db.execute(
                select(Subscription)
                .options(selectinload(Subscription.user))
                .where(Subscription.status == SubscriptionStatus.EXPIRED.value)
            )
            expired_subscriptions = result.scalars().all()

            if not expired_subscriptions:
                return

            now = datetime.utcnow()
            sent_day1 = 0
            sent_day23 = 0
            sent_dayn = 0
            threshold_n = NotificationSettingsService.get_expired_dayn_threshold_days()
            discount_day23 = NotificationSettingsService.get_expired_day23_discount_percent()
            discount_dayn = NotificationSettingsService.get_expired_dayn_discount_percent()
            valid_day23 = NotificationSettingsService.get_expired_day23_valid_hours()
            valid_dayn = NotificationSettingsService.get_expired_dayn_valid_hours()

            for subscription in expired_subscriptions:
                user = subscription.user
                if not user:
                    continue

                delta = now - subscription.end_date
                if delta.total_seconds() < 0:
                    continue

                days_since = int(delta.total_seconds() // 86400)

                if days_since < 1:
                    continue

                if (
                    day1_enabled
                    and days_since == 1
                    and not await notification_sent(db, user.id, subscription.id, "expired_followup_day1", days_since)
                ):
                    success = await self._send_expired_followup_notification(
                        user,
                        subscription,
                        "day1",
                        days_since=days_since,
                    )
                    if success:
                        await record_notification(db, user.id, subscription.id, "expired_followup_day1", days_since)
                        sent_day1 += 1

                if (
                    day23_enabled
                    and days_since in {2, 3}
                    and not await notification_sent(db, user.id, subscription.id, "expired_followup_day23", days_since)
                ):
                    success = await self._send_expired_followup_notification(
                        user,
                        subscription,
                        "day23",
                        days_since=days_since,
                        discount_percent=discount_day23,
                        valid_hours=valid_day23,
                    )
                    if success:
                        await record_notification(db, user.id, subscription.id, "expired_followup_day23", days_since)
                        sent_day23 += 1

                if (
                    dayn_enabled
                    and days_since >= threshold_n
                    and not await notification_sent(db, user.id, subscription.id, "expired_followup_dayn", days_since)
                ):
                    success = await self._send_expired_followup_notification(
                        user,
                        subscription,
                        "dayn",
                        days_since=days_since,
                        discount_percent=discount_dayn,
                        valid_hours=valid_dayn,
                        threshold_days=threshold_n,
                    )
                    if success:
                        await record_notification(db, user.id, subscription.id, "expired_followup_dayn", days_since)
                        sent_dayn += 1

            total_sent = sent_day1 + sent_day23 + sent_dayn
            if total_sent > 0:
                await self._log_monitoring_event(
                    db,
                    "expired_followup_notifications",
                    "Отправлены напоминания после окончания подписки",
                    {
                        "sent_day1": sent_day1,
                        "sent_day23": sent_day23,
                        "sent_dayn": sent_dayn,
                        "total": total_sent,
                    },
                )

        except Exception as e:
            logger.error(f"Ошибка отправки последующих уведомлений по истекшим подпискам: {e}")

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
            
            current_time = datetime.utcnow()
            is_active = (subscription.status == SubscriptionStatus.ACTIVE.value and 
                        subscription.end_date > current_time)
            
            if (subscription.status == SubscriptionStatus.ACTIVE.value and 
                subscription.end_date <= current_time):
                subscription.status = SubscriptionStatus.EXPIRED.value
                await db.commit()
                is_active = False
                logger.info(f"📝 Статус подписки {subscription.id} обновлен на 'expired'")
            
            async with self.api as api:
                updated_user = await api.update_user(
                    uuid=user.remnawave_uuid,
                    status=UserStatus.ACTIVE if is_active else UserStatus.EXPIRED,
                    expire_at=subscription.end_date,
                    traffic_limit_bytes=self._gb_to_bytes(subscription.traffic_limit_gb),
                    traffic_limit_strategy=TrafficLimitStrategy.MONTH,
                    hwid_device_limit=subscription.device_limit,
                    description=settings.format_remnawave_user_description(
                        full_name=user.full_name,
                        username=user.username,
                        telegram_id=user.telegram_id
                    ),
                    active_internal_squads=subscription.connected_squads
                )
                
                subscription.subscription_url = updated_user.subscription_url
                await db.commit()
                
                status_text = "активным" if is_active else "истёкшим"
                logger.info(f"✅ Обновлен RemnaWave пользователь {user.remnawave_uuid} со статусом {status_text}")
                return updated_user
                
        except RemnaWaveAPIError as e:
            logger.error(f"Ошибка обновления RemnaWave пользователя: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка обновления RemnaWave пользователя: {e}")
            return None
    
    async def _check_expiring_subscriptions(self, db: AsyncSession):
        try:
            warning_days = settings.get_autopay_warning_days()
            all_processed_users = set() 
            
            for days in warning_days:
                expiring_subscriptions = await self._get_expiring_paid_subscriptions(db, days)
                sent_count = 0
                
                for subscription in expiring_subscriptions:
                    user = await get_user_by_id(db, subscription.user_id)
                    if not user:
                        continue

                    user_key = f"user_{user.telegram_id}_today"

                    if (await notification_sent(db, user.id, subscription.id, "expiring", days) or
                        user_key in all_processed_users):
                        logger.debug(f"🔄 Пропускаем дублирование для пользователя {user.telegram_id} на {days} дней")
                        continue

                    should_send = True
                    for other_days in warning_days:
                        if other_days < days:
                            other_subs = await self._get_expiring_paid_subscriptions(db, other_days)
                            if any(s.user_id == user.id for s in other_subs):
                                should_send = False
                                logger.debug(f"🎯 Пропускаем уведомление на {days} дней для пользователя {user.telegram_id}, есть более срочное на {other_days} дней")
                                break

                    if not should_send:
                        continue

                    if self.bot:
                        success = await self._send_subscription_expiring_notification(user, subscription, days)
                        if success:
                            await record_notification(db, user.id, subscription.id, "expiring", days)
                            all_processed_users.add(user_key)
                            sent_count += 1
                            logger.info(f"✅ Пользователю {user.telegram_id} отправлено уведомление об истечении подписки через {days} дней")
                        else:
                            logger.warning(f"❌ Не удалось отправить уведомление пользователю {user.telegram_id}")
                
                if sent_count > 0:
                    await self._log_monitoring_event(
                        db, "expiring_notifications_sent",
                        f"Отправлено {sent_count} уведомлений об истечении через {days} дней",
                        {"days": days, "count": sent_count}
                    )
                    
        except Exception as e:
            logger.error(f"Ошибка проверки истекающих подписок: {e}")
    
    async def _check_trial_expiring_soon(self, db: AsyncSession):
        try:
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

                if await notification_sent(db, user.id, subscription.id, "trial_2h"):
                    continue

                if self.bot:
                    success = await self._send_trial_ending_notification(user, subscription)
                    if success:
                        await record_notification(db, user.id, subscription.id, "trial_2h")
                        logger.info(f"🎁 Пользователю {user.telegram_id} отправлено уведомление об окончании тестовой подписки через 2 часа")
            
            if trial_expiring:
                await self._log_monitoring_event(
                    db, "trial_expiring_notifications_sent",
                    f"Отправлено {len(trial_expiring)} уведомлений об окончании тестовых подписок",
                    {"count": len(trial_expiring)}
                )
                
        except Exception as e:
            logger.error(f"Ошибка проверки истекающих тестовых подписок: {e}")

    async def _check_trial_inactive_users(self, db: AsyncSession):
        try:
            if not self.bot:
                return

            one_hour_enabled = NotificationSettingsService.is_trial_inactive_1h_enabled()
            day_enabled = NotificationSettingsService.is_trial_inactive_24h_enabled()

            if not (one_hour_enabled or day_enabled):
                return

            result = await db.execute(
                select(Subscription)
                .options(selectinload(Subscription.user))
                .where(
                    Subscription.is_trial == True,
                    Subscription.first_usage_at.is_(None),
                    Subscription.start_date.is_not(None),
                    Subscription.status.in_(
                        [
                            SubscriptionStatus.ACTIVE.value,
                            SubscriptionStatus.TRIAL.value,
                        ]
                    ),
                )
            )
            trial_subscriptions = result.scalars().all()

            if not trial_subscriptions:
                return

            now = datetime.utcnow()
            sent_1h = 0
            sent_24h = 0

            for subscription in trial_subscriptions:
                user = subscription.user
                if not user:
                    continue

                try:
                    sync_success = await self.subscription_service.sync_subscription_usage(db, subscription)
                    if sync_success:
                        await db.refresh(subscription)
                except Exception as sync_error:  # pragma: no cover - defensive log
                    logger.debug(
                        "Не удалось синхронизировать использование триальной подписки %s: %s",
                        subscription.id,
                        sync_error,
                    )

                if subscription.first_usage_at:
                    continue

                started_at = subscription.start_date or subscription.created_at
                if not started_at:
                    continue

                time_since_start = now - started_at

                if (
                    one_hour_enabled
                    and time_since_start >= timedelta(hours=1)
                    and not await notification_sent(db, user.id, subscription.id, "trial_inactive_1h", 0)
                ):
                    success = await self._send_trial_inactive_notification(user, subscription, "1h")
                    if success:
                        await record_notification(db, user.id, subscription.id, "trial_inactive_1h", 0)
                        sent_1h += 1

                if (
                    day_enabled
                    and time_since_start >= timedelta(days=1)
                    and not await notification_sent(db, user.id, subscription.id, "trial_inactive_24h", 1)
                ):
                    success = await self._send_trial_inactive_notification(user, subscription, "24h")
                    if success:
                        await record_notification(db, user.id, subscription.id, "trial_inactive_24h", 1)
                        sent_24h += 1

            total_sent = sent_1h + sent_24h
            if total_sent > 0:
                await self._log_monitoring_event(
                    db,
                    "trial_inactive_notifications",
                    "Отправлены напоминания о неиспользуемом триале",
                    {"sent_1h": sent_1h, "sent_24h": sent_24h, "total": total_sent},
                )

        except Exception as e:
            logger.error(f"Ошибка проверки неиспользуемых триалов: {e}")

    async def _get_expiring_paid_subscriptions(self, db: AsyncSession, days_before: int) -> List[Subscription]:
        current_time = datetime.utcnow()
        threshold_date = current_time + timedelta(days=days_before)
        
        result = await db.execute(
            select(Subscription)
            .options(selectinload(Subscription.user))
            .where(
                and_(
                    Subscription.status == SubscriptionStatus.ACTIVE.value,
                    Subscription.is_trial == False, 
                    Subscription.end_date > current_time,
                    Subscription.end_date <= threshold_date
                )
            )
        )
        
        logger.debug(f"🔍 Поиск платных подписок, истекающих в ближайшие {days_before} дней")
        logger.debug(f"📅 Текущее время: {current_time}")
        logger.debug(f"📅 Пороговая дата: {threshold_date}")
        
        subscriptions = result.scalars().all()
        logger.info(f"📊 Найдено {len(subscriptions)} платных подписок для уведомлений")
        
        return subscriptions
    
    async def _process_autopayments(self, db: AsyncSession):
        try:
            current_time = datetime.utcnow()
            
            result = await db.execute(
                select(Subscription)
                .options(selectinload(Subscription.user))
                .where(
                    and_(
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                        Subscription.autopay_enabled == True,
                        Subscription.is_trial == False 
                    )
                )
            )
            all_autopay_subscriptions = result.scalars().all()
            
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
                
                autopay_key = f"autopay_{user.telegram_id}_{subscription.id}"
                if autopay_key in self._notified_users:
                    continue
                
                if user.balance_kopeks >= renewal_cost:
                    success = await subtract_user_balance(
                        db, user, renewal_cost,
                        "Автопродление подписки"
                    )
                    
                    if success:
                        await extend_subscription(db, subscription, 30)
                        await self.subscription_service.update_remnawave_user(db, subscription)
                        
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
    
    async def _send_subscription_expired_notification(self, user: User) -> bool:
        try:
            message = """
⛔ <b>Подписка истекла</b>

Ваша подписка истекла. Для восстановления доступа продлите подписку.

🔧 Доступ к серверам заблокирован до продления.
"""
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Купить подписку", callback_data="menu_buy")],
                [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="balance_topup")]
            ])
            
            await self.bot.send_message(
                user.telegram_id, 
                message, 
                parse_mode="HTML",
                reply_markup=keyboard
            )
            return True
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об истечении подписки пользователю {user.telegram_id}: {e}")
            return False
    
    async def _send_subscription_expiring_notification(self, user: User, subscription: Subscription, days: int) -> bool:
        try:
            from app.utils.formatters import format_days_declension
            
            texts = get_texts(user.language)
            days_text = format_days_declension(days, user.language)
            
            if subscription.autopay_enabled:
                autopay_status = "✅ Включен - подписка продлится автоматически"
                action_text = f"💰 Убедитесь, что на балансе достаточно средств: {texts.format_price(user.balance_kopeks)}"
            else:
                autopay_status = "❌ Отключен - не забудьте продлить вручную!"
                action_text = "💡 Включите автоплатеж или продлите подписку вручную"
            
            message = f"""
⚠️ <b>Подписка истекает через {days_text}!</b>

Ваша платная подписка истекает {subscription.end_date.strftime("%d.%m.%Y %H:%M")}.

💳 <b>Автоплатеж:</b> {autopay_status}

{action_text}
"""
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏰ Продлить подписку", callback_data="subscription_extend")],
                [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="balance_topup")],
                [InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")]
            ])
            
            await self.bot.send_message(
                user.telegram_id, 
                message, 
                parse_mode="HTML",
                reply_markup=keyboard
            )
            return True
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об истечении подписки пользователю {user.telegram_id}: {e}")
            return False
    
    async def _send_trial_ending_notification(self, user: User, subscription: Subscription) -> bool:
        try:
            texts = get_texts(user.language)

            message = f"""
🎁 <b>Тестовая подписка скоро закончится!</b>

Ваша тестовая подписка истекает через 2 часа.

💎 <b>Не хотите остаться без VPN?</b>
Переходите на полную подписку!

🔥 <b>Специальное предложение:</b>
• 30 дней всего за {settings.format_price(settings.PRICE_30_DAYS)}
• Безлимитный трафик
• Все серверы доступны
• Скорость до 1ГБит/сек

⚡️ Успейте оформить до окончания тестового периода!
"""
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Купить подписку", callback_data="menu_buy")],
                [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="balance_topup")]
            ])
            
            await self.bot.send_message(
                user.telegram_id, 
                message, 
                parse_mode="HTML",
                reply_markup=keyboard
            )
            return True
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об окончании тестовой подписки пользователю {user.telegram_id}: {e}")
            return False

    async def _send_trial_inactive_notification(self, user: User, subscription: Subscription, stage: str) -> bool:
        try:
            language = (user.language or settings.DEFAULT_LANGUAGE).lower()
            support_url = settings.get_support_contact_url()
            support_text = settings.get_support_contact_display()

            if stage == "1h":
                if language.startswith("en"):
                    message = (
                        "👋 <b>Let's set up your VPN</b>\n\n"
                        "It's been an hour since you activated the trial, but we haven't seen any connections yet.\n\n"
                        "Tap the button below to add the configuration and start browsing safely."
                    )
                else:
                    message = (
                        "👋 <b>Давайте подключим VPN</b>\n\n"
                        "Прошел час после активации тестового доступа, но подключений пока нет.\n\n"
                        "Нажмите кнопку ниже, чтобы добавить конфигурацию и начать пользоваться сервисом."
                    )
            else:  # 24h stage
                if language.startswith("en"):
                    message = (
                        "⏰ <b>Trial is still waiting for you</b>\n\n"
                        "A whole day has passed and the VPN is still not connected.\n\n"
                        "Connect now and make the most of the test period — it only takes a minute!"
                    )
                else:
                    message = (
                        "⏰ <b>Тест все еще не используется</b>\n\n"
                        "Прошли сутки, но VPN так и не был подключен.\n\n"
                        "Подключитесь сейчас и успейте воспользоваться тестовым периодом — это занимает меньше минуты!"
                    )

            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            buttons = [
                [InlineKeyboardButton(text="🔗 Подключить VPN" if not language.startswith("en") else "🔗 Connect VPN", callback_data="subscription_connect")],
                [InlineKeyboardButton(text="📱 Моя подписка" if not language.startswith("en") else "📱 My subscription", callback_data="menu_subscription")],
            ]

            if support_url:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=("🛟 Поддержка" if not language.startswith("en") else "🛟 Support"),
                            url=support_url,
                        )
                    ]
                )
            elif support_text:
                message += f"\n\n💬 {support_text}"

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            await self.bot.send_message(
                user.telegram_id,
                message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return True

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о неиспользуемом триале пользователю {user.telegram_id}: {e}")
            return False

    async def _send_expired_followup_notification(
        self,
        user: User,
        subscription: Subscription,
        stage: str,
        *,
        days_since: int,
        discount_percent: int | None = None,
        valid_hours: int | None = None,
        threshold_days: int | None = None,
    ) -> bool:
        try:
            language = (user.language or settings.DEFAULT_LANGUAGE).lower()
            support_url = settings.get_support_contact_url()
            support_text = settings.get_support_contact_display()

            if language.startswith("en"):
                if stage == "day1":
                    message = (
                        "📅 <b>Your VPN subscription expired yesterday</b>\n\n"
                        "Renew now to restore unlimited access.\n"
                        "Tap a button below — activation is instant."
                    )
                elif stage == "day23":
                    message = (
                        "🔥 <b>Special return offer</b>\n\n"
                        f"It's been {days_since} days since the subscription expired.\n"
                        f"Renew now with a {discount_percent}% discount valid for {valid_hours} hours."
                    )
                else:
                    trigger_days = threshold_days or days_since
                    message = (
                        "🎁 <b>Extra discount just for you</b>\n\n"
                        f"The subscription ended {days_since} days ago.\n"
                        f"Come back with a {discount_percent}% discount valid for {valid_hours} hours.\n"
                        f"Offer unlocked after {trigger_days} days without renewal."
                    )
                extend_text = "⏰ Renew subscription"
                buy_text = "💎 Buy new period"
                balance_text = "💳 Top up balance"
                support_button_text = "🛟 Support"
            else:
                if stage == "day1":
                    message = (
                        "📅 <b>Подписка истекла вчера</b>\n\n"
                        "Продлите доступ прямо сейчас — активация моментальная."
                    )
                elif stage == "day23":
                    message = (
                        "🔥 <b>Скидка на продление</b>\n\n"
                        f"Подписка закончилась {days_since} дня назад.\n"
                        f"Вернитесь со скидкой {discount_percent}% — предложение действует {valid_hours} ч."
                    )
                else:
                    trigger_days = threshold_days or days_since
                    message = (
                        "🎁 <b>Дополнительная скидка для возврата</b>\n\n"
                        f"Прошло {days_since} дней без подписки.\n"
                        f"Продлите её со скидкой {discount_percent}% в течение {valid_hours} ч.\n"
                        f"Предложение доступно после {trigger_days} дней без продления."
                    )
                extend_text = "⏰ Продлить подписку"
                buy_text = "💎 Купить новый период"
                balance_text = "💳 Пополнить баланс"
                support_button_text = "🛟 Поддержка"

            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            buttons = [
                [InlineKeyboardButton(text=extend_text, callback_data="subscription_extend")],
                [InlineKeyboardButton(text=buy_text, callback_data="menu_buy")],
                [InlineKeyboardButton(text=balance_text, callback_data="balance_topup")],
            ]

            if support_url:
                buttons.append([
                    InlineKeyboardButton(text=support_button_text, url=support_url)
                ])
            elif support_text:
                message += f"\n\n💬 {support_text}"

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            await self.bot.send_message(
                user.telegram_id,
                message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return True

        except Exception as e:
            logger.error(
                f"Ошибка отправки уведомления о завершившейся подписке пользователю {user.telegram_id}: {e}"
            )
            return False

    async def _send_autopay_success_notification(self, user: User, amount: int, days: int):
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
        try:
            texts = get_texts(user.language)
            message = texts.AUTOPAY_FAILED.format(
                balance=settings.format_price(balance),
                required=settings.format_price(required)
            )
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="balance_topup")],
                [InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")]
            ])
            
            await self.bot.send_message(
                user.telegram_id, 
                message, 
                parse_mode="HTML",
                reply_markup=keyboard
            )
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о неудачном автоплатеже пользователю {user.telegram_id}: {e}")
    
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
    
    async def _check_ticket_sla(self, db: AsyncSession):
        try:
            # Quick guards
            # Allow runtime toggle from SupportSettingsService
            try:
                from app.services.support_settings_service import SupportSettingsService
                sla_enabled_runtime = SupportSettingsService.get_sla_enabled()
            except Exception:
                sla_enabled_runtime = getattr(settings, 'SUPPORT_TICKET_SLA_ENABLED', True)
            if not sla_enabled_runtime:
                return
            if not self.bot:
                return
            if not settings.is_admin_notifications_enabled():
                return

            from datetime import datetime, timedelta
            try:
                from app.services.support_settings_service import SupportSettingsService
                sla_minutes = max(1, int(SupportSettingsService.get_sla_minutes()))
            except Exception:
                sla_minutes = max(1, int(getattr(settings, 'SUPPORT_TICKET_SLA_MINUTES', 5)))
            cooldown_minutes = max(1, int(getattr(settings, 'SUPPORT_TICKET_SLA_REMINDER_COOLDOWN_MINUTES', 15)))
            now = datetime.utcnow()
            stale_before = now - timedelta(minutes=sla_minutes)
            cooldown_before = now - timedelta(minutes=cooldown_minutes)

            # Tickets to remind: open, no admin reply yet after user's last message (status OPEN), stale by SLA,
            # and either never reminded or cooldown passed
            result = await db.execute(
                select(Ticket)
                .options(selectinload(Ticket.user))
                .where(
                    and_(
                        Ticket.status == TicketStatus.OPEN.value,
                        Ticket.updated_at <= stale_before,
                        or_(Ticket.last_sla_reminder_at.is_(None), Ticket.last_sla_reminder_at <= cooldown_before),
                    )
                )
            )
            tickets = result.scalars().all()
            if not tickets:
                return

            from app.services.admin_notification_service import AdminNotificationService

            reminders_sent = 0
            service = AdminNotificationService(self.bot)

            for ticket in tickets:
                try:
                    waited_minutes = max(0, int((now - ticket.updated_at).total_seconds() // 60))
                    title = (ticket.title or '').strip()
                    if len(title) > 60:
                        title = title[:57] + '...'

                    text = (
                        f"⏰ <b>Ожидание ответа на тикет превышено</b>\n\n"
                        f"🆔 <b>ID:</b> <code>{ticket.id}</code>\n"
                        f"👤 <b>User ID:</b> <code>{ticket.user_id}</code>\n"
                        f"📝 <b>Заголовок:</b> {title or '—'}\n"
                        f"⏱️ <b>Ожидает ответа:</b> {waited_minutes} мин\n"
                    )

                    sent = await service.send_ticket_event_notification(text)
                    if sent:
                        ticket.last_sla_reminder_at = now
                        reminders_sent += 1
                        # commit after each to persist timestamp and avoid duplicate reminders on crash
                        await db.commit()
                except Exception as notify_error:
                    logger.error(f"Ошибка отправки SLA-уведомления по тикету {ticket.id}: {notify_error}")

            if reminders_sent > 0:
                await self._log_monitoring_event(
                    db,
                    "ticket_sla_reminders_sent",
                    f"Отправлено {reminders_sent} SLA-напоминаний по тикетам",
                    {"count": reminders_sent},
                )
        except Exception as e:
            logger.error(f"Ошибка проверки SLA тикетов: {e}")

    async def _sla_loop(self):
        try:
            interval_seconds = max(10, int(getattr(settings, 'SUPPORT_TICKET_SLA_CHECK_INTERVAL_SECONDS', 60)))
        except Exception:
            interval_seconds = 60
        while self.is_running:
            try:
                async for db in get_db():
                    try:
                        await self._check_ticket_sla(db)
                    finally:
                        break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в SLA-цикле: {e}")
            await asyncio.sleep(interval_seconds)

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
        event_type: Optional[str] = None,
        page: int = 1,
        per_page: int = 20
    ) -> List[Dict[str, Any]]:
        try:
            from sqlalchemy import select, desc
            
            query = select(MonitoringLog).order_by(desc(MonitoringLog.created_at))
            
            if event_type:
                query = query.where(MonitoringLog.event_type == event_type)
            
            if page > 1 or per_page != 20:
                offset = (page - 1) * per_page
                query = query.offset(offset).limit(per_page)
            else:
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

    async def get_monitoring_logs_count(
        self, 
        db: AsyncSession,
        event_type: Optional[str] = None
    ) -> int:
        try:
            from sqlalchemy import select, func
            
            query = select(func.count(MonitoringLog.id))
            
            if event_type:
                query = query.where(MonitoringLog.event_type == event_type)
            
            result = await db.execute(query)
            count = result.scalar()
            
            return count or 0
            
        except Exception as e:
            logger.error(f"Ошибка получения количества логов: {e}")
            return 0
    
    async def cleanup_old_logs(self, db: AsyncSession, days: int = 30) -> int:
        try:
            from sqlalchemy import delete, select
            
            if days == 0:
                result = await db.execute(delete(MonitoringLog))
            else:
                cutoff_date = datetime.utcnow() - timedelta(days=days)
                result = await db.execute(
                    delete(MonitoringLog).where(MonitoringLog.created_at < cutoff_date)
                )
            
            deleted_count = result.rowcount
            await db.commit()
            
            if days == 0:
                logger.info(f"🗑️ Удалены все логи мониторинга ({deleted_count} записей)")
            else:
                logger.info(f"🗑️ Удалено {deleted_count} старых записей логов (старше {days} дней)")
                
            return deleted_count
            
        except Exception as e:
            logger.error(f"Ошибка очистки логов: {e}")
            await db.rollback()
            return 0


monitoring_service = MonitoringService()
