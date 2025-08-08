from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, String, Float, DateTime, Boolean, Text, Integer, text
from datetime import datetime
from typing import Optional, List, Dict
import logging

logger = logging.getLogger(__name__)

class Base(DeclarativeBase):
    pass

class ReferralProgram(Base):
    __tablename__ = 'referral_programs'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(BigInteger, index=True)  # Кто пригласил
    referred_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)  # Кто был приглашен
    referral_code: Mapped[str] = mapped_column(String(20), index=True)  # Промокод реферера
    first_reward_paid: Mapped[bool] = mapped_column(Boolean, default=False)  # Выплачена ли разовая награда
    total_earned: Mapped[float] = mapped_column(Float, default=0.0)  # Всего заработано
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    first_reward_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # Когда выплатили первую награду

class ReferralEarning(Base):
    __tablename__ = 'referral_earnings'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(BigInteger, index=True)  # Кто получил награду
    referred_id: Mapped[int] = mapped_column(BigInteger, index=True)  # От кого получена награда
    amount: Mapped[float] = mapped_column(Float)  # Размер награды
    earning_type: Mapped[str] = mapped_column(String(20))  # 'first_reward', 'percentage'
    related_payment_id: Mapped[Optional[int]] = mapped_column(Integer)  # Связанный платеж
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class User(Base):
    __tablename__ = 'users'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    last_name: Mapped[Optional[str]] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(10), default='ru')
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    remnawave_uuid: Mapped[Optional[str]] = mapped_column(String(255))
    is_trial_used: Mapped[bool] = mapped_column(Boolean, default=False)

class Subscription(Base):
    __tablename__ = 'subscriptions'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Float)
    duration_days: Mapped[int] = mapped_column(Integer)
    traffic_limit_gb: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    squad_uuid: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    is_imported: Mapped[bool] = mapped_column(Boolean, default=False)

class UserSubscription(Base):
    __tablename__ = 'user_subscriptions'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    subscription_id: Mapped[int] = mapped_column(Integer, index=True)
    short_uuid: Mapped[str] = mapped_column(String(255))  # УБРАНО unique=True
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    traffic_limit_gb: Mapped[Optional[int]] = mapped_column(Integer)  # Добавлено поле
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=datetime.utcnow)  # Добавлено поле

