import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from database import Database
from remnawave_api import RemnaWaveAPI

logger = logging.getLogger(__name__)

class SubscriptionMonitorService:
    
    def __init__(self, bot, db: Database, config, api: RemnaWaveAPI = None):
        self.bot = bot
        self.db = db
        self.config = config
        self.api = api
        self.is_running = False
        self._monitor_task = None
        self._daily_task = None
        self.last_check_time = None
        
    async def start(self):
        if self.is_running:
            logger.warning("Monitor service is already running")
            return
        
        if not getattr(self.config, 'MONITOR_ENABLED', True):
            logger.info("Subscription monitoring disabled in config")
            return
        
        logger.info("Starting subscription monitor service...")
        
        try:
            self.is_running = True
            
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info(f"Started monitoring loop with interval {self.config.MONITOR_CHECK_INTERVAL}s")
            
            self._daily_task = asyncio.create_task(self._daily_loop())
            logger.info(f"Started daily cleanup task for {self.config.MONITOR_DAILY_CHECK_HOUR}:00")
            
            await asyncio.sleep(0.1)
            
            monitor_running = self._monitor_task and not self._monitor_task.done()
            daily_running = self._daily_task and not self._daily_task.done()
            
            if monitor_running and daily_running:
                logger.info("✅ Subscription monitor service started successfully")
            else:
                logger.warning(f"⚠️ Monitor service partially started: monitor={monitor_running}, daily={daily_running}")
                
        except Exception as e:
            logger.error(f"❌ Failed to start monitor service: {e}", exc_info=True)
            self.is_running = False
            
            if self._monitor_task:
                self._monitor_task.cancel()
            if self._daily_task:
                self._daily_task.cancel()
            
            raise
        
    async def stop(self):
        if not self.is_running:
            return
        
        logger.info("Stopping subscription monitor service...")
        self.is_running = False
        
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            logger.info("Monitor loop stopped")
        
        if self._daily_task:
            self._daily_task.cancel()
            try:
                await self._daily_task
            except asyncio.CancelledError:
                pass
            logger.info("Daily task stopped")
        
        logger.info("Subscription monitor service stopped")
        
    async def _monitor_loop(self):
        logger.info("🔥 Starting monitor loop")
        
        logger.info("⏰ Initial check in 10 seconds...")
        await asyncio.sleep(10)
        
        while self.is_running:
            try:
                logger.info("🔍 Running periodic subscription check...")
                warnings_sent = await self._check_expiring_subscriptions()
                
                trial_notifications = await self._check_expired_trial_subscriptions()
                
                if warnings_sent > 0 or trial_notifications > 0:
                    logger.info(f"✅ Monitor check completed: {warnings_sent} warnings sent, {trial_notifications} trial notifications sent")
                else:
                    logger.info("✅ Monitor check completed: no warnings needed")
                
                self.last_check_time = datetime.now()
                
                logger.debug(f"😴 Sleeping for {self.config.MONITOR_CHECK_INTERVAL} seconds...")
                await asyncio.sleep(self.config.MONITOR_CHECK_INTERVAL)
                
            except asyncio.CancelledError:
                logger.info("Monitor loop cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in monitor loop: {e}", exc_info=True)
                error_sleep = min(300, self.config.MONITOR_CHECK_INTERVAL // 2)
                logger.info(f"😴 Error recovery: sleeping for {error_sleep} seconds...")
                await asyncio.sleep(error_sleep)
                
    async def _daily_loop(self):
        logger.info("📅 Starting daily loop")
        
        while self.is_running:
            try:
                now = datetime.now()
                target_hour = self.config.MONITOR_DAILY_CHECK_HOUR
                
                if now.hour == target_hour and now.minute < 30:
                    logger.info("📅 Starting scheduled daily check...")
                    deactivated = await self.force_daily_check()
                    logger.info(f"📅 Daily check completed: {deactivated} subscriptions deactivated")
                    
                    next_check = now.replace(hour=target_hour, minute=0, second=0, microsecond=0) + timedelta(days=1)
                    sleep_time = (next_check - now).total_seconds()
                    logger.info(f"😴 Daily check completed, next check in {sleep_time/3600:.1f} hours")
                else:
                    target_time = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
                    if target_time <= now:
                        target_time += timedelta(days=1)
                    
                    sleep_time = (target_time - now).total_seconds()
                    logger.debug(f"😴 Next daily check in {sleep_time/3600:.1f} hours")
                
                sleep_duration = min(sleep_time, 3600)
                await asyncio.sleep(sleep_duration)
                
            except asyncio.CancelledError:
                logger.info("Daily loop cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in daily loop: {e}", exc_info=True)
                await asyncio.sleep(3600)

    async def _check_expired_trial_subscriptions(self) -> int:
        try:
            logger.info("🆓 Checking for expired trial subscriptions...")
            
            notifications_sent = 0
            now_utc = datetime.utcnow()
            
            all_users = await self.db.get_all_users()
            
            for user in all_users:
                try:
                    user_subs = await self.db.get_user_subscriptions(user.telegram_id)
                    
                    for user_sub in user_subs:
                        try:
                            subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
                            if not subscription:
                                continue
                            
                            if not subscription.is_trial:
                                continue
                            
                            expires_at_utc = user_sub.expires_at
                            if expires_at_utc.tzinfo is None:
                                expires_at_utc = expires_at_utc.replace(tzinfo=None)
                            else:
                                expires_at_utc = expires_at_utc.astimezone(timezone.utc).replace(tzinfo=None)
                            
                            time_diff = expires_at_utc - now_utc
                            hours_since_expiry = -time_diff.total_seconds() / 3600
                            
                            if 1 <= hours_since_expiry <= 24 and user_sub.is_active:
                                logger.info(f"🆓 Sending trial expiry notification to user {user.telegram_id}: "
                                          f"trial '{subscription.name}' expired {hours_since_expiry:.1f} hours ago")
                                
                                try:
                                    await self._send_trial_expiry_notification(user, subscription)
                                    notifications_sent += 1
                                    logger.info(f"✅ Trial expiry notification sent to user {user.telegram_id}")
                                except Exception as notification_error:
                                    logger.error(f"❌ Failed to send trial notification to user {user.telegram_id}: {notification_error}")
                        
                        except Exception as sub_error:
                            logger.error(f"❌ Error checking trial subscription {user_sub.id}: {sub_error}")
                
                except Exception as user_error:
                    logger.error(f"❌ Error checking trial subscriptions for user {user.telegram_id}: {user_error}")
            
            if notifications_sent > 0:
                logger.info(f"🆓 Trial expiry check completed: {notifications_sent} notifications sent")
            
            return notifications_sent
        
        except Exception as e:
            logger.error(f"❌ Critical error in check_expired_trial_subscriptions: {e}", exc_info=True)
            return 0

    async def _send_trial_expiry_notification(self, user, subscription):
        try:
            if not self.bot:
                logger.error("❌ Bot instance is None, cannot send trial notification")
                return
            
            from translations import t
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            message = t('trial_subscription_expired', user.language, name=subscription.name)
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=t('buy_subscription_btn', user.language), 
                    callback_data="buy_subscription"
                )],
                [InlineKeyboardButton(
                    text=t('my_subscriptions_btn', user.language), 
                    callback_data="my_subscriptions"
                )]
            ])
            
            await self.bot.send_message(user.telegram_id, message, reply_markup=keyboard)
            
            logger.info(f"✅ Trial expiry notification sent to user {user.telegram_id} for subscription '{subscription.name}'")
        
        except Exception as e:
            logger.error(f"❌ Error sending trial expiry notification to user {user.telegram_id}: {e}", exc_info=True)
            raise

    async def delete_expired_trial_subscriptions(self, force: bool = False) -> Dict[str, Any]:
        try:
            logger.info("🗑️ Starting deletion of expired trial subscriptions...")
            
            now_utc = datetime.utcnow()
            delete_threshold_days = getattr(self.config, 'DELETE_EXPIRED_TRIAL_DAYS', 1)
            cutoff_date = now_utc - timedelta(days=delete_threshold_days)
            
            logger.info(f"🗑️ Deleting trial subscriptions expired before: {cutoff_date} (older than {delete_threshold_days} days)")
            
            results = {
                'total_checked': 0,
                'deleted_from_db': 0,
                'deleted_from_api': 0,
                'errors': [],
                'deleted_subscriptions': []
            }
            
            all_users = await self.db.get_all_users()
            
            for user in all_users:
                try:
                    user_subs = await self.db.get_user_subscriptions(user.telegram_id)
                    
                    for user_sub in user_subs:
                        results['total_checked'] += 1
                        
                        subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
                        if not subscription or not subscription.is_trial:
                            continue
                        
                        expires_at_utc = user_sub.expires_at
                        if expires_at_utc.tzinfo is None:
                            expires_at_utc = expires_at_utc.replace(tzinfo=None)
                        else:
                            expires_at_utc = expires_at_utc.astimezone(timezone.utc).replace(tzinfo=None)
                        
                        if expires_at_utc > cutoff_date and not force:
                            continue  
                        
                        logger.info(f"🗑️ Deleting expired trial subscription '{subscription.name}' for user {user.telegram_id} "
                                  f"(expired: {expires_at_utc}, cutoff: {cutoff_date})")
                        
                        api_deleted = False
                        if self.api and user_sub.short_uuid:
                            try:
                                api_result = await self.api.delete_user_by_short_uuid(user_sub.short_uuid)
                                if api_result and api_result.get('success'):
                                    api_deleted = True
                                    results['deleted_from_api'] += 1
                                    logger.info(f"✅ Deleted from RemnaWave API: {user_sub.short_uuid}")
                                else:
                                    results['errors'].append(f"Failed to delete {user_sub.short_uuid} from API")
                                    logger.warning(f"⚠️ Failed to delete {user_sub.short_uuid} from API")
                            except Exception as api_error:
                                results['errors'].append(f"API error for {user_sub.short_uuid}: {str(api_error)}")
                                logger.error(f"❌ API error deleting {user_sub.short_uuid}: {api_error}")
                        
                        db_deleted = await self.db.delete_user_subscription(user_sub.id)
                        if db_deleted:
                            results['deleted_from_db'] += 1
                            results['deleted_subscriptions'].append({
                                'user_id': user.telegram_id,
                                'subscription_name': subscription.name,
                                'short_uuid': user_sub.short_uuid,
                                'expired_at': expires_at_utc.isoformat(),
                                'deleted_from_api': api_deleted,
                                'deleted_from_db': True
                            })
                            logger.info(f"✅ Deleted from database: subscription ID {user_sub.id}")
                        else:
                            results['errors'].append(f"Failed to delete subscription ID {user_sub.id} from database")
                            logger.error(f"❌ Failed to delete subscription ID {user_sub.id} from database")
                        
                except Exception as user_error:
                    results['errors'].append(f"Error processing user {user.telegram_id}: {str(user_error)}")
                    logger.error(f"❌ Error processing user {user.telegram_id}: {user_error}")
            
            logger.info(f"🗑️ Trial deletion completed: {results['deleted_from_db']} from DB, {results['deleted_from_api']} from API")
            return results
            
        except Exception as e:
            logger.error(f"❌ Error in delete_expired_trial_subscriptions: {e}", exc_info=True)
            return {
                'total_checked': 0,
                'deleted_from_db': 0,
                'deleted_from_api': 0,
                'errors': [f"Critical error: {str(e)}"],
                'deleted_subscriptions': []
            }

    async def delete_expired_regular_subscriptions(self, force: bool = False) -> Dict[str, Any]:
        try:
            logger.info("🗑️ Starting deletion of expired regular subscriptions...")
            
            now_utc = datetime.utcnow()
            delete_threshold_days = getattr(self.config, 'DELETE_EXPIRED_REGULAR_DAYS', 7)
            cutoff_date = now_utc - timedelta(days=delete_threshold_days)
            
            logger.info(f"🗑️ Deleting regular subscriptions expired before: {cutoff_date} (older than {delete_threshold_days} days)")
            
            results = {
                'total_checked': 0,
                'deleted_from_db': 0,
                'deleted_from_api': 0,
                'errors': [],
                'deleted_subscriptions': []
            }
            
            all_users = await self.db.get_all_users()
            
            for user in all_users:
                try:
                    user_subs = await self.db.get_user_subscriptions(user.telegram_id)
                    
                    for user_sub in user_subs:
                        results['total_checked'] += 1
                        
                        subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
                        if not subscription or subscription.is_trial:
                            continue
                        
                        if getattr(subscription, 'is_imported', False) or subscription.name == "Старая подписка":
                            continue
                        
                        expires_at_utc = user_sub.expires_at
                        if expires_at_utc.tzinfo is None:
                            expires_at_utc = expires_at_utc.replace(tzinfo=None)
                        else:
                            expires_at_utc = expires_at_utc.astimezone(timezone.utc).replace(tzinfo=None)
                        
                        if expires_at_utc > cutoff_date and not force:
                            continue 
                        
                        logger.info(f"🗑️ Deleting expired regular subscription '{subscription.name}' for user {user.telegram_id} "
                                  f"(expired: {expires_at_utc}, cutoff: {cutoff_date})")
                        
                        api_deleted = False
                        if self.api and user_sub.short_uuid:
                            try:
                                api_result = await self.api.delete_user_by_short_uuid(user_sub.short_uuid)
                                if api_result and api_result.get('success'):
                                    api_deleted = True
                                    results['deleted_from_api'] += 1
                                    logger.info(f"✅ Deleted from RemnaWave API: {user_sub.short_uuid}")
                                else:
                                    results['errors'].append(f"Failed to delete {user_sub.short_uuid} from API")
                                    logger.warning(f"⚠️ Failed to delete {user_sub.short_uuid} from API")
                            except Exception as api_error:
                                results['errors'].append(f"API error for {user_sub.short_uuid}: {str(api_error)}")
                                logger.error(f"❌ API error deleting {user_sub.short_uuid}: {api_error}")
                        
                        db_deleted = await self.db.delete_user_subscription(user_sub.id)
                        if db_deleted:
                            results['deleted_from_db'] += 1
                            results['deleted_subscriptions'].append({
                                'user_id': user.telegram_id,
                                'subscription_name': subscription.name,
                                'short_uuid': user_sub.short_uuid,
                                'expired_at': expires_at_utc.isoformat(),
                                'deleted_from_api': api_deleted,
                                'deleted_from_db': True
                            })
                            logger.info(f"✅ Deleted from database: subscription ID {user_sub.id}")
                        else:
                            results['errors'].append(f"Failed to delete subscription ID {user_sub.id} from database")
                            logger.error(f"❌ Failed to delete subscription ID {user_sub.id} from database")
                        
                except Exception as user_error:
                    results['errors'].append(f"Error processing user {user.telegram_id}: {str(user_error)}")
                    logger.error(f"❌ Error processing user {user.telegram_id}: {user_error}")
            
            logger.info(f"🗑️ Regular deletion completed: {results['deleted_from_db']} from DB, {results['deleted_from_api']} from API")
            return results
            
        except Exception as e:
            logger.error(f"❌ Error in delete_expired_regular_subscriptions: {e}", exc_info=True)
            return {
                'total_checked': 0,
                'deleted_from_db': 0,
                'deleted_from_api': 0,
                'errors': [f"Critical error: {str(e)}"],
                'deleted_subscriptions': []
            }
                
    async def _check_expiring_subscriptions(self):
        try:
            logger.info("🔍 Checking for expiring subscriptions...")
        
            all_users = await self.db.get_all_users()
            logger.info(f"👥 Found {len(all_users)} total users in database")
        
            if not all_users:
                logger.warning("⚠️ No users found in database")
                return 0
        
            warnings_sent = 0
            errors_count = 0
            total_subscriptions = 0
        
            now_utc = datetime.utcnow()
            logger.info(f"🕐 Current UTC time: {now_utc}")
        
            for user in all_users:
                try:
                    user_subs = await self.db.get_user_subscriptions(user.telegram_id)
                    total_subscriptions += len(user_subs)
                
                    if not user_subs:
                        logger.debug(f"👤 User {user.telegram_id} has no subscriptions")
                        continue
                
                    logger.debug(f"👤 User {user.telegram_id}: checking {len(user_subs)} subscriptions")
                
                    for user_sub in user_subs:
                        try:
                            subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
                            if not subscription:
                                logger.warning(f"⚠️ Subscription plan {user_sub.subscription_id} not found")
                                continue
                        
                            expires_at_utc = user_sub.expires_at
                            if expires_at_utc.tzinfo is None:
                                expires_at_utc = expires_at_utc.replace(tzinfo=None)
                            else:
                                expires_at_utc = expires_at_utc.astimezone(timezone.utc).replace(tzinfo=None)
                        
                            time_diff = expires_at_utc - now_utc
                            hours_left = time_diff.total_seconds() / 3600
                            days_left = int(hours_left / 24)
                            
                            logger.debug(f"📋 Subscription '{subscription.name}': "
                                       f"expires_at={expires_at_utc}, "
                                       f"now_utc={now_utc}, "
                                       f"hours_left={hours_left:.1f}, "
                                       f"days_left={days_left}, "
                                       f"threshold={self.config.MONITOR_WARNING_DAYS}")
                        
                            if subscription.is_trial:
                                logger.debug(f"⭐️ Skipping trial subscription '{subscription.name}'")
                                continue
                        
                            if getattr(subscription, 'is_imported', False) or subscription.name == "Старая подписка":
                                logger.debug(f"⭐️ Skipping imported subscription '{subscription.name}'")
                                continue
                        
                            should_warn = (
                                user_sub.is_active and 
                                days_left <= self.config.MONITOR_WARNING_DAYS and 
                                hours_left > 0
                            )
                        
                            if should_warn:
                                logger.info(f"⚠️ Sending warning to user {user.telegram_id}: "
                                          f"subscription '{subscription.name}' expires in {days_left} days "
                                          f"({hours_left:.1f} hours)")
                            
                                try:
                                    await self._send_expiry_warning(user, user_sub)
                                    warnings_sent += 1
                                    logger.info(f"✅ Warning sent successfully to user {user.telegram_id}")
                                except Exception as warning_error:
                                    logger.error(f"❌ Failed to send warning to user {user.telegram_id}: {warning_error}")
                                    errors_count += 1
                            elif hours_left <= 0:
                                logger.debug(f"❌ Subscription '{subscription.name}' already expired {abs(hours_left):.1f} hours ago")
                            else:
                                logger.debug(f"✅ Subscription '{subscription.name}' is OK (expires in {days_left} days)")
                    
                        except Exception as sub_error:
                            logger.error(f"❌ Error checking subscription {user_sub.id}: {sub_error}")
                            errors_count += 1
                    
                except Exception as user_error:
                    logger.error(f"❌ Error checking subscriptions for user {user.telegram_id}: {user_error}")
                    errors_count += 1
        
            logger.info(f"📊 Check completed: {total_subscriptions} subscriptions checked, {warnings_sent} warnings sent, {errors_count} errors")
            return warnings_sent
                
        except Exception as e:
            logger.error(f"❌ Critical error in check_expiring_subscriptions: {e}", exc_info=True)
            return 0
            
    async def _send_expiry_warning(self, user, user_subscription):
        try:
            if not self.bot:
                logger.error("❌ Bot instance is None, cannot send warning")
                return
            
            now_utc = datetime.utcnow()
            expires_at_utc = user_subscription.expires_at
            if expires_at_utc.tzinfo is None:
                expires_at_utc = expires_at_utc.replace(tzinfo=None)
            else:
                expires_at_utc = expires_at_utc.astimezone(timezone.utc).replace(tzinfo=None)
        
            time_diff = expires_at_utc - now_utc
            hours_left = time_diff.total_seconds() / 3600
            days_left = int(hours_left / 24)
        
            subscription = await self.db.get_subscription_by_id(user_subscription.subscription_id)
            if not subscription:
                logger.warning(f"Subscription {user_subscription.subscription_id} not found")
                return
        
            message = self._format_expiry_message_with_action(subscription.name, days_left, user.language)
        
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            from translations import t
        
            keyboard = None
            if hours_left > 0: 
                is_imported = (getattr(subscription, 'is_imported', False) or 
                              subscription.name == "Старая подписка" or
                              (subscription.description and 'импорт' in subscription.description.lower()))
            
                if not is_imported:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=t('extend_subscription_btn', user.language), callback_data=f"extend_sub_{user_subscription.id}")],
                        [InlineKeyboardButton(text=t('my_subscriptions_btn', user.language), callback_data="my_subscriptions")]
                    ])
                else:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=t('my_subscriptions_btn', user.language), callback_data="my_subscriptions")],
                        [InlineKeyboardButton(text=t('buy_new_subscription_btn', user.language), callback_data="buy_subscription")]
                    ])
            else:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=t('restore_subscription_btn', user.language), callback_data=f"extend_sub_{user_subscription.id}")],
                    [InlineKeyboardButton(text=t('my_subscriptions_btn', user.language), callback_data="my_subscriptions")]
                ])
        
            await self.bot.send_message(user.telegram_id, message, reply_markup=keyboard)
        
            if days_left == 0:
                logger.info(f"✅ URGENT expiry warning sent to user {user.telegram_id} for subscription '{subscription.name}' (expires today, {hours_left:.1f} hours left)")
            elif days_left > 0:
                logger.info(f"✅ Expiry warning sent to user {user.telegram_id} for subscription '{subscription.name}' (expires in {days_left} days)")
            else:
                logger.info(f"✅ Expiry notification sent to user {user.telegram_id} for subscription '{subscription.name}' (already expired)")
        
        except Exception as e:
            logger.error(f"❌ Error sending expiry warning to user {user.telegram_id}: {e}", exc_info=True)
            raise
            
    def _format_expiry_message_with_action(self, subscription_name: str, days_left: int, language: str = 'ru') -> str:
        from translations import t
    
        if days_left < 0:
            return t('subscription_expired_message', language, name=subscription_name)
        elif days_left == 0:
            return t('subscription_expires_today', language, name=subscription_name)
        elif days_left == 1:
            return t('subscription_expires_tomorrow', language, name=subscription_name)
        elif days_left == 2:
            return t('subscription_expires_day_after_tomorrow', language, name=subscription_name, days=days_left)
        else:
            return t('subscription_expires_in_days', language, name=subscription_name, days=days_left)
    
    async def force_daily_check(self):
        try:
            logger.info("🚀 Starting forced daily check and cleanup")
            
            logger.info("📢 Sending expiry warnings...")
            warnings_sent = await self._check_expiring_subscriptions()
            logger.info(f"📢 Sent {warnings_sent} expiry warnings")
            
            logger.info("🆓 Checking expired trial subscriptions...")
            trial_notifications = await self._check_expired_trial_subscriptions()
            logger.info(f"🆓 Sent {trial_notifications} trial expiry notifications")
            
            logger.info("🔥 Deactivating expired subscriptions...")
            deactivated_count = await self.deactivate_expired_subscriptions()
            logger.info(f"🔥 Deactivated {deactivated_count} expired subscriptions")
            
            deleted_trials = 0
            deleted_regular = 0
            if getattr(self.config, 'AUTO_DELETE_ENABLED', False):
                logger.info("🗑️ Auto-deletion enabled, deleting expired subscriptions...")
                
                trial_result = await self.delete_expired_trial_subscriptions()
                deleted_trials = trial_result.get('deleted_from_db', 0)
                
                regular_result = await self.delete_expired_regular_subscriptions()
                deleted_regular = regular_result.get('deleted_from_db', 0)
                
                logger.info(f"🗑️ Auto-deleted {deleted_trials} trial and {deleted_regular} regular subscriptions")
            
            logger.info("📩 Sending final expiry notifications...")
            await self._send_final_expiry_notifications()
            logger.info("📩 Final notifications sent")
            
            logger.info(f"✅ Daily check completed successfully. Warnings: {warnings_sent}, Trial notifications: {trial_notifications}, Deactivated: {deactivated_count}, "
                       f"Deleted trials: {deleted_trials}, Deleted regular: {deleted_regular}")
            return deactivated_count
            
        except Exception as e:
            logger.error(f"❌ Error in force_daily_check: {e}", exc_info=True)
            return 0
            
    async def deactivate_expired_subscriptions(self) -> int:
        try:
            count = 0
            now_utc = datetime.utcnow()
            logger.info(f"🔍 Checking for expired subscriptions (current UTC: {now_utc})")
        
            all_users = await self.db.get_all_users()
        
            logger.info(f"🔍 Checking {len(all_users)} users for expired subscriptions")
        
            for user in all_users:
                try:
                    user_subs = await self.db.get_user_subscriptions(user.telegram_id)
                
                    for user_sub in user_subs:
                        if user_sub.is_active:
                            expires_at_utc = user_sub.expires_at
                            if expires_at_utc.tzinfo is None:
                                expires_at_utc = expires_at_utc.replace(tzinfo=None)
                            else:
                                expires_at_utc = expires_at_utc.astimezone(timezone.utc).replace(tzinfo=None)
                        
                            time_diff = expires_at_utc - now_utc
                            hours_until_expiry = time_diff.total_seconds() / 3600
                        
                            if hours_until_expiry < -1.0:
                                subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
                                subscription_name = subscription.name if subscription else f"ID:{user_sub.subscription_id}"
                            
                                logger.info(f"❌ Deactivating truly expired subscription '{subscription_name}' "
                                          f"for user {user.telegram_id} (expired {abs(hours_until_expiry):.1f} hours ago)")
                            
                                user_sub.is_active = False
                                success = await self.db.update_user_subscription(user_sub)
                            
                                if success:
                                    count += 1
                                    logger.info(f"✅ Successfully deactivated subscription '{subscription_name}' for user {user.telegram_id}")
                                
                                    if self.api and user_sub.short_uuid:
                                        try:
                                            user_data = await self.api.get_user_by_short_uuid(user_sub.short_uuid)
                                            if user_data and user_data.get('uuid'):
                                                await self.api.update_user(user_data['uuid'], {'status': 'EXPIRED'})
                                                logger.debug(f"🔥 Also deactivated user {user_data['uuid']} in RemnaWave")
                                        except Exception as api_error:
                                            logger.warning(f"⚠️ Could not deactivate user in RemnaWave: {api_error}")
                                else:
                                    logger.error(f"❌ Failed to deactivate subscription {user_sub.id} in database")
                            else:
                                logger.debug(f"✅ Subscription expires in {hours_until_expiry:.1f} hours - not deactivating yet")
                except Exception as user_error:
                    logger.error(f"❌ Error processing user {user.telegram_id}: {user_error}")
        
            logger.info(f"✅ Deactivation completed: {count} subscriptions deactivated")
            return count
        
        except Exception as e:
            logger.error(f"❌ Error deactivating expired subscriptions: {e}", exc_info=True)
            return 0
            
    async def _send_final_expiry_notifications(self):
        try:
            all_users = await self.db.get_all_users()
            notifications_sent = 0
            now_utc = datetime.utcnow()
        
            logger.info(f"📩 Checking for subscriptions that expired recently (current UTC: {now_utc})")
        
            for user in all_users:
                try:
                    user_subs = await self.db.get_user_subscriptions(user.telegram_id)
                
                    for user_sub in user_subs:
                        expires_at_utc = user_sub.expires_at
                        if expires_at_utc.tzinfo is None:
                            expires_at_utc = expires_at_utc.replace(tzinfo=None)
                        else:
                            expires_at_utc = expires_at_utc.astimezone(timezone.utc).replace(tzinfo=None)
                    
                        time_since_expiry = now_utc - expires_at_utc
                        hours_since_expiry = time_since_expiry.total_seconds() / 3600
                    
                        if 2 <= hours_since_expiry <= 24:
                            subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
                            if subscription and not subscription.is_trial and not getattr(subscription, 'is_imported', False):
                                message = self._format_expiry_message_with_action(subscription.name, 0, user.language)
                            
                                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="🔄 Восстановить подписку", callback_data=f"extend_sub_{user_sub.id}")],
                                    [InlineKeyboardButton(text="📋 Мои подписки", callback_data="my_subscriptions")]
                                ])
                            
                                await self.bot.send_message(user.telegram_id, message, reply_markup=keyboard)
                                notifications_sent += 1
                                logger.info(f"📩 Sent final expiry notification for subscription '{subscription.name}' to user {user.telegram_id} (expired {hours_since_expiry:.1f} hours ago)")
                except Exception as e:
                    logger.error(f"❌ Error sending final notification to user {user.telegram_id}: {e}")
        
            if notifications_sent > 0:
                logger.info(f"📩 Sent {notifications_sent} final expiry notifications")
                    
        except Exception as e:
            logger.error(f"❌ Error sending final expiry notifications: {e}", exc_info=True)
            
    async def check_single_user(self, user_id: int):
        try:
            results = []
    
            logger.info(f"🧪 Testing monitor for user {user_id}")
    
            user = await self.db.get_user_by_telegram_id(user_id)
            if not user:
                results.append({
                    'success': False,
                    'message': f'User {user_id} not found in database',
                    'error': None
                })
                return results
    
            user_subs = await self.db.get_user_subscriptions(user_id)
    
            if not user_subs:
                results.append({
                    'success': True,
                    'message': f'User {user_id} has no subscriptions',
                    'error': None
                })
                return results
    
            logger.info(f"🧪 Found {len(user_subs)} subscriptions for user {user_id}")
            
            now_utc = datetime.utcnow()
    
            for user_sub in user_subs:
                try:
                    subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
            
                    if not subscription:
                        results.append({
                            'success': False,
                            'message': f'Subscription plan {user_sub.subscription_id} not found',
                            'error': None
                        })
                        continue
            
                    expires_at_utc = user_sub.expires_at
                    if expires_at_utc.tzinfo is None:
                        expires_at_utc = expires_at_utc.replace(tzinfo=None)
                    else:
                        expires_at_utc = expires_at_utc.astimezone(timezone.utc).replace(tzinfo=None)
            
                    time_diff = expires_at_utc - now_utc
                    hours_left = time_diff.total_seconds() / 3600
                    days_left = int(hours_left / 24)
            
                    if subscription.is_trial:
                        hours_since_expiry = -hours_left
                        if 1 <= hours_since_expiry <= 24 and user_sub.is_active:
                            test_message = f"🧪 [ТЕСТОВОЕ УВЕДОМЛЕНИЕ]\n\n🆓 Ваша триальная подписка '{subscription.name}' истекла! Купите новый тариф чтобы продолжить использование VPN."
                            
                            if self.bot:
                                try:
                                    await self.bot.send_message(user_id, test_message)
                                    results.append({
                                        'success': True,
                                        'message': f'✅ Sent test trial expiry notification for "{subscription.name}" (expired {hours_since_expiry:.1f} hours ago)',
                                        'error': None
                                    })
                                except Exception as send_error:
                                    results.append({
                                        'success': False,
                                        'message': f'❌ Failed to send test trial notification for "{subscription.name}"',
                                        'error': str(send_error)
                                    })
                        else:
                            results.append({
                                'success': True,
                                'message': f'Trial subscription "{subscription.name}" - no notification needed (expired {hours_since_expiry:.1f} hours ago)',
                                'error': None
                            })
                        continue
                
                    if getattr(subscription, 'is_imported', False) or subscription.name == "Старая подписка":
                        results.append({
                            'success': True,
                            'message': f'Imported subscription "{subscription.name}" skipped (cannot be renewed)',
                            'error': None
                        })
                        continue
            
                    if days_left <= self.config.MONITOR_WARNING_DAYS and hours_left > 0:
                        message = self._format_expiry_message_with_action(subscription.name, days_left, user.language)
                        test_message = f"🧪 [ТЕСТОВОЕ УВЕДОМЛЕНИЕ]\n\n{message}"
                
                        if self.bot:
                            try:
                                await self.bot.send_message(user_id, test_message)
                                logger.info(f"✅ Test warning sent to user {user_id}")
                            
                                results.append({
                                    'success': True,
                                    'message': f'✅ Sent test warning for subscription "{subscription.name}" (expires in {days_left} days / {hours_left:.1f} hours)',
                                    'error': None
                                })
                            except Exception as send_error:
                                logger.error(f"❌ Failed to send test message: {send_error}")
                                results.append({
                                    'success': False,
                                    'message': f'❌ Failed to send test warning for "{subscription.name}"',
                                    'error': str(send_error)
                                })
                        else:
                            logger.error("❌ Bot instance is None")
                            results.append({
                                'success': False,
                                'message': f'❌ Bot instance unavailable for "{subscription.name}"',
                                'error': 'Bot is None'
                            })
                    elif hours_left <= 0:
                        results.append({
                            'success': True,
                            'message': f'❌ Subscription "{subscription.name}" already expired {abs(hours_left):.1f} hours ago',
                            'error': None
                        })
                    else:
                        results.append({
                            'success': True,
                            'message': f'✅ Subscription "{subscription.name}" is OK (expires in {days_left} days / {hours_left:.1f} hours, warning threshold: {self.config.MONITOR_WARNING_DAYS} days)',
                            'error': None
                        })
                
                except Exception as e:
                    logger.error(f"❌ Error checking subscription {user_sub.id}: {e}")
                    results.append({
                        'success': False,
                        'message': f'Error checking subscription ID {user_sub.id}',
                        'error': str(e)
                    })
    
            return results
    
        except Exception as e:
            logger.error(f"❌ Error in check_single_user: {e}", exc_info=True)
            return [{
                'success': False,
                'message': f'Critical error checking user {user_id}',
                'error': str(e)
            }]
            
    async def get_service_status(self) -> dict:
        monitor_running = self._monitor_task is not None and not self._monitor_task.done()
        daily_running = self._daily_task is not None and not self._daily_task.done()
        
        return {
            'is_running': self.is_running and (monitor_running or daily_running),
            'monitor_enabled': getattr(self.config, 'MONITOR_ENABLED', True),
            'check_interval': self.config.MONITOR_CHECK_INTERVAL,
            'daily_check_hour': self.config.MONITOR_DAILY_CHECK_HOUR,
            'warning_days': self.config.MONITOR_WARNING_DAYS,
            'delete_trial_days': getattr(self.config, 'DELETE_EXPIRED_TRIAL_DAYS', 1),
            'delete_regular_days': getattr(self.config, 'DELETE_EXPIRED_REGULAR_DAYS', 7),
            'auto_delete_enabled': getattr(self.config, 'AUTO_DELETE_ENABLED', False),
            'last_check': self.last_check_time.strftime("%Y-%m-%d %H:%M:%S") if self.last_check_time else None,
            'has_tasks': {
                'monitor_task': monitor_running,
                'daily_task': daily_running
            },
            'task_status': {
                'monitor_task': 'running' if monitor_running else 'stopped',
                'daily_task': 'running' if daily_running else 'stopped'
            }
        }

async def create_subscription_monitor(bot, db: Database, config, api: RemnaWaveAPI = None) -> SubscriptionMonitorService:
    logger.info("🏭 Creating subscription monitor service...")
    service = SubscriptionMonitorService(bot, db, config, api)
    logger.info("✅ Subscription monitor service created")
    return service
