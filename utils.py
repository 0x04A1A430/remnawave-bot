import logging
import secrets
import string
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict, Any
from database import Database
from database import ReferralProgram, ReferralEarning  # ДОБАВЛЕНО: импорт моделей

logger = logging.getLogger(__name__)

def is_valid_amount(text: str) -> Tuple[bool, float]:
    """Validate amount input"""
    try:
        # Remove spaces and replace comma with dot
        text = text.strip().replace(' ', '').replace(',', '.')
        
        amount = float(text)
        
        # Check if amount is positive and reasonable
        if amount <= 0:
            return False, 0.0
        
        if amount > 1000000:  # Max 1M rubles
            return False, 0.0
        
        # Round to 2 decimal places
        amount = round(amount, 2)
        
        return True, amount
        
    except (ValueError, TypeError):
        return False, 0.0

def validate_promocode_format(code: str) -> bool:
    """Validate promocode format"""
    if not code:
        return False
    
    # Remove spaces and convert to uppercase
    code = code.strip().upper()
    
    # Check length
    if len(code) < 3 or len(code) > 20:
        return False
    
    # Check that code contains only letters and numbers
    if not code.replace('_', '').isalnum():
        return False
    
    return True

def validate_squad_uuid(uuid: str) -> bool:
    """Validate squad UUID format"""
    if not uuid or not isinstance(uuid, str):
        return False
    
    uuid = uuid.strip()
    
    # Basic UUID format validation
    if len(uuid) < 8:
        return False
    
    # UUID should contain only hex characters and hyphens
    allowed_chars = set('0123456789abcdefABCDEF-')
    if not all(c in allowed_chars for c in uuid):
        return False
    
    return True

def parse_telegram_id(text: str) -> Optional[int]:
    """Parse Telegram ID from text"""
    try:
        # Remove spaces and common prefixes
        text = text.strip().replace(' ', '')
        
        # Remove common prefixes
        if text.startswith('@'):
            text = text[1:]
        
        if text.startswith('id'):
            text = text[2:]
        
        # Try to convert to int
        telegram_id = int(text)
        
        # Validate that it's a reasonable Telegram ID
        if telegram_id <= 0 or telegram_id > 9999999999:  # Max 10 digits
            return None
        
        return telegram_id
        
    except (ValueError, TypeError):
        return None

def generate_username() -> str:
    """Generate random username for RemnaWave"""
    prefix = "user_"
    random_part = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return prefix + random_part

def generate_password() -> str:
    """Generate random password for RemnaWave"""
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))

def calculate_expiry_date(days: int) -> str:
    """Calculate expiry date for subscription"""
    expiry_date = datetime.now() + timedelta(days=days)
    return expiry_date.isoformat() + 'Z'

def format_datetime(dt: datetime, language: str = 'ru') -> str:
    """Format datetime for display"""
    if not dt:
        return "N/A"
    
    if language == 'ru':
        return dt.strftime('%d.%m.%Y %H:%M')
    else:
        return dt.strftime('%Y-%m-%d %H:%M')

def format_date(dt: datetime, language: str = 'ru') -> str:
    """Format date for display"""
    if not dt:
        return "N/A"
    
    if language == 'ru':
        return dt.strftime('%d.%m.%Y')
    else:
        return dt.strftime('%Y-%m-%d')

def format_bytes(bytes_value: int) -> str:
    """Format bytes to human readable format"""
    if bytes_value == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    value = float(bytes_value)
    
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    
    if value >= 100:
        return f"{value:.0f} {units[unit_index]}"
    elif value >= 10:
        return f"{value:.1f} {units[unit_index]}"
    else:
        return f"{value:.2f} {units[unit_index]}"

def format_payment_status(status: str, language: str = 'ru') -> str:
    """Format payment status for display"""
    status_map = {
        'ru': {
            'pending': 'Ожидает',
            'completed': 'Завершен', 
            'cancelled': 'Отменен',
            'failed': 'Ошибка'
        },
        'en': {
            'pending': 'Pending',
            'completed': 'Completed',
            'cancelled': 'Cancelled',
            'failed': 'Failed'
        }
    }
    
    return status_map.get(language, status_map['ru']).get(status, status)

