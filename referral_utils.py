import logging
from datetime import datetime
from database import Database
from database import ReferralProgram, ReferralEarning

logger = logging.getLogger(__name__)

async def process_referral_rewards(user_id: int, amount: float, payment_id: int, db: Database, bot=None):
    try:
        import os
        
        threshold = float(os.getenv('REFERRAL_THRESHOLD', '300.0'))
        first_reward = float(os.getenv('REFERRAL_FIRST_REWARD', '150.0'))
        referred_bonus = float(os.getenv('REFERRAL_REFERRED_BONUS', '150.0'))
        percentage = float(os.getenv('REFERRAL_PERCENTAGE', '0.25'))
        
        referral = await db.get_referral_by_referred_id(user_id)
        
        if not referral:
            logger.debug(f"No referral found for user {user_id}")
            return
        
        user = await db.get_user_by_telegram_id(user_id)
        if not user:
            logger.error(f"User {user_id} not found")
            return
        
        logger.info(f"Processing referral rewards for user {user_id}, amount {amount}, referrer {referral.referrer_id}")
        
        if not referral.first_reward_paid and user.balance >= threshold:
            logger.info(f"Processing first reward for referral {referral.id} (threshold: {threshold}, reward: {first_reward})")
            
            await db.add_balance(referral.referrer_id, first_reward)
            
            await db.create_payment(
                user_id=referral.referrer_id,
                amount=first_reward,
                payment_type='referral',
                description=f'Первая награда за реферала ID:{user_id}',
                status='completed'
            )
            
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
                        await bot.send_message(
                            referral.referrer_id,
                            f"🎉 Поздравляем! Ваш реферал пополнил баланс на {threshold}₽+\n\n"
                            f"💰 Вам начислено {first_reward}₽ за приведенного друга!\n"
                            f"Теперь вы будете получать {percentage*100:.0f}% с каждого его следующего платежа."
                        )
                        
                        await bot.send_message(
                            user_id,
                            f"🎁 Бонус активирован! Вам начислено {referred_bonus}₽ за переход по реферальной ссылке!"
                        )
                        
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
        
        if amount > 0 and referral.first_reward_paid: 
            percentage_reward = amount * percentage
            
            if percentage_reward >= 0.01: 
                await db.add_balance(referral.referrer_id, percentage_reward)
                
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
                    
                    if bot and percentage_reward >= 1.0: 
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
    try:
        if not start_param.startswith("ref_"):
            return False
        
        referrer_id = int(start_param.replace("ref_", ""))
        
        if referrer_id == user_telegram_id:
            logger.warning(f"User {user_telegram_id} tried to refer themselves")
            return False
        
        existing_reverse_referral = await db.get_referral_by_referred_id(referrer_id)
        if existing_reverse_referral and existing_reverse_referral.referrer_id == user_telegram_id:
            logger.warning(f"Mutual referral attempt blocked: {user_telegram_id} is already referrer for {referrer_id}")
            return False
        
        existing_referral = await db.get_referral_by_referred_id(user_telegram_id)
        
        if existing_referral:
            logger.info(f"User {user_telegram_id} already has referrer")
            return False
        
        referral_code = await db.generate_unique_referral_code(referrer_id)
        
        referral = await db.create_referral(referrer_id, user_telegram_id, referral_code)
        
        if referral:
            logger.info(f"Created referral: {referrer_id} -> {user_telegram_id} with code {referral_code}")
            
            if bot:
                try:
                    referrer = await db.get_user_by_telegram_id(referrer_id)
                    if referrer:
                        import os
                        threshold = float(os.getenv('REFERRAL_THRESHOLD', '300.0'))
                        first_reward = float(os.getenv('REFERRAL_FIRST_REWARD', '150.0'))
                        percentage = float(os.getenv('REFERRAL_PERCENTAGE', '0.25'))
                        
                        await bot.send_message(
                            referrer_id,
                            f"🎉 Отлично! По вашей ссылке зарегистрировался новый пользователь!\n\n"
                            f"Вы получите {first_reward:.0f}₽ после того, как он пополнит баланс на {threshold:.0f}₽.\n"
                            f"И будете получать {percentage*100:.0f}% с каждого его платежа!"
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
    try:
        if not referral_code.startswith("REF"):
            return False
        
        logger.info(f"Trying to use referral code {referral_code} for user {user_telegram_id}")
        
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
            
            if referrer_id == user_telegram_id:
                logger.warning(f"User {user_telegram_id} tried to use own referral code")
                return False
            
            existing_reverse_referral = await db.get_referral_by_referred_id(referrer_id)
            if existing_reverse_referral and existing_reverse_referral.referrer_id == user_telegram_id:
                logger.warning(f"Mutual referral attempt blocked: {user_telegram_id} is already referrer for {referrer_id}")
                return False
            
            existing_referral = await db.get_referral_by_referred_id(user_telegram_id)
            
            if existing_referral:
                logger.info(f"User {user_telegram_id} already has referrer")
                return False
            
            referral = await db.create_referral(referrer_id, user_telegram_id, referral_code)
            
            if referral:
                logger.info(f"Created referral from promocode: {referrer_id} -> {user_telegram_id}")
                
                if bot:
                    try:
                        import os
                        threshold = float(os.getenv('REFERRAL_THRESHOLD', '300.0'))
                        first_reward = float(os.getenv('REFERRAL_FIRST_REWARD', '150.0'))
                        
                        await bot.send_message(
                            referrer_id,
                            f"🎉 По вашему промокоду {referral_code} зарегистрировался новый пользователь!\n\n"
                            f"Вы получите {first_reward:.0f}₽ после того, как он пополнит баланс на {threshold:.0f}₽."
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify referrer: {e}")
                
                return True
            
            return False
        
    except Exception as e:
        logger.error(f"Error creating referral from promocode: {e}")
        return False

def generate_referral_link(bot_username: str, user_id: int) -> str:
    if not bot_username:
        return ""
    
    if bot_username.startswith('@'):
        bot_username = bot_username[1:]
    
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

def validate_referral_code(code: str) -> bool:
    if not code or not code.startswith("REF"):
        return False
    if len(code) < 4 or len(code) > 20:
        return False
    return True

def format_referral_stats(stats: dict, lang: str = 'ru') -> str:
    if lang == 'ru':
        return (f"👥 Приглашено: {stats['total_referrals']}\n"
                f"✅ Активных: {stats['active_referrals']}\n"
                f"💰 Заработано: {stats['total_earned']:.2f}₽")
    else:
        return (f"👥 Invited: {stats['total_referrals']}\n"
                f"✅ Active: {stats['active_referrals']}\n"
                f"💰 Earned: ${stats['total_earned']:.2f}")
