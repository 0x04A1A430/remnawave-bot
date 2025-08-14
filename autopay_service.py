import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Optional
from database import Database, UserSubscription, User, Subscription
from remnawave_api import RemnaWaveAPI
from referral_utils import process_referral_rewards

logger = logging.getLogger(__name__)

class AutoPayService:
    
    def __init__(self, db: Database, api: Optional[RemnaWaveAPI] = None, bot=None):
        self.db = db
        self.api = api
        self.bot = bot
        self.is_running = False
        self.check_task = None
        
    async def start(self):
        if self.is_running:
            logger.warning("AutoPay service is already running")
            return
            
        self.is_running = True
        logger.info("🔄 Starting AutoPay service...")
        
        self.check_task = asyncio.create_task(self._periodic_check())
        
    async def stop(self):
        if not self.is_running:
            return
            
        self.is_running = False
        logger.info("⏹ Stopping AutoPay service...")
        
        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                pass
                
    async def _periodic_check(self):
        while self.is_running:
            try:
                await self.process_autopayments()
                # Проверяем каждые 30 минут
                await asyncio.sleep(1800)
            except asyncio.CancelledError:
                logger.info("AutoPay periodic check cancelled")
                break
            except Exception as e:
                logger.error(f"Error in AutoPay periodic check: {e}")
                await asyncio.sleep(300) 
                
    async def process_autopayments(self) -> dict:
        logger.info("🔄 Processing autopayments...")
        
        try:
            subscriptions_to_pay = await self.db.get_subscriptions_for_autopay()
            
            if not subscriptions_to_pay:
                logger.info("No subscriptions ready for autopay")
                return {
                    'processed': 0,
                    'successful': 0,
                    'failed': 0,
                    'insufficient_balance': 0,
                    'errors': []
                }
            
            logger.info(f"Found {len(subscriptions_to_pay)} subscriptions for autopay")
            
            stats = {
                'processed': 0,
                'successful': 0,
                'failed': 0,
                'insufficient_balance': 0,
                'errors': []
            }
            
            for user_sub in subscriptions_to_pay:
                try:
                    result = await self._process_single_autopayment(user_sub)
                    stats['processed'] += 1
                    
                    if result['success']:
                        stats['successful'] += 1
                        logger.info(f"✅ Autopay successful for user {user_sub.user_id}, subscription {user_sub.id}")
                    elif result['reason'] == 'insufficient_balance':
                        stats['insufficient_balance'] += 1
                        logger.info(f"💳 Insufficient balance for user {user_sub.user_id}, subscription {user_sub.id}")
                    else:
                        stats['failed'] += 1
                        stats['errors'].append(f"User {user_sub.user_id}: {result['reason']}")
                        logger.warning(f"❌ Autopay failed for user {user_sub.user_id}: {result['reason']}")
                        
                except Exception as e:
                    stats['processed'] += 1
                    stats['failed'] += 1
                    error_msg = f"User {user_sub.user_id}: {str(e)}"
                    stats['errors'].append(error_msg)
                    logger.error(f"Error processing autopay for user {user_sub.user_id}: {e}")
            
            logger.info(f"📊 Autopay processing complete: {stats['successful']} successful, "
                       f"{stats['failed']} failed, {stats['insufficient_balance']} insufficient balance")
            
            return stats
            
        except Exception as e:
            logger.error(f"Error in process_autopayments: {e}")
            return {
                'processed': 0,
                'successful': 0,
                'failed': 0,
                'insufficient_balance': 0,
                'errors': [str(e)]
            }
    
    async def _process_single_autopayment(self, user_sub: UserSubscription) -> dict:
        try:
            user = await self.db.get_user_by_telegram_id(user_sub.user_id)
            if not user:
                return {'success': False, 'reason': 'User not found'}
            
            subscription = await self.db.get_subscription_by_id(user_sub.subscription_id)
            if not subscription:
                return {'success': False, 'reason': 'Subscription plan not found'}
            
            if subscription.is_trial:
                logger.info(f"Skipping autopay for trial subscription: user {user_sub.user_id}")
                return {'success': False, 'reason': 'Trial subscriptions are not eligible for autopay'}
            
            if not user_sub.is_active or not user_sub.auto_pay_enabled:
                return {'success': False, 'reason': 'Subscription inactive or autopay disabled'}
            
            if user.balance < subscription.price:
                await self._notify_insufficient_balance(user, subscription, user_sub)
                return {'success': False, 'reason': 'insufficient_balance'}
            
            return await self._execute_autopayment(user, subscription, user_sub)
            
        except Exception as e:
            logger.error(f"Error in _process_single_autopayment: {e}")
            return {'success': False, 'reason': str(e)}
    
    async def _execute_autopayment(self, user: User, subscription: Subscription, user_sub: UserSubscription) -> dict:
        try:
            user.balance -= subscription.price
            await self.db.update_user(user)
            
            now = datetime.utcnow()
            if user_sub.expires_at > now:
                new_expiry = user_sub.expires_at + timedelta(days=subscription.duration_days)
            else:
                new_expiry = now + timedelta(days=subscription.duration_days)
            
            user_sub.expires_at = new_expiry
            user_sub.is_active = True
            await self.db.update_user_subscription(user_sub)
            
            if self.api and user_sub.short_uuid:
                try:
                    remna_user_details = await self.api.get_user_by_short_uuid(user_sub.short_uuid)
                    if remna_user_details:
                        user_uuid = remna_user_details.get('uuid')
                        if user_uuid:
                            expiry_str = new_expiry.isoformat() + 'Z'
                            update_data = {
                                'enable': True,
                                'expireAt': expiry_str
                            }
                            await self.api.update_user(user_uuid, update_data)
                            logger.info(f"Updated RemnaWave expiry for user {user_sub.user_id}")
                except Exception as e:
                    logger.warning(f"Failed to update RemnaWave expiry: {e}")
            
            payment = await self.db.create_payment(
                user_id=user_sub.user_id,
                amount=-subscription.price,
                payment_type='autopay',
                description=f'Автоплатеж: {subscription.name}',
                status='completed'
            )
            
            if self.bot:
                try:
                    await process_referral_rewards(
                        user_sub.user_id,
                        subscription.price,
                        payment.id,
                        self.db,
                        self.bot,
                        payment_type='autopay'
                    )
                except Exception as e:
                    logger.warning(f"Failed to process referral rewards for autopay: {e}")
            
            await self._notify_successful_autopay(user, subscription, user_sub, new_expiry)
            
            return {'success': True, 'reason': 'Payment processed successfully'}
            
        except Exception as e:
            logger.error(f"Error executing autopayment: {e}")
            try:
                user.balance += subscription.price
                await self.db.update_user(user)
            except:
                pass
            return {'success': False, 'reason': str(e)}
    
    async def _notify_successful_autopay(self, user: User, subscription: Subscription, 
                                       user_sub: UserSubscription, new_expiry: datetime):
        if not self.bot:
            return
        
        try:
            from utils import format_datetime
            
            text = f"✅ Автоматическое продление подписки\n\n"
            text += f"📋 Подписка: {subscription.name}\n"
            text += f"💰 Списано: {subscription.price} руб.\n"
            text += f"📅 Продлено до: {format_datetime(new_expiry, user.language)}\n"
            text += f"💳 Остаток на балансе: {user.balance} руб.\n\n"
            text += f"🔄 Следующее продление произойдет автоматически за {user_sub.auto_pay_days_before} дн. до истечения.\n\n"
            text += f"ℹ️ Для отключения автоплатежа перейдите в 'Мои подписки' → выберите подписку"
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои подписки", callback_data="my_subscriptions")],
                [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")]
            ])
            
            await self.bot.send_message(
                user.telegram_id,
                text,
                reply_markup=keyboard
            )
            
        except Exception as e:
            logger.error(f"Failed to send autopay success notification: {e}")
    
    async def _notify_insufficient_balance(self, user: User, subscription: Subscription, user_sub: UserSubscription):
        if not self.bot:
            return
        
        try:
            from utils import format_datetime
            
            needed = subscription.price - user.balance
            days_left = (user_sub.expires_at - datetime.utcnow()).days
            
            text = f"⚠️ Не удалось автоматически продлить подписку\n\n"
            text += f"📋 Подписка: {subscription.name}\n"
            text += f"💰 Нужно для продления: {subscription.price} руб.\n"
            text += f"💳 Ваш баланс: {user.balance} руб.\n"
            text += f"💸 Недостает: {needed} руб.\n\n"
            text += f"📅 Подписка истекает: {format_datetime(user_sub.expires_at, user.language)}\n"
            text += f"⏰ Осталось дней: {days_left}\n\n"
            text += f"💡 Пополните баланс для автоматического продления"
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="topup_balance")],
                [InlineKeyboardButton(text="📋 Мои подписки", callback_data="my_subscriptions")]
            ])
            
            await self.bot.send_message(
                user.telegram_id,
                text,
                reply_markup=keyboard
            )
            
        except Exception as e:
            logger.error(f"Failed to send insufficient balance notification: {e}")
    
    async def get_service_status(self) -> dict:
        return {
            'is_running': self.is_running,
            'check_interval': 1800, 
            'has_api': self.api is not None,
            'has_bot': self.bot is not None
        }