def format_subscription_info(subscription: Dict[str, Any], language: str = 'ru') -> str:
    """Format subscription information for display"""
    text = ""
    
    if language == 'ru':
        text += f"📋 **Подписка: {subscription['name']}**\n\n"
        text += f"💰 Цена: {subscription['price']} руб.\n"
        text += f"⏱ Длительность: {subscription['duration_days']} дн.\n"
        
        if subscription['traffic_limit_gb'] > 0:
            text += f"📊 Лимит трафика: {subscription['traffic_limit_gb']} ГБ\n"
        else:
            text += f"📊 Лимит трафика: Безлимит\n"
        
        if subscription.get('description'):
            text += f"\n📝 Описание:\n{subscription['description']}"
    else:
        text += f"📋 **Subscription: {subscription['name']}**\n\n"
        text += f"💰 Price: ${subscription['price']}\n"
        text += f"⏱ Duration: {subscription['duration_days']} days\n"
        
        if subscription['traffic_limit_gb'] > 0:
            text += f"📊 Traffic limit: {subscription['traffic_limit_gb']} GB\n"
        else:
            text += f"📊 Traffic limit: Unlimited\n"
        
        if subscription.get('description'):
            text += f"\n📝 Description:\n{subscription['description']}"
    
    return text

def format_user_subscription_info(user_sub: Dict[str, Any], subscription: Dict[str, Any], 
                                expires_at: datetime, language: str = 'ru') -> str:
    """Format user subscription information for display - ИСПРАВЛЕНО: добавлены пометки для импортированных"""
    text = ""
    
    if language == 'ru':
        text += f"📋 **{subscription['name']}**\n\n"
        
        # Status
        now = datetime.utcnow()
        if expires_at < now:
            status = "❌ Истекла"
            days_left = 0
        elif not user_sub.get('is_active', True):
            status = "⏸ Приостановлена"
            days_left = (expires_at - now).days
        else:
            days_left = (expires_at - now).days
            status = f"✅ Активна"
        
        text += f"🔘 Статус: {status}\n"
        text += f"📅 Истекает: {format_datetime(expires_at, language)}\n"
        
        if days_left > 0:
            text += f"⏰ Осталось: {days_left} дн.\n"
        
        # Traffic info
        if subscription['traffic_limit_gb'] > 0:
            text += f"📊 Лимит трафика: {subscription['traffic_limit_gb']} ГБ\n"
        else:
            text += f"📊 Лимит трафика: Безлимит\n"
        
        if subscription.get('name') == "Старая подписка" or (subscription.get('description') and 'импорт' in subscription.get('description', '').lower()):
            text += f"\n🔄 Тип: Импортированная из старой системы\n"
            text += f"ℹ️ Продление недоступно"
        
        # Description
        if subscription.get('description') and not ('импорт' in subscription.get('description', '').lower()):
            text += f"\n📝 {subscription['description']}"
    else:
        text += f"📋 **{subscription['name']}**\n\n"
        
        # Status
        now = datetime.utcnow()
        if expires_at < now:
            status = "❌ Expired"
            days_left = 0
        elif not user_sub.get('is_active', True):
            status = "⏸ Suspended"
            days_left = (expires_at - now).days
        else:
            days_left = (expires_at - now).days
            status = f"✅ Active"
        
        text += f"🔘 Status: {status}\n"
        text += f"📅 Expires: {format_datetime(expires_at, language)}\n"
        
        if days_left > 0:
            text += f"⏰ Days left: {days_left}\n"
        
        # Traffic info
        if subscription['traffic_limit_gb'] > 0:
            text += f"📊 Traffic limit: {subscription['traffic_limit_gb']} GB\n"
        else:
            text += f"📊 Traffic limit: Unlimited\n"
        
        if subscription.get('name') == "Старая подписка" or (subscription.get('description') and 'import' in subscription.get('description', '').lower()):
            text += f"\n🔄 Type: Imported from old system\n"
            text += f"ℹ️ Extension not available"
        
        # Description
        if subscription.get('description') and not ('import' in subscription.get('description', '').lower()):
            text += f"\n📝 {subscription['description']}"
    
    return text

def log_user_action(user_id: int, action: str, details: str = ""):
    """Log user action"""
    logger.info(f"USER_ACTION: {user_id} - {action}" + (f" - {details}" if details else ""))

