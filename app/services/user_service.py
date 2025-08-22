import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.user import (
    get_user_by_id, get_user_by_telegram_id, get_users_list,
    get_users_count, get_users_statistics, get_inactive_users,
    add_user_balance, subtract_user_balance, update_user, delete_user
)
from app.database.crud.transaction import get_user_transactions_count
from app.database.crud.subscription import get_subscription_by_user_id
from app.database.models import User, UserStatus
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
                await add_user_balance(db, user, amount_kopeks, description)
                logger.info(f"Админ {admin_id} пополнил баланс пользователя {user_id} на {amount_kopeks/100}₽")
            else:
                success = await subtract_user_balance(db, user, abs(amount_kopeks), description)
                if success:
                    logger.info(f"Админ {admin_id} списал с баланса пользователя {user_id} {abs(amount_kopeks)/100}₽")
                return success
            
            return True
            
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
                return False
            
            if user.subscription:
                from app.database.crud.subscription import deactivate_subscription
                await deactivate_subscription(db, user.subscription)
            
            success = await delete_user(db, user)
            
            if success:
                logger.info(f"Админ {admin_id} удалил пользователя {user_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Ошибка удаления пользователя: {e}")
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
                success = await delete_user(db, user)
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
                "referrer_id": user.referrer_id,
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