class Payment(Base):
    __tablename__ = 'payments'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[float] = mapped_column(Float)
    payment_type: Mapped[str] = mapped_column(String(50))  # 'topup', 'subscription'
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default='pending')  # 'pending', 'completed', 'cancelled'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Promocode(Base):
    __tablename__ = 'promocodes'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    discount_amount: Mapped[float] = mapped_column(Float)
    discount_percent: Mapped[Optional[int]] = mapped_column(Integer)
    usage_limit: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class PromocodeUsage(Base):
    __tablename__ = 'promocode_usage'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    promocode_id: Mapped[int] = mapped_column(Integer, index=True)
    used_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Database:
    def __init__(self, database_url: str):
        self.engine = create_async_engine(
            database_url, 
            echo=False,
            pool_pre_ping=True,
            pool_recycle=300
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
    
    async def init_db(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
        # Выполняем миграции
        await self.migrate_user_subscriptions()
        await self.migrate_subscription_imported_field()
        await self.migrate_referral_tables()  # НОВАЯ МИГРАЦИЯ

    async def migrate_referral_tables(self):
        """Create referral system tables if they don't exist"""
        try:
            async with self.engine.begin() as conn:
                # Создаем таблицы реферальной системы
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS referral_programs (
                        id SERIAL PRIMARY KEY,
                        referrer_id BIGINT NOT NULL,
                        referred_id BIGINT UNIQUE NOT NULL,
                        referral_code VARCHAR(20) NOT NULL,
                        first_reward_paid BOOLEAN DEFAULT FALSE,
                        total_earned DOUBLE PRECISION DEFAULT 0.0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        first_reward_at TIMESTAMP,
                    
                        INDEX idx_referrer (referrer_id),
                        INDEX idx_referred (referred_id),
                        INDEX idx_referral_code (referral_code)
                    )
                """))
            
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS referral_earnings (
                        id SERIAL PRIMARY KEY,
                        referrer_id BIGINT NOT NULL,
                        referred_id BIGINT NOT NULL,
                        amount DOUBLE PRECISION NOT NULL,
                        earning_type VARCHAR(20) NOT NULL,
                        related_payment_id INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                        INDEX idx_referrer_earnings (referrer_id),
                        INDEX idx_referred_earnings (referred_id),
                        INDEX idx_earning_type (earning_type)
                    )
                """))
            
                logger.info("Successfully created referral system tables")
        except Exception as e:
            logger.error(f"Error creating referral tables: {e}")
    
    async def close(self):
        await self.engine.dispose()
    
    # User methods
    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(User).where(User.telegram_id == telegram_id)
                )
                return result.scalar_one_or_none()
            except Exception as e:
                logger.error(f"Error getting user by telegram_id {telegram_id}: {e}")
                return None
    
    async def create_user(self, telegram_id: int, username: str = None, 
                         first_name: str = None, last_name: str = None, 
                         language: str = 'ru', is_admin: bool = False) -> User:
        async with self.session_factory() as session:
            try:
                user = User(
                    telegram_id=telegram_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    language=language,
                    is_admin=is_admin
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
                return user
            except Exception as e:
                logger.error(f"Error creating user {telegram_id}: {e}")
                await session.rollback()
                raise
    
    async def update_user(self, user: User) -> User:
        async with self.session_factory() as session:
            try:
                await session.merge(user)
                await session.commit()
                return user
            except Exception as e:
                logger.error(f"Error updating user {user.telegram_id}: {e}")
                await session.rollback()
                raise
    
    async def add_balance(self, user_id: int, amount: float) -> bool:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, update
                result = await session.execute(
                    update(User)
                    .where(User.telegram_id == user_id)
                    .values(balance=User.balance + amount)
                )
                await session.commit()
                return result.rowcount > 0
            except Exception as e:
                logger.error(f"Error adding balance to user {user_id}: {e}")
                await session.rollback()
                return False
    
    # Subscription methods
    async def get_all_subscriptions(self, include_inactive: bool = False, exclude_trial: bool = True, exclude_imported: bool = True) -> List[Subscription]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                query = select(Subscription)
                if not include_inactive:
                    query = query.where(Subscription.is_active == True)
                if exclude_trial:
                    query = query.where(Subscription.is_trial == False)
                if exclude_imported:
                    query = query.where(Subscription.is_imported == False)  # Исключаем импортированные
                result = await session.execute(query)
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting subscriptions: {e}")
                return []

    async def get_all_subscriptions_admin(self) -> List[Subscription]:
        """Get all subscriptions including imported ones (for admin purposes)"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(select(Subscription))
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting admin subscriptions: {e}")
                return []

    async def migrate_subscription_imported_field(self):
        """Add is_imported field to subscriptions table"""
        try:
            async with self.engine.begin() as conn:
                try:
                    await conn.execute(text("""
                        ALTER TABLE subscriptions 
                        ADD COLUMN IF NOT EXISTS is_imported BOOLEAN DEFAULT FALSE
                    """))
                    logger.info("Successfully added is_imported field to subscriptions table")
                except Exception as e:
                    logger.info(f"Migration may have already been applied: {e}")
        except Exception as e:
            logger.error(f"Error during subscription migration: {e}")            
    
    async def get_subscription_by_id(self, subscription_id: int) -> Optional[Subscription]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(Subscription).where(Subscription.id == subscription_id)
                )
                return result.scalar_one_or_none()
            except Exception as e:
                logger.error(f"Error getting subscription {subscription_id}: {e}")
                return None
    
    async def create_subscription(self, name: str, description: str, price: float,
                                duration_days: int, traffic_limit_gb: int, 
                                squad_uuid: str, is_imported: bool = False) -> Subscription:
        async with self.session_factory() as session:
            try:
                subscription = Subscription(
                    name=name,
                    description=description,
                    price=price,
                    duration_days=duration_days,
                    traffic_limit_gb=traffic_limit_gb,
                    squad_uuid=squad_uuid,
                    is_imported=is_imported  # Добавляем поддержку is_imported
                )
                session.add(subscription)
                await session.commit()
                await session.refresh(subscription)
                return subscription
            except Exception as e:
                logger.error(f"Error creating subscription: {e}")
                await session.rollback()
                raise
    
    async def update_subscription(self, subscription: Subscription) -> Subscription:
        async with self.session_factory() as session:
            try:
                await session.merge(subscription)
                await session.commit()
                return subscription
            except Exception as e:
                logger.error(f"Error updating subscription {subscription.id}: {e}")
                await session.rollback()
                raise
    
    async def delete_subscription(self, subscription_id: int) -> bool:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import delete
                result = await session.execute(
                    delete(Subscription).where(Subscription.id == subscription_id)
                )
                await session.commit()
                return result.rowcount > 0
            except Exception as e:
                logger.error(f"Error deleting subscription {subscription_id}: {e}")
                await session.rollback()
                return False
    
    async def get_user_subscriptions(self, user_id: int) -> List[UserSubscription]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(UserSubscription).where(UserSubscription.user_id == user_id)
                )
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting user subscriptions for {user_id}: {e}")
                return []
    
    async def create_user_subscription(self, user_id: int, subscription_id: int, 
                                 short_uuid: str, expires_at: datetime, 
                                 is_active: bool = True, traffic_limit_gb: int = None) -> Optional[UserSubscription]:
        """Create user subscription with proper error handling"""
        async with self.session_factory() as session:
            try:
                # Проверяем что подписка не существует
                from sqlalchemy import select
                existing = await session.execute(
                    select(UserSubscription).where(
                        UserSubscription.user_id == user_id,
                        UserSubscription.short_uuid == short_uuid
                    )
                )
                existing_sub = existing.scalar_one_or_none()
            
                if existing_sub:
                    logger.warning(f"Subscription with short_uuid {short_uuid} already exists for user {user_id}")
                    return existing_sub
            
            # Создаем новую подписку
                new_subscription = UserSubscription(
                    user_id=user_id,
                    subscription_id=subscription_id,
                    short_uuid=short_uuid,
                    expires_at=expires_at,
                    is_active=is_active
                )
            
                session.add(new_subscription)
                await session.commit()
                await session.refresh(new_subscription)
            
                return new_subscription
            
            except Exception as e:
                logger.error(f"Error creating user subscription: {e}")
                await session.rollback()
                return None
    
        # Payment methods
    async def create_payment(self, user_id: int, amount: float, payment_type: str,
                           description: str, status: str = 'pending') -> Payment:
        async with self.session_factory() as session:
            try:
                payment = Payment(
                    user_id=user_id,
                    amount=amount,
                    payment_type=payment_type,
                    description=description,
                    status=status
                )
                session.add(payment)
                await session.commit()
                await session.refresh(payment)
                return payment
            except Exception as e:
                logger.error(f"Error creating payment: {e}")
                await session.rollback()
                raise
    
    async def get_payment_by_id(self, payment_id: int) -> Optional[Payment]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(Payment).where(Payment.id == payment_id)
                )
                return result.scalar_one_or_none()
            except Exception as e:
                logger.error(f"Error getting payment {payment_id}: {e}")
                return None
    
    async def update_payment(self, payment: Payment) -> Payment:
        async with self.session_factory() as session:
            try:
                await session.merge(payment)
                await session.commit()
                return payment
            except Exception as e:
                logger.error(f"Error updating payment {payment.id}: {e}")
                await session.rollback()
                raise
    
    async def get_user_payments(self, user_id: int) -> List[Payment]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, desc
                result = await session.execute(
                    select(Payment)
                    .where(Payment.user_id == user_id)
                    .order_by(desc(Payment.created_at))
                )
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting user payments for {user_id}: {e}")
                return []
    
    # Promocode methods
    async def get_promocode_by_code(self, code: str) -> Optional[Promocode]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(Promocode).where(Promocode.code == code)
                )
                return result.scalar_one_or_none()
            except Exception as e:
                logger.error(f"Error getting promocode {code}: {e}")
                return None
    
    async def create_promocode(self, code: str, discount_amount: float = 0,
                             discount_percent: int = None, usage_limit: int = 1,
                             expires_at: datetime = None) -> Promocode:
        async with self.session_factory() as session:
            try:
                promocode = Promocode(
                    code=code,
                    discount_amount=discount_amount,
                    discount_percent=discount_percent,
                    usage_limit=usage_limit,
                    expires_at=expires_at
                )
                session.add(promocode)
                await session.commit()
                await session.refresh(promocode)
                return promocode
            except Exception as e:
                logger.error(f"Error creating promocode: {e}")
                await session.rollback()
                raise
    
    async def use_promocode(self, user_id: int, promocode: Promocode) -> bool:
        async with self.session_factory() as session:
            try:
                # Check if already used
                from sqlalchemy import select
                existing = await session.execute(
                    select(PromocodeUsage).where(
                        PromocodeUsage.user_id == user_id,
                        PromocodeUsage.promocode_id == promocode.id
                    )
                )
                if existing.scalar_one_or_none():
                    return False
                
                # Create usage record
                usage = PromocodeUsage(user_id=user_id, promocode_id=promocode.id)
                session.add(usage)
                
                # Update promocode used count
                promocode.used_count += 1
                await session.merge(promocode)
                
                await session.commit()
                return True
            except Exception as e:
                logger.error(f"Error using promocode: {e}")
                await session.rollback()
                return False
    
    async def get_all_promocodes(self) -> List[Promocode]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(select(Promocode))
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting promocodes: {e}")
                return []
    
    # Admin methods
    async def get_all_users(self) -> List[User]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(select(User))
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting all users: {e}")
                return []
    
    async def get_stats(self) -> dict:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, func
            
                # Total users
                total_users = await session.execute(
                    select(func.count(User.id))
                )
                total_users = total_users.scalar()
            
                # Total subscriptions (excluding trial)
                total_subs_non_trial = await session.execute(
                    select(func.count(UserSubscription.id))
                    .join(Subscription, UserSubscription.subscription_id == Subscription.id)
                    .where(Subscription.is_trial == False)
                )
                total_subs_non_trial = total_subs_non_trial.scalar()
            
                # Total payments (excluding trial payments)
                total_payments = await session.execute(
                    select(func.sum(Payment.amount)).where(
                        Payment.status == 'completed',
                        Payment.payment_type != 'trial'  # Исключаем тестовые платежи
                    )
                )
                total_payments = total_payments.scalar() or 0
            
                return {
                   'total_users': total_users,
                   'total_subscriptions_non_trial': total_subs_non_trial,
                   'total_revenue': total_payments
                }
            except Exception as e:
                logger.error(f"Error getting stats: {e}")
                return {
                    'total_users': 0,
                    'total_subscriptions_non_trial': 0,
                    'total_revenue': 0
            }

    async def get_trial_subscriptions(self) -> List[Subscription]:
        """Get only trial subscriptions"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(Subscription).where(Subscription.is_trial == True)
                )
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting trial subscriptions: {e}")
                return []

    async def get_user_subscription_by_short_uuid(self, user_id: int, short_uuid: str) -> Optional[UserSubscription]:
        """Get user subscription by short_uuid"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(UserSubscription).where(
                        UserSubscription.user_id == user_id,
                        UserSubscription.short_uuid == short_uuid
                    )
                )
                return result.scalar_one_or_none()
            except Exception as e:
                logger.error(f"Error getting user subscription by short_uuid: {e}")
                return None
    
    async def update_user_subscription(self, user_subscription: UserSubscription) -> bool:
        """Update user subscription"""
        async with self.session_factory() as session:
            try:
                # Устанавливаем время обновления
                user_subscription.updated_at = datetime.utcnow()
            
                # Обновляем подписку
                await session.merge(user_subscription)
                await session.commit()
                return True
            except Exception as e:
                logger.error(f"Error updating user subscription: {e}")
                await session.rollback()
                return False

    async def migrate_user_subscriptions(self):
        """Migrate user_subscriptions table to add missing columns"""
        try:
            async with self.engine.begin() as conn:
                # Проверяем существование столбцов и добавляем их если нет
                try:
                    await conn.execute(text("""
                        ALTER TABLE user_subscriptions 
                        ADD COLUMN IF NOT EXISTS traffic_limit_gb INTEGER,
                        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP
                    """))
                    logger.info("Successfully migrated user_subscriptions table")
                except Exception as e:
                    logger.info(f"Migration may have already been applied or error occurred: {e}")
        except Exception as e:
            logger.error(f"Error during migration: {e}")

    async def get_expiring_subscriptions(self, user_id: int, days_threshold: int = 3) -> List[UserSubscription]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                from datetime import datetime, timedelta
                
                threshold_date = datetime.utcnow() + timedelta(days=days_threshold)
                
                result = await session.execute(
                    select(UserSubscription).where(
                        UserSubscription.user_id == user_id,
                        UserSubscription.is_active == True,
                        UserSubscription.expires_at <= threshold_date,
                        UserSubscription.expires_at > datetime.utcnow()
                    )
                )
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting expiring subscriptions for {user_id}: {e}")
                return []

    async def has_used_trial(self, user_id: int) -> bool:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(User.is_trial_used).where(User.telegram_id == user_id)
                )
                is_trial_used = result.scalar_one_or_none()
                return is_trial_used or False
            except Exception as e:
                logger.error(f"Error checking trial usage for user {user_id}: {e}")
                return False

    async def mark_trial_used(self, user_id: int) -> bool:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import update
                result = await session.execute(
                    update(User)
                    .where(User.telegram_id == user_id)
                    .values(is_trial_used=True)
                )
                await session.commit()
                return result.rowcount > 0
            except Exception as e:
                logger.error(f"Error marking trial used for user {user_id}: {e}")
                await session.rollback()
                return False

    async def get_all_payments_paginated(self, offset: int = 0, limit: int = 10) -> tuple[List[Payment], int]:
        """Get all payments with pagination"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, desc, func
        
                # Получаем общее количество записей
                count_result = await session.execute(
                    select(func.count(Payment.id))
                )
                total_count = count_result.scalar()
        
                # Получаем платежи с пагинацией
                result = await session.execute(
                    select(Payment)
                    .order_by(desc(Payment.created_at))
                    .offset(offset)
                    .limit(limit)
                )
                payments = list(result.scalars().all())
        
                return payments, total_count
        
            except Exception as e:
                logger.error(f"Error getting paginated payments: {e}")
                return [], 0

    async def get_payments_by_type_paginated(self, payment_type: str, offset: int = 0, limit: int = 10) -> tuple[List[Payment], int]:
        """Get payments by type with pagination"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, desc, func
        
                # Получаем общее количество записей
                count_result = await session.execute(
                    select(func.count(Payment.id)).where(Payment.payment_type == payment_type)
                )
                total_count = count_result.scalar()
        
                # Получаем платежи с пагинацией
                result = await session.execute(
                    select(Payment)
                    .where(Payment.payment_type == payment_type)
                    .order_by(desc(Payment.created_at))
                    .offset(offset)
                    .limit(limit)
                )
                payments = list(result.scalars().all())
        
                return payments, total_count
        
            except Exception as e:
                logger.error(f"Error getting paginated payments by type: {e}")
                return [], 0

    async def get_payments_by_status_paginated(self, status: str, offset: int = 0, limit: int = 10) -> tuple[List[Payment], int]:
        """Get payments by status with pagination"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, desc, func
            
                # Получаем общее количество записей
                count_result = await session.execute(
                    select(func.count(Payment.id)).where(Payment.status == status)
                )
                total_count = count_result.scalar()
            
                # Получаем платежи с пагинацией
                result = await session.execute(
                    select(Payment)
                    .where(Payment.status == status)
                    .order_by(desc(Payment.created_at))
                    .offset(offset)
                    .limit(limit)
                )
                payments = list(result.scalars().all())
            
                return payments, total_count
            
            except Exception as e:
                logger.error(f"Error getting paginated payments by status: {e}")
                return [], 0

    async def get_user_subscriptions_by_plan_id(self, plan_id: int) -> List[UserSubscription]:
        """Get all user subscriptions for a specific plan"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(UserSubscription).where(UserSubscription.subscription_id == plan_id)
                )
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting user subscriptions for plan {plan_id}: {e}")
                return []

    async def delete_user_subscription(self, user_subscription_id: int) -> bool:
        """Delete user subscription by ID"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import delete
                result = await session.execute(
                    delete(UserSubscription).where(UserSubscription.id == user_subscription_id)
                )
                await session.commit()
                return result.rowcount > 0
            except Exception as e:
                logger.error(f"Error deleting user subscription {user_subscription_id}: {e}")
                await session.rollback()
                return False

    async def create_referral(self, referrer_id: int, referred_id: int, referral_code: str) -> Optional[ReferralProgram]:
        """Create referral relationship - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
        
                if referred_id == 0:
                    # Генерируем уникальный placeholder ID для хранения кода
                    placeholder_id = 999999999 - referrer_id  # Уникальный ID на основе referrer_id
            
                    # Проверяем что код еще не существует для этого пользователя
                    existing = await session.execute(
                        select(ReferralProgram).where(
                            ReferralProgram.referrer_id == referrer_id,
                            ReferralProgram.referred_id == placeholder_id
                        )
                    )
                    existing_referral = existing.scalar_one_or_none()
            
                    if existing_referral:
                        logger.info(f"Referral code already exists for user {referrer_id}")
                        return existing_referral
            
                    # Создаем запись для хранения кода
                    referral = ReferralProgram(
                        referrer_id=referrer_id,
                        referred_id=placeholder_id,  # Уникальный placeholder
                        referral_code=referral_code
                    )
                    session.add(referral)
                    await session.commit()
                    await session.refresh(referral)
                    logger.info(f"Created referral code storage for user {referrer_id}")
                    return referral
            
                # Обычная логика для реальных рефералов
                existing = await session.execute(
                    select(ReferralProgram).where(
                        ReferralProgram.referred_id == referred_id,
                        ReferralProgram.referred_id < 900000000,  # Исключаем placeholder
                        ReferralProgram.referred_id > 0  # Исключаем нулевые
                    )
                )
                if existing.scalar_one_or_none():
                    logger.info(f"User {referred_id} already has a real referrer")
                    return None
    
                # Проверяем что пользователь не приглашает сам себя
                if referrer_id == referred_id:
                    logger.warning(f"User {referrer_id} tried to refer themselves")
                    return None
    
                # Создаем реальную реферальную связь
                referral = ReferralProgram(
                    referrer_id=referrer_id,
                    referred_id=referred_id,
                    referral_code=referral_code
                )
                session.add(referral)
                await session.commit()
                await session.refresh(referral)
            
                logger.info(f"Created real referral: {referrer_id} -> {referred_id}")
                return referral
            
            except Exception as e:
                logger.error(f"Error creating referral: {e}")
                await session.rollback()
                return None

    async def get_referral_by_referred_id(self, referred_id: int) -> Optional[ReferralProgram]:
        """Get referral info by referred user ID"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(ReferralProgram).where(ReferralProgram.referred_id == referred_id)
                )
                return result.scalar_one_or_none()
            except Exception as e:
                logger.error(f"Error getting referral: {e}")
                return None

    async def get_user_referrals(self, referrer_id: int) -> List[ReferralProgram]:
        """Get all referrals for a user - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, and_
                result = await session.execute(
                    select(ReferralProgram).where(
                        and_(
                            ReferralProgram.referrer_id == referrer_id,
                            # ИСПРАВЛЕНО: исключаем placeholder записи для хранения кодов
                            ReferralProgram.referred_id < 900000000,
                            ReferralProgram.referred_id > 0
                        )
                    )
                )
                referrals = list(result.scalars().all())
                logger.debug(f"Found {len(referrals)} real referrals for user {referrer_id}")
                return referrals
            except Exception as e:
                logger.error(f"Error getting user referrals: {e}")
                return []

    async def create_referral(self, referrer_id: int, referred_id: int, referral_code: str) -> Optional[ReferralProgram]:
        """Create referral relationship - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
        
                # Специальный случай: создание кода для самого пользователя
                if referred_id == 0:
                    # Генерируем уникальный placeholder ID для хранения кода
                    placeholder_id = 999999999 - referrer_id  # Уникальный ID на основе referrer_id
            
                    # Проверяем что код еще не существует для этого пользователя
                    existing = await session.execute(
                        select(ReferralProgram).where(
                            ReferralProgram.referrer_id == referrer_id,
                            ReferralProgram.referred_id == placeholder_id
                        )
                    )
                    existing_referral = existing.scalar_one_or_none()
            
                    if existing_referral:
                        logger.info(f"Referral code already exists for user {referrer_id}")
                        return existing_referral
            
                    # Создаем запись для хранения кода
                    referral = ReferralProgram(
                        referrer_id=referrer_id,
                        referred_id=placeholder_id,  # Уникальный placeholder
                        referral_code=referral_code
                    )
                    session.add(referral)
                    await session.commit()
                    await session.refresh(referral)
                    return referral
        
                # Обычная логика для реальных рефералов
                existing = await session.execute(
                    select(ReferralProgram).where(ReferralProgram.referred_id == referred_id)
                )
                if existing.scalar_one_or_none():
                    logger.info(f"User {referred_id} already has a referrer")
                    return None
    
                # Проверяем что пользователь не приглашает сам себя
                if referrer_id == referred_id:
                    logger.warning(f"User {referrer_id} tried to refer themselves")
                    return None
    
                referral = ReferralProgram(
                    referrer_id=referrer_id,
                    referred_id=referred_id,
                    referral_code=referral_code
                )
                session.add(referral)
                await session.commit()
                await session.refresh(referral)
                return referral
            except Exception as e:
                logger.error(f"Error creating referral: {e}")
                await session.rollback()
                return None

    async def get_user_referral_stats(self, user_id: int) -> Dict:
        """Get user referral statistics - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, func, and_, or_
        
                placeholder_id = 999999999 - user_id
            
                # Количество приглашенных (исключаем только конкретный placeholder этого пользователя)
                referrals_count = await session.execute(
                    select(func.count(ReferralProgram.id))
                    .where(
                        and_(
                            ReferralProgram.referrer_id == user_id,
                            ReferralProgram.referred_id != placeholder_id,  # Исключаем только наш placeholder
                            ReferralProgram.referred_id != 0  # Исключаем нулевые записи
                        )
                    )
                )
        
                # Количество тех, кто получил первую награду (исключаем placeholder)
                active_referrals = await session.execute(
                    select(func.count(ReferralProgram.id))
                    .where(
                        and_(
                            ReferralProgram.referrer_id == user_id,
                            ReferralProgram.first_reward_paid == True,
                            ReferralProgram.referred_id != placeholder_id,  # Исключаем только наш placeholder
                            ReferralProgram.referred_id != 0  # Исключаем нулевые записи
                        )
                    )
                )
            
                # Общий заработок
                total_earned = await session.execute(
                    select(func.sum(ReferralEarning.amount))
                    .where(ReferralEarning.referrer_id == user_id)
                )
        
                result = {
                    'total_referrals': referrals_count.scalar() or 0,
                    'active_referrals': active_referrals.scalar() or 0,
                    'total_earned': total_earned.scalar() or 0.0
                }
            
                logger.info(f"Referral stats for user {user_id}: {result}")
                return result
        
            except Exception as e:
                logger.error(f"Error getting referral stats: {e}")
                return {
                    'total_referrals': 0,
                    'active_referrals': 0,
                    'total_earned': 0.0
                }

    async def generate_unique_referral_code(self, user_id: int) -> str:
        """Generate unique referral code for user"""
        async with self.session_factory() as session:
            try:
                import secrets
                import string
            
                # Сначала пытаемся создать код на основе user_id
                base_code = f"REF{user_id}"
            
                from sqlalchemy import select
                existing = await session.execute(
                    select(ReferralProgram).where(ReferralProgram.referral_code == base_code)
                )
            
                if not existing.scalar_one_or_none():
                    return base_code
            
                # Если код уже существует, добавляем случайные символы
                for _ in range(10):
                    random_suffix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
                    code = f"REF{user_id}{random_suffix}"
                
                    existing = await session.execute(
                        select(ReferralProgram).where(ReferralProgram.referral_code == code)
                    )
                
                    if not existing.scalar_one_or_none():
                        return code
            
                # Если все еще не удалось, используем полностью случайный код
                return f"REF{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))}"
            
            except Exception as e:
                logger.error(f"Error generating referral code: {e}")
                return f"REF{user_id}ERR"

    async def get_user_referrals(self, referrer_id: int) -> List[ReferralProgram]:
        """Get all referrals for a user - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, and_
            
                placeholder_id = 999999999 - referrer_id
            
                result = await session.execute(
                    select(ReferralProgram).where(
                        and_(
                            ReferralProgram.referrer_id == referrer_id,
                            ReferralProgram.referred_id != placeholder_id,  # Исключаем только наш placeholder
                            ReferralProgram.referred_id != 0  # Исключаем нулевые записи
                        )
                    ).order_by(ReferralProgram.created_at.desc())  # Сортируем по дате создания
                )
                referrals = list(result.scalars().all())
            
                logger.info(f"Found {len(referrals)} real referrals for user {referrer_id} (excluding placeholder {placeholder_id})")
            
                # Дополнительно логируем каждого реферала для отладки
                for ref in referrals:
                    logger.debug(f"Referral: referrer={ref.referrer_id}, referred={ref.referred_id}, "
                               f"first_reward_paid={ref.first_reward_paid}, total_earned={ref.total_earned}")
            
                return referrals
            except Exception as e:
                logger.error(f"Error getting user referrals: {e}")
                return []

    async def create_referral_earning(self, referrer_id: int, referred_id: int, 
                                     amount: float, earning_type: str, 
                                     related_payment_id: Optional[int] = None) -> bool:
        """Create referral earning record"""
        async with self.session_factory() as session:
            try:
                earning = ReferralEarning(
                    referrer_id=referrer_id,
                    referred_id=referred_id,
                    amount=amount,
                    earning_type=earning_type,
                    related_payment_id=related_payment_id
                )
                session.add(earning)
                
                # Обновляем общий заработок и статус первой награды в реферальной программе
                from sqlalchemy import select, update
                
                # Найти запись реферальной программы
                referral = await session.execute(
                    select(ReferralProgram).where(
                        ReferralProgram.referrer_id == referrer_id,
                        ReferralProgram.referred_id == referred_id
                    )
                )
                referral_record = referral.scalar_one_or_none()
                
                if referral_record:
                    # Обновляем total_earned
                    referral_record.total_earned += amount
                    
                    # Если это первая награда, помечаем как выплаченную
                    if earning_type == 'first_reward':
                        referral_record.first_reward_paid = True
                        referral_record.first_reward_at = datetime.utcnow()
                    
                    await session.merge(referral_record)
                
                await session.commit()
                return True
                
            except Exception as e:
                logger.error(f"Error creating referral earning: {e}")
                await session.rollback()
                return False
