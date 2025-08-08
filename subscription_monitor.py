import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from database import Database
from remnawave_api import RemnaWaveAPI

logger = logging.getLogger(__name__)

class SubscriptionMonitorService:
    """Service for monitoring subscriptions and sending notifications"""
    
    def __init__(self, bot, db: Database, config, api: RemnaWaveAPI = None):
        self.bot = bot
        self.db = db
        self.config = config
        self.api = api
        self.is_running = False
        self._monitor_task = None
        self._daily_task = None
        
    async def start(self):
        """Start the monitoring service"""
        if self.is_running:
            logger.warning("Monitor service is already running")
            return
        
        self.is_running = True
        
        # Start periodic monitoring task
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        
        # Start daily cleanup task
        self._daily_task = asyncio.create_task(self._daily_loop())
        
        logger.info("Subscription monitor service started")
        
    async def stop(self):
        """Stop the monitoring service"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        # Cancel tasks
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        if self._daily_task:
            self._daily_task.cancel()
            try:
                await self._daily_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Subscription monitor service stopped")
        
    async def _monitor_loop(self):
        """Main monitoring loop"""
        while self.is_running:
            try:
                await self._check_expiring_subscriptions()
                await asyncio.sleep(self.config.MONITOR_CHECK_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retry
                
    async def _daily_loop(self):
        """Daily cleanup loop"""
        while self.is_running:
            try:
                now = datetime.now()
                
                # Wait until the specified hour
                if now.hour == self.config.MONITOR_DAILY_CHECK_HOUR:
                    await self.force_daily_check()
                    
                    # Wait until next day
                    tomorrow = now.replace(hour=self.config.MONITOR_DAILY_CHECK_HOUR, minute=0, second=0, microsecond=0) + timedelta(days=1)
                    sleep_time = (tomorrow - now).total_seconds()
                else:
                    # Calculate time until next check
                    target_time = now.replace(hour=self.config.MONITOR_DAILY_CHECK_HOUR, minute=0, second=0, microsecond=0)
                    if target_time < now:
                        target_time += timedelta(days=1)
                    sleep_time = (target_time - now).total_seconds()
                
                await asyncio.sleep(min(sleep_time, 3600))  # Check at least every hour
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in daily loop: {e}")
                await asyncio.sleep(3600)  # Wait 1 hour before retry
                
    async def _check_expiring_subscriptions(self):
        """Check for expiring subscriptions and send warnings"""
        try:
            # Get all users
            all_users = await self.db.get_all_users()
            
            for user in all_users:
                try:
                    # Get expiring subscriptions for this user
                    expiring_subs = await self.db.get_expiring_subscriptions(
                        user.telegram_id, 
                        self.config.MONITOR_WARNING_DAYS
                    )
                    
                    for user_sub in expiring_subs:
                        await self._send_expiry_warning(user, user_sub)
                        
                except Exception as e:
                    logger.error(f"Error checking subscriptions for user {user.telegram_id}: {e}")
                    
        except Exception as e:
            logger.error(f"Error in check_expiring_subscriptions: {e}")
            
    async def _send_expiry_warning(self, user, user_subscription):
        """Send expiry warning to user"""
        try:
            days_left = (user_subscription.expires_at - datetime.utcnow()).days
            
            subscription = await self.db.get_subscription_by_id(user_subscription.subscription_id)
            if not subscription:
                return
            
            # Don't send warnings for trial subscriptions
            if subscription.is_trial:
                return
            
            message = self._format_expiry_message(subscription.name, days_left, user.language)
            
            await self.bot.send_message(user.telegram_id, message)
            logger.info(f"Sent expiry warning to user {user.telegram_id} for subscription {subscription.name}")
            
        except Exception as e:
            logger.error(f"Error sending expiry warning: {e}")
            
    def _format_expiry_message(self, subscription_name: str, days_left: int, language: str = 'ru') -> str:
        """Format expiry warning message"""
        if language == 'ru':
            if days_left <= 0:
                return f"⚠️ Ваша подписка '{subscription_name}' истекла!\n\nДля продления перейдите в раздел 'Мои подписки'."
            elif days_left == 1:
                return f"⚠️ Ваша подписка '{subscription_name}' истекает завтра!\n\nНе забудьте продлить её в разделе 'Мои подписки'."
            else:
                return f"⚠️ Ваша подписка '{subscription_name}' истекает через {days_left} дн.!\n\nРекомендуем продлить её заранее в разделе 'Мои подписки'."
        else:
            if days_left <= 0:
                return f"⚠️ Your subscription '{subscription_name}' has expired!\n\nTo renew, go to 'My Subscriptions'."
            elif days_left == 1:
                return f"⚠️ Your subscription '{subscription_name}' expires tomorrow!\n\nDon't forget to renew it in 'My Subscriptions'."
            else:
                return f"⚠️ Your subscription '{subscription_name}' expires in {days_left} days!\n\nWe recommend renewing it in advance in 'My Subscriptions'."
    
    async def force_daily_check(self):
        """Force daily check and cleanup"""
        try:
            logger.info("Starting daily check and cleanup")
            
            # Deactivate expired subscriptions
            deactivated_count = await self.deactivate_expired_subscriptions()
            
            # Send final expiry notifications
            await self._send_final_expiry_notifications()
            
            logger.info(f"Daily check completed. Deactivated {deactivated_count} expired subscriptions")
            
        except Exception as e:
            logger.error(f"Error in force_daily_check: {e}")
            
    async def deactivate_expired_subscriptions(self) -> int:
        """Deactivate expired subscriptions"""
        try:
            count = 0
            all_users = await self.db.get_all_users()
            
            for user in all_users:
                user_subs = await self.db.get_user_subscriptions(user.telegram_id)
                
                for user_sub in user_subs:
                    if user_sub.is_active and user_sub.expires_at < datetime.utcnow():
                        user_sub.is_active = False
                        await self.db.update_user_subscription(user_sub)
                        count += 1
                        
                        # Also try to deactivate in RemnaWave if possible
                        if self.api and user_sub.short_uuid:
                            try:
                                user_data = await self.api.get_user_by_short_uuid(user_sub.short_uuid)
                                if user_data and user_data.get('uuid'):
                                    await self.api.update_user(user_data['uuid'], {'status': 'EXPIRED'})
                            except Exception as e:
                                logger.warning(f"Could not deactivate user in RemnaWave: {e}")
            
            return count
            
        except Exception as e:
            logger.error(f"Error deactivating expired subscriptions: {e}")
            return 0
            
    async def _send_final_expiry_notifications(self):
        """Send final notifications for just-expired subscriptions"""
        try:
            # Get subscriptions that expired today
            all_users = await self.db.get_all_users()
            
            for user in all_users:
                user_subs = await self.db.get_user_subscriptions(user.telegram_id)
                
                for user_sub in user_subs:
                    # Check if subscription expired today (within last 24 hours)
                    time_since_expiry = datetime.utcnow() - user_sub.expires_at
                    
                    if (time_since_expiry.total_seconds() > 0 and 
                        time_since_expiry.total_seconds() <= 86400):  # 24 hours
                        
                        subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
                        if subscription and not subscription.is_trial:
                            message = self._format_expiry_message(subscription.name, 0, user.language)
                            await self.bot.send_message(user.telegram_id, message)
                            
        except Exception as e:
            logger.error(f"Error sending final expiry notifications: {e}")
            
    async def check_single_user(self, user_id: int):
        """Check subscriptions for a single user (for testing)"""
        try:
            results = []
            
            user = await self.db.get_user_by_telegram_id(user_id)
            if not user:
                results.append({
                    'success': False,
                    'message': f'User {user_id} not found',
                    'error': None
                })
                return results
            
            # Get user subscriptions
            user_subs = await self.db.get_user_subscriptions(user_id)
            
            if not user_subs:
                results.append({
                    'success': True,
                    'message': f'User {user_id} has no subscriptions',
                    'error': None
                })
                return results
            
            for user_sub in user_subs:
                try:
                    subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
                    
                    days_left = (user_sub.expires_at - datetime.utcnow()).days
                    
                    if days_left <= self.config.MONITOR_WARNING_DAYS:
                        # Send test notification
                        message = self._format_expiry_message(subscription.name, days_left, user.language)
                        await self.bot.send_message(user_id, f"[ТЕСТ] {message}")
                        
                        results.append({
                            'success': True,
                            'message': f'Sent warning for subscription "{subscription.name}" (expires in {days_left} days)',
                            'error': None
                        })
                    else:
                        results.append({
                            'success': True,
                            'message': f'Subscription "{subscription.name}" is OK (expires in {days_left} days)',
                            'error': None
                        })
                        
                except Exception as e:
                    results.append({
                        'success': False,
                        'message': f'Error checking subscription ID {user_sub.id}',
                        'error': str(e)
                    })
            
            return results
            
        except Exception as e:
            return [{
                'success': False,
                'message': f'Error checking user {user_id}',
                'error': str(e)
            }]
            
    async def get_service_status(self) -> dict:
        """Get service status information"""
        return {
            'is_running': self.is_running,
            'check_interval': self.config.MONITOR_CHECK_INTERVAL,
            'daily_check_hour': self.config.MONITOR_DAILY_CHECK_HOUR,
            'warning_days': self.config.MONITOR_WARNING_DAYS,
            'last_check': datetime.now().strftime("%Y-%m-%d %H:%M:%S") if self.is_running else None
        }

async def create_subscription_monitor(bot, db: Database, config, api: RemnaWaveAPI = None) -> SubscriptionMonitorService:
    """Create and return subscription monitor service"""
    return SubscriptionMonitorService(bot, db, config, api)
