import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, update

from app.database.crud.user import (
    get_user_by_id, get_user_by_telegram_id, get_users_list,
    get_users_count, get_users_statistics, get_inactive_users,
    add_user_balance, subtract_user_balance, update_user, delete_user
)
from app.database.crud.transaction import get_user_transactions_count
from app.database.crud.subscription import get_subscription_by_user_id
from app.database.models import (
    User, UserStatus, Subscription, Transaction, PromoCodeUse, 
    ReferralEarning, SubscriptionServer, YooKassaPayment, BroadcastHistory, CryptoBotPayment
)
from app.config import settings

logger = logging.getLogger(__name__)


class UserService:
    
    async def get_user_profile(
        self, 
        db: AsyncSession, 
        user_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            user = await get_user_by_id(db, user_id)
            if not user:
                return None
            
            subscription = await get_subscription_by_user_id(db, user_id)
            transactions_count = await get_user_transactions_count(db, user_id)
            
            return {
                "user": user,
                "subscription": subscription,
                "transactions_count": transactions_count,
                "is_admin": settings.is_admin(user.telegram_id),
                "registration_days": (datetime.utcnow() - user.created_at).days
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения профиля пользователя {user_id}: {e}")
            return None
    
    async def search_users(
        self,
        db: AsyncSession,
        query: str,
        page: int = 1,
        limit: int = 20
    ) -> Dict[str, Any]:
        try:
            offset = (page - 1) * limit
            
            users = await get_users_list(
                db, offset=offset, limit=limit, search=query
            )
            total_count = await get_users_count(db, search=query)
            
            total_pages = (total_count + limit - 1) // limit
            
            return {
                "users": users,
                "current_page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
            
        except Exception as e:
            logger.error(f"Ошибка поиска пользователей: {e}")
            return {
                "users": [],
                "current_page": 1,
                "total_pages": 1,
                "total_count": 0,
                "has_next": False,
                "has_prev": False
            }
    
    async def get_users_page(
        self,
        db: AsyncSession,
        page: int = 1,
        limit: int = 20,
        status: Optional[UserStatus] = None
    ) -> Dict[str, Any]:
        try:
            offset = (page - 1) * limit
            
            users = await get_users_list(
                db, offset=offset, limit=limit, status=status
            )
            total_count = await get_users_count(db, status=status)
            
            total_pages = (total_count + limit - 1) // limit
            
            return {
                "users": users,
                "current_page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения страницы пользователей: {e}")
            return {
                "users": [],
                "current_page": 1,
                "total_pages": 1,
                "total_count": 0,
                "has_next": False,
                "has_prev": False
            }
    
    async def update_user_balance(
        self,
        db: AsyncSession,
        user_id: int,
        amount_kopeks: int,
        description: str,
        admin_id: int
    ) -> bool:
        try:
            user = await get_user_by_id(db, user_id)
            if not user:
                return False
            
            if amount_kopeks > 0:
                await add_user_balance(db, user, amount_kopeks, description=description)
                logger.info(f"Админ {admin_id} пополнил баланс пользователя {user_id} на {amount_kopeks/100}₽")
                return True
            else:
                success = await subtract_user_balance(db, user, abs(amount_kopeks), description)
                if success:
                    logger.info(f"Админ {admin_id} списал с баланса пользователя {user_id} {abs(amount_kopeks)/100}₽")
                return success
            
        except Exception as e:
            logger.error(f"Ошибка изменения баланса пользователя: {e}")
            return False
    
    async def block_user(
        self,
        db: AsyncSession,
        user_id: int,
        admin_id: int,
        reason: str = "Заблокирован администратором"
    ) -> bool:
        try:
            user = await get_user_by_id(db, user_id)
            if not user:
                return False
            
            if user.remnawave_uuid:
                try:
                    from app.services.subscription_service import SubscriptionService
                    subscription_service = SubscriptionService()
                    await subscription_service.disable_remnawave_user(user.remnawave_uuid)
                    logger.info(f"✅ RemnaWave пользователь {user.remnawave_uuid} деактивирован при блокировке")
                except Exception as e:
                    logger.error(f"❌ Ошибка деактивации RemnaWave пользователя при блокировке: {e}")
            
            if user.subscription:
                from app.database.crud.subscription import deactivate_subscription
                await deactivate_subscription(db, user.subscription)
            
            await update_user(db, user, status=UserStatus.BLOCKED.value)
            
            logger.info(f"Админ {admin_id} заблокировал пользователя {user_id}: {reason}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка блокировки пользователя: {e}")
            return False
    
    async def unblock_user(
        self,
        db: AsyncSession,
        user_id: int,
        admin_id: int
    ) -> bool:
        try:
            user = await get_user_by_id(db, user_id)
            if not user:
                return False
            
            await update_user(db, user, status=UserStatus.ACTIVE.value)
            
            if user.subscription:
                from datetime import datetime
                from app.database.models import SubscriptionStatus
                
                if user.subscription.end_date > datetime.utcnow():
                    user.subscription.status = SubscriptionStatus.ACTIVE.value
                    await db.commit()
                    await db.refresh(user.subscription)
                    logger.info(f"🔄 Подписка пользователя {user_id} восстановлена")
                    
                    if user.remnawave_uuid:
                        try:
                            from app.services.subscription_service import SubscriptionService
                            subscription_service = SubscriptionService()
                            await subscription_service.update_remnawave_user(db, user.subscription)
                            logger.info(f"✅ RemnaWave пользователь {user.remnawave_uuid} восстановлен при разблокировке")
                        except Exception as e:
                            logger.error(f"❌ Ошибка восстановления RemnaWave пользователя при разблокировке: {e}")
                else:
                    logger.info(f"⏰ Подписка пользователя {user_id} истекла, восстановление невозможно")
            
            logger.info(f"Админ {admin_id} разблокировал пользователя {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка разблокировки пользователя: {e}")
            return False
    
    async def delete_user_account(
        self,
        db: AsyncSession,
        user_id: int,
        admin_id: int
    ) -> bool:
        try:
            user = await get_user_by_id(db, user_id)
            if not user:
                logger.warning(f"Пользователь {user_id} не найден для удаления")
                return False
            
            logger.info(f"🗑️ Начинаем полное удаление пользователя {user_id} (Telegram ID: {user.telegram_id})")
            
            if user.remnawave_uuid:
                try:
                    from app.services.subscription_service import SubscriptionService
                    subscription_service = SubscriptionService()
                    await subscription_service.disable_remnawave_user(user.remnawave_uuid)
                    logger.info(f"✅ RemnaWave пользователь {user.remnawave_uuid} деактивирован")
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка деактивации RemnaWave: {e}")
            
            try:
                from app.database.models import UserMessage
                from sqlalchemy import update
                
                result = await db.execute(
                    update(UserMessage)
                    .where(UserMessage.created_by == user_id)
                    .values(created_by=None)
                )
                if result.rowcount > 0:
                    logger.info(f"🔄 Обновлено {result.rowcount} пользовательских сообщений")
                await db.flush()
            except Exception as e:
                logger.error(f"❌ Ошибка обновления пользовательских сообщений: {e}")
            
            try:
                from app.database.models import PromoCode
                from sqlalchemy import update
                
                result = await db.execute(
                    update(PromoCode)
                    .where(PromoCode.created_by == user_id)
                    .values(created_by=None)
                )
                if result.rowcount > 0:
                    logger.info(f"🔄 Обновлено {result.rowcount} промокодов")
                await db.flush()
            except Exception as e:
                logger.error(f"❌ Ошибка обновления промокодов: {e}")
            
            try:
                from app.database.models import YooKassaPayment
                from sqlalchemy import select
                
                yookassa_result = await db.execute(
                    select(YooKassaPayment).where(YooKassaPayment.user_id == user_id)
                )
                yookassa_payments = yookassa_result.scalars().all()
                
                if yookassa_payments:
                    logger.info(f"🔄 Удаляем {len(yookassa_payments)} YooKassa платежей")
                    await db.execute(
                        delete(YooKassaPayment).where(YooKassaPayment.user_id == user_id)
                    )
                    await db.flush()
                    logger.info(f"✅ YooKassa платежи удалены")
            except Exception as e:
                logger.error(f"❌ Ошибка удаления YooKassa платежей: {e}")

            try:
                from app.database.models import CryptoBotPayment
                from sqlalchemy import select, delete
                
                cryptobot_result = await db.execute(
                    select(CryptoBotPayment).where(CryptoBotPayment.user_id == user_id)
                )
                cryptobot_payments = cryptobot_result.scalars().all()
                
                if cryptobot_payments:
                    logger.info(f"🔄 Удаляем {len(cryptobot_payments)} CryptoBot платежей")
                    await db.execute(
                        delete(CryptoBotPayment).where(CryptoBotPayment.user_id == user_id)
                    )
                    await db.flush()
                    logger.info(f"✅ CryptoBot платежи удалены")
            except Exception as e:
                logger.error(f"❌ Ошибка удаления CryptoBot платежей: {e}")
            
            try:
                transactions_result = await db.execute(
                    select(Transaction).where(Transaction.user_id == user_id)
                )
                transactions = transactions_result.scalars().all()
                
                if transactions:
                    logger.info(f"🔄 Удаляем {len(transactions)} транзакций")
                    await db.execute(
                        delete(Transaction).where(Transaction.user_id == user_id)
                    )
                    await db.flush()
                    logger.info(f"✅ Транзакции удалены")
            except Exception as e:
                logger.error(f"❌ Ошибка удаления транзакций: {e}")
            
            try:
                await db.execute(
                    delete(PromoCodeUse).where(PromoCodeUse.user_id == user_id)
                )
                await db.flush()
                logger.info(f"🗑️ Удалены использования промокодов пользователя {user_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка удаления использований промокодов: {e}")
            
            try:
                await db.execute(
                    delete(ReferralEarning).where(ReferralEarning.user_id == user_id)
                )
                await db.flush()
                logger.info(f"🗑️ Удалены реферальные доходы пользователя {user_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка удаления реферальных доходов: {e}")
            
            try:
                await db.execute(
                    delete(ReferralEarning).where(ReferralEarning.referral_id == user_id)
                )
                await db.flush()
                logger.info(f"🗑️ Удалены реферальные записи о пользователе {user_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка удаления реферальных записей: {e}")
            
            try:
                from app.database.models import BroadcastHistory
                await db.execute(
                    delete(BroadcastHistory).where(BroadcastHistory.admin_id == user_id)
                )
                await db.flush()
                logger.info(f"🗑️ Удалена история рассылок админа {user_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка удаления истории рассылок: {e}")
            
            try:
                from app.database.models import SubscriptionConversion
                conversions_result = await db.execute(
                    select(SubscriptionConversion).where(SubscriptionConversion.user_id == user_id)
                )
                conversions = conversions_result.scalars().all()
                
                if conversions:
                    logger.info(f"🔄 Удаляем {len(conversions)} записей конверсий")
                    await db.execute(
                        delete(SubscriptionConversion).where(SubscriptionConversion.user_id == user_id)
                    )
                    await db.flush()
                    logger.info(f"✅ Записи конверсий удалены")
            except Exception as e:
                logger.error(f"❌ Ошибка удаления записей конверсий: {e}")
            
            if user.subscription:
                try:
                    await db.execute(
                        delete(SubscriptionServer).where(
                            SubscriptionServer.subscription_id == user.subscription.id
                        )
                    )
                    await db.flush()
                    logger.info(f"🗑️ Удалены записи SubscriptionServer для подписки {user.subscription.id}")
                except Exception as e:
                    logger.error(f"❌ Ошибка удаления SubscriptionServer: {e}")
            
            if user.subscription:
                try:
                    from app.database.models import Subscription
                    await db.execute(
                        delete(Subscription).where(Subscription.user_id == user_id)
                    )
                    await db.flush()
                    logger.info(f"🗑️ Удалена подписка пользователя {user_id}")
                except Exception as e:
                    logger.error(f"❌ Ошибка удаления подписки: {e}")
            
            try:
                from sqlalchemy import update
                referrals_result = await db.execute(
                    update(User)
                    .where(User.referred_by_id == user_id)
                    .values(referred_by_id=None)
                )
                if referrals_result.rowcount > 0:
                    logger.info(f"🔗 Очищены реферальные ссылки у {referrals_result.rowcount} рефералов")
                await db.flush()
            except Exception as e:
                logger.error(f"❌ Ошибка очистки реферальных ссылок: {e}")
            
            try:
                await db.execute(
                    delete(User).where(User.id == user_id)
                )
                await db.commit()
                logger.info(f"✅ Пользователь {user_id} окончательно удален из базы")
            except Exception as e:
                logger.error(f"❌ Ошибка финального удаления пользователя: {e}")
                await db.rollback()
                return False
            
            logger.info(f"✅ Пользователь {user.telegram_id} (ID: {user_id}) полностью удален администратором {admin_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка удаления пользователя {user_id}: {e}")
            await db.rollback()
            return False
    
    async def get_user_statistics(self, db: AsyncSession) -> Dict[str, Any]:
        try:
            stats = await get_users_statistics(db)
            return stats
            
        except Exception as e:
            logger.error(f"Ошибка получения статистики пользователей: {e}")
            return {
                "total_users": 0,
                "active_users": 0,
                "blocked_users": 0,
                "new_today": 0,
                "new_week": 0,
                "new_month": 0
            }
    
    async def cleanup_inactive_users(
        self,
        db: AsyncSession,
        months: int = None
    ) -> int:
        try:
            if months is None:
                months = settings.INACTIVE_USER_DELETE_MONTHS
            
            inactive_users = await get_inactive_users(db, months)
            deleted_count = 0
            
            for user in inactive_users:
                success = await self.delete_user_account(db, user.id, 0) 
                if success:
                    deleted_count += 1
            
            logger.info(f"Удалено {deleted_count} неактивных пользователей")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Ошибка очистки неактивных пользователей: {e}")
            return 0
    
    async def get_user_activity_summary(
        self,
        db: AsyncSession,
        user_id: int
    ) -> Dict[str, Any]:
        try:
            user = await get_user_by_id(db, user_id)
            if not user:
                return {}
            
            subscription = await get_subscription_by_user_id(db, user_id)
            transactions_count = await get_user_transactions_count(db, user_id)
            
            days_since_registration = (datetime.utcnow() - user.created_at).days
            
            days_since_activity = (datetime.utcnow() - user.last_activity).days if user.last_activity else None
            
            return {
                "user_id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "full_name": user.full_name,
                "status": user.status,
                "language": user.language,
                "balance_kopeks": user.balance_kopeks,
                "registration_date": user.created_at,
                "last_activity": user.last_activity,
                "days_since_registration": days_since_registration,
                "days_since_activity": days_since_activity,
                "has_subscription": subscription is not None,
                "subscription_active": subscription.is_active if subscription else False,
                "subscription_trial": subscription.is_trial if subscription else False,
                "transactions_count": transactions_count,
                "referrer_id": user.referred_by_id,
                "referral_code": user.referral_code
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения сводки активности пользователя {user_id}: {e}")
            return {}
    
    async def get_users_by_criteria(
        self,
        db: AsyncSession,
        criteria: Dict[str, Any]
    ) -> List[User]:
        try:
            status = criteria.get('status')
            has_subscription = criteria.get('has_subscription')
            is_trial = criteria.get('is_trial')
            min_balance = criteria.get('min_balance', 0)
            max_balance = criteria.get('max_balance')
            days_inactive = criteria.get('days_inactive')
            
            registered_after = criteria.get('registered_after')
            registered_before = criteria.get('registered_before')
            
            users = await get_users_list(db, offset=0, limit=10000, status=status)
            
            filtered_users = []
            for user in users:
                if user.balance_kopeks < min_balance:
                    continue
                if max_balance and user.balance_kopeks > max_balance:
                    continue
                
                if registered_after and user.created_at < registered_after:
                    continue
                if registered_before and user.created_at > registered_before:
                    continue
                
                if days_inactive and user.last_activity:
                    inactive_threshold = datetime.utcnow() - timedelta(days=days_inactive)
                    if user.last_activity > inactive_threshold:
                        continue
                
                filtered_users.append(user)
            
            return filtered_users
            
        except Exception as e:
            logger.error(f"Ошибка получения пользователей по критериям: {e}")
            return []