async def process_referral_rewards(user_id: int, amount: float, payment_id: int, db: Database, bot=None):
    """Process referral rewards after successful payment - ИСПРАВЛЕНА СИГНАТУРА"""
    try:
        # Получаем конфигурацию из переменных окружения напрямую
        import os
        
        threshold = float(os.getenv('REFERRAL_THRESHOLD', '300.0'))
        first_reward = float(os.getenv('REFERRAL_FIRST_REWARD', '150.0'))
        referred_bonus = float(os.getenv('REFERRAL_REFERRED_BONUS', '150.0'))
        percentage = float(os.getenv('REFERRAL_PERCENTAGE', '0.25'))
        
        # Проверяем есть ли у пользователя реферер
        referral = await db.get_referral_by_referred_id(user_id)
        
        if not referral:
            logger.debug(f"No referral found for user {user_id}")
            return
        
        # Получаем информацию о пользователе
        user = await db.get_user_by_telegram_id(user_id)
        if not user:
            logger.error(f"User {user_id} not found")
            return
        
        logger.info(f"Processing referral rewards for user {user_id}, amount {amount}, referrer {referral.referrer_id}")
        
        if not referral.first_reward_paid and user.balance >= threshold:
            logger.info(f"Processing first reward for referral {referral.id} (threshold: {threshold}, reward: {first_reward})")
            
            await db.add_balance(referral.referrer_id, first_reward)
            
            # Создаем запись о платеже для реферера
            await db.create_payment(
                user_id=referral.referrer_id,
                amount=first_reward,
                payment_type='referral',
                description=f'Первая награда за реферала ID:{user_id}',
                status='completed'
            )
            
            # Выплачиваем первую награду рефереру (записываем в историю)
            success = await db.create_referral_earning(
                referrer_id=referral.referrer_id,
                referred_id=user_id,
                amount=first_reward,
                earning_type='first_reward',
                related_payment_id=payment_id
            )
            
            if success:
                logger.info(f"First reward paid: {first_reward}₽ to referrer {referral.referrer_id}")
                
                if bot:
                    try:
                        # Уведомляем реферера
                        await bot.send_message(
                            referral.referrer_id,
                            f"🎉 Поздравляем! Ваш реферал пополнил баланс на {threshold}₽+\n\n"
                            f"💰 Вам начислено {first_reward}₽ за приведенного друга!\n"
                            f"Теперь вы будете получать {percentage*100:.0f}% с каждого его следующего платежа."
                        )
                        
                        # Уведомляем самого пользователя
                        await bot.send_message(
                            user_id,
                            f"🎁 Бонус активирован! Вам начислено {referred_bonus}₽ за переход по реферальной ссылке!"
                        )
                        
                        # Добавляем бонус рефералу
                        await db.add_balance(user_id, referred_bonus)
                        await db.create_payment(
                            user_id=user_id,
                            amount=referred_bonus,
                            payment_type='referral',
                            description='Бонус за переход по реферальной ссылке',
                            status='completed'
                        )
                        
                        logger.info(f"Referral bonus notifications sent and balance updated")
                        
                    except Exception as e:
                        logger.error(f"Failed to send referral notifications: {e}")
            else:
                logger.error(f"Failed to create first reward earning")
        
        if amount > 0 and referral.first_reward_paid:  # ИЗМЕНЕНО: добавлено условие first_reward_paid
            percentage_reward = amount * percentage
            
            if percentage_reward >= 0.01:  # Минимум 1 копейка
                await db.add_balance(referral.referrer_id, percentage_reward)
                
                # Создаем запись о платеже для реферера
                await db.create_payment(
                    user_id=referral.referrer_id,
                    amount=percentage_reward,
                    payment_type='referral',
                    description=f'{percentage*100:.0f}% дохода от реферала ID:{user_id}',
                    status='completed'
                )
                
                success = await db.create_referral_earning(
                    referrer_id=referral.referrer_id,
                    referred_id=user_id,
                    amount=percentage_reward,
                    earning_type='percentage',
                    related_payment_id=payment_id
                )
                
                if success:
                    logger.info(f"Percentage reward paid: {percentage_reward:.2f}₽ ({percentage*100:.0f}%) to referrer {referral.referrer_id}")
                    
                    if bot and percentage_reward >= 1.0:  # Уведомляем только если сумма >= 1₽
                        try:
                            await bot.send_message(
                                referral.referrer_id,
                                f"💰 Реферальный доход!\n\n"
                                f"Ваш реферал совершил платеж на {amount:.2f}₽\n"
                                f"Вам начислено: {percentage_reward:.2f}₽ ({percentage*100:.0f}%)"
                            )
                        except Exception as e:
                            logger.error(f"Failed to send percentage notification: {e}")
                else:
                    logger.error(f"Failed to create percentage earning")
        elif amount > 0 and not referral.first_reward_paid:
            logger.info(f"Skipping percentage reward for user {user_id} - first reward not yet paid")
    
    except Exception as e:
        logger.error(f"Error processing referral rewards: {e}")

