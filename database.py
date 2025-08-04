from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, String, Float, DateTime, Boolean, Text, Integer
from datetime import datetime
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)

class Base(DeclarativeBase):
    pass

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

class UserSubscription(Base):
    __tablename__ = 'user_subscriptions'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    subscription_id: Mapped[int] = mapped_column(Integer, index=True)
    short_uuid: Mapped[str] = mapped_column(String(255))  # УБРАНО unique=True
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

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
    async def get_all_subscriptions(self, include_inactive: bool = False) -> List[Subscription]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                query = select(Subscription)
                if not include_inactive:
                    query = query.where(Subscription.is_active == True)
                result = await session.execute(query)
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting subscriptions: {e}")
                return []
    
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
                                squad_uuid: str) -> Subscription:
        async with self.session_factory() as session:
            try:
                subscription = Subscription(
                    name=name,
                    description=description,
                    price=price,
                    duration_days=duration_days,
                    traffic_limit_gb=traffic_limit_gb,
                    squad_uuid=squad_uuid
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
                                     short_uuid: str, expires_at: datetime) -> UserSubscription:
        async with self.session_factory() as session:
            try:
                user_sub = UserSubscription(
                    user_id=user_id,
                    subscription_id=subscription_id,
                    short_uuid=short_uuid,
                    expires_at=expires_at
                )
                session.add(user_sub)
                await session.commit()
                await session.refresh(user_sub)
                return user_sub
            except Exception as e:
                logger.error(f"Error creating user subscription: {e}")
                await session.rollback()
                raise
    
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
                
                # Total subscriptions
                total_subs = await session.execute(
                    select(func.count(UserSubscription.id))
                )
                total_subs = total_subs.scalar()
                
                # Total payments
                total_payments = await session.execute(
                    select(func.sum(Payment.amount)).where(Payment.status == 'completed')
                )
                total_payments = total_payments.scalar() or 0
                
                return {
                    'total_users': total_users,
                    'total_subscriptions': total_subs,
                    'total_revenue': total_payments
                }
            except Exception as e:
                logger.error(f"Error getting stats: {e}")
                return {
                    'total_users': 0,
                    'total_subscriptions': 0,
                    'total_revenue': 0
                }
    
    async def update_user_subscription(self, user_sub: UserSubscription) -> UserSubscription:
        async with self.session_factory() as session:
            try:
                await session.merge(user_sub)
                await session.commit()
                return user_sub
            except Exception as e:
                logger.error(f"Error updating user subscription {user_sub.id}: {e}")
                await session.rollback()
                raise

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