async def create_referral_from_start_param(user_telegram_id: int, start_param: str, db: Database, bot=None):
    """Create referral relationship from start parameter"""
    try:
        if not start_param.startswith("ref_"):
            return False
        
        referrer_id = int(start_param.replace("ref_", ""))
        
        # Проверяем что это не тот же пользователь
        if referrer_id == user_telegram_id:
            logger.warning(f"User {user_telegram_id} tried to refer themselves")
            return False
        
        # Проверяем что у пользователя еще нет реферера
        existing_referral = await db.get_referral_by_referred_id(user_telegram_id)
        
        if existing_referral:
            logger.info(f"User {user_telegram_id} already has referrer")
            return False
        
        # Генерируем промокод реферера
        referral_code = await db.generate_unique_referral_code(referrer_id)
        
        # Создаем реферальную связь
        referral = await db.create_referral(referrer_id, user_telegram_id, referral_code)
        
        if referral:
            logger.info(f"Created referral: {referrer_id} -> {user_telegram_id} with code {referral_code}")
            
            # Уведомляем реферера
            if bot:
                try:
                    referrer = await db.get_user_by_telegram_id(referrer_id)
                    if referrer:
                        await bot.send_message(
                            referrer_id,
                            f"🎉 Отлично! По вашей ссылке зарегистрировался новый пользователь!\n\n"
                            f"Вы получите 150₽ после того, как он пополнит баланс на 300₽.\n"
                            f"И будете получать 25% с каждого его платежа!"
                        )
                except Exception as e:
                    logger.error(f"Failed to notify referrer: {e}")
            
            return True
        
        return False
        
    except (ValueError, TypeError) as e:
        logger.warning(f"Invalid referral parameter: {start_param}")
        return False
    except Exception as e:
        logger.error(f"Error creating referral from start param: {e}")
        return False

async def create_referral_from_promocode(user_telegram_id: int, referral_code: str, db: Database, bot=None):
    """Create referral relationship from promocode - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    try:
        if not referral_code.startswith("REF"):
            return False
        
        logger.info(f"Trying to use referral code {referral_code} for user {user_telegram_id}")
        
        # Ищем реферера по коду - ИСПРАВЛЕНО: используем правильный импорт
        async with db.session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(ReferralProgram).where(ReferralProgram.referral_code == referral_code)
            )
            referral_record = result.scalar_one_or_none()
            
            if not referral_record:
                logger.warning(f"No referrer found for code {referral_code}")
                return False
            
            referrer_id = referral_record.referrer_id
            
            # Проверяем что пользователь не пытается использовать свой код
            if referrer_id == user_telegram_id:
                logger.warning(f"User {user_telegram_id} tried to use own referral code")
                return False
            
            # Проверяем что у пользователя еще нет реферера
            existing_referral = await db.get_referral_by_referred_id(user_telegram_id)
            
            if existing_referral:
                logger.info(f"User {user_telegram_id} already has referrer")
                return False
            
            # Создаем реферальную связь
            referral = await db.create_referral(referrer_id, user_telegram_id, referral_code)
            
            if referral:
                logger.info(f"Created referral from promocode: {referrer_id} -> {user_telegram_id}")
                
                # Уведомляем реферера
                if bot:
                    try:
                        await bot.send_message(
                            referrer_id,
                            f"🎉 По вашему промокоду {referral_code} зарегистрировался новый пользователь!\n\n"
                            f"Вы получите 150₽ после того, как он пополнит баланс на 300₽."
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify referrer: {e}")
                
                return True
            
            return False
        
    except Exception as e:
        logger.error(f"Error creating referral from promocode: {e}")
        return False

def generate_referral_link(bot_username: str, user_id: int) -> str:
    """Generate referral link for user"""
    if not bot_username:
        return ""
    
    if bot_username.startswith('@'):
        bot_username = bot_username[1:]
    
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

def validate_referral_code(code: str) -> bool:
    """Validate referral code format"""
    if not code or not code.startswith("REF"):
        return False
    if len(code) < 4 or len(code) > 20:
        return False
    return True

def format_referral_stats(stats: dict, lang: str = 'ru') -> str:
    """Format referral statistics for display"""
    if lang == 'ru':
        return (f"👥 Приглашено: {stats['total_referrals']}\n"
                f"✅ Активных: {stats['active_referrals']}\n"
                f"💰 Заработано: {stats['total_earned']:.2f}₽")
    else:
        return (f"👥 Invited: {stats['total_referrals']}\n"
                f"✅ Active: {stats['active_referrals']}\n"
                f"💰 Earned: ${stats['total_earned']:.2f}")
