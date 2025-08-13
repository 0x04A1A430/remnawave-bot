from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, String, Float, DateTime, Boolean, Text, Integer, text, select, func, and_, Column
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class Base(DeclarativeBase):
    pass

class ReferralProgram(Base):
    __tablename__ = 'referral_programs'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(BigInteger, index=True)  
    referred_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True) 
    referral_code: Mapped[str] = mapped_column(String(20), index=True)  
    first_reward_paid: Mapped[bool] = mapped_column(Boolean, default=False) 
    total_earned: Mapped[float] = mapped_column(Float, default=0.0) 
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    first_reward_at: Mapped[Optional[datetime]] = mapped_column(DateTime) 

class ReferralEarning(Base):
    __tablename__ = 'referral_earnings'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(BigInteger, index=True) 
    referred_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[float] = mapped_column(Float) 
    earning_type: Mapped[str] = mapped_column(String(20)) 
    related_payment_id: Mapped[Optional[int]] = mapped_column(Integer)  
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
    traffic_limit_gb: Mapped[int] = mapped_column(Integer, default=0) 
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
    short_uuid: Mapped[str] = mapped_column(String(255))  
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    traffic_limit_gb: Mapped[Optional[int]] = mapped_column(Integer)  
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=datetime.utcnow)
    auto_pay_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_pay_days_before: Mapped[int] = mapped_column(Integer, default=3)  

class Payment(Base):
    __tablename__ = 'payments'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[float] = mapped_column(Float)
    payment_type: Mapped[str] = mapped_column(String(50)) 
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default='pending')  
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

class LuckyGame(Base):
    __tablename__ = 'lucky_games'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chosen_number: Mapped[int] = mapped_column(Integer)
    winning_numbers: Mapped[str] = mapped_column(String(255))  
    is_winner: Mapped[bool] = mapped_column(Boolean, default=False)
    reward_amount: Mapped[float] = mapped_column(Float, default=0.0)
    played_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class StarPayment(Base):
    __tablename__ = 'star_payments'
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    stars_amount: Mapped[int] = mapped_column(Integer)  
    rub_amount: Mapped[float] = mapped_column(Float)    
    status: Mapped[str] = mapped_column(String(50), default='pending')  
    telegram_payment_charge_id: Mapped[Optional[str]] = mapped_column(String(255)) 
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class ServiceRule(Base):
    __tablename__ = 'service_rules'
    
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False) 
    content = Column(Text, nullable=False) 
    page_order = Column(Integer, nullable=False, default=1) 
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

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
    
        await self.migrate_user_subscriptions()
        await self.migrate_subscription_imported_field()
        await self.migrate_referral_tables() 
        await self.migrate_star_payments_table()
        await self.migrate_autopay_fields()

    async def toggle_autopay(self, user_subscription_id: int, enabled: bool) -> bool:
        """Включает/выключает автоплатеж для подписки"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import update
                result = await session.execute(
                    update(UserSubscription)
                    .where(UserSubscription.id == user_subscription_id)
                    .values(auto_pay_enabled=enabled)
                )
                await session.commit()
                return result.rowcount > 0
            except Exception as e:
                logger.error(f"Error toggling autopay: {e}")
                await session.rollback()
                return False

    async def set_autopay_days(self, user_subscription_id: int, days_before: int) -> bool:
        """Устанавливает количество дней до истечения для автоплатежа"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import update
                result = await session.execute(
                    update(UserSubscription)
                    .where(UserSubscription.id == user_subscription_id)
                    .values(auto_pay_days_before=days_before)
                )
                await session.commit()
                return result.rowcount > 0
            except Exception as e:
                logger.error(f"Error setting autopay days: {e}")
                await session.rollback()
                return False

    async def get_subscriptions_for_autopay(self, days_threshold: int = None) -> List[UserSubscription]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, and_
                from datetime import datetime, timedelta
            
                if days_threshold is None:
                    current_time = datetime.utcnow()
                
                    conditions = []
                    for days in [1, 2, 3, 5, 7]:
                        threshold_date = current_time + timedelta(days=days)
                        conditions.append(
                            and_(
                                UserSubscription.auto_pay_days_before == days,
                                UserSubscription.expires_at <= threshold_date,
                                UserSubscription.expires_at > current_time
                            )
                        )
                
                    from sqlalchemy import or_
                    query = select(UserSubscription).where(
                        and_(
                            UserSubscription.auto_pay_enabled == True,
                            UserSubscription.is_active == True,
                            or_(*conditions)
                        )
                    )
                else:
                    threshold_date = datetime.utcnow() + timedelta(days=days_threshold)
                    query = select(UserSubscription).where(
                        and_(
                            UserSubscription.auto_pay_enabled == True,
                            UserSubscription.is_active == True,
                            UserSubscription.expires_at <= threshold_date,
                            UserSubscription.expires_at > datetime.utcnow()
                        )
                    )
            
                result = await session.execute(query)
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting subscriptions for autopay: {e}")
                return []

    async def migrate_referral_tables(self):
        try:
            async with self.engine.begin() as conn:
                try:
                    await conn.execute(text("SELECT 1 FROM referral_programs LIMIT 1"))
                    logger.info("referral_programs table already exists")
                except Exception:
                    await conn.execute(text("""
                        CREATE TABLE referral_programs (
                            id SERIAL PRIMARY KEY,
                            referrer_id BIGINT NOT NULL,
                            referred_id BIGINT UNIQUE NOT NULL,
                            referral_code VARCHAR(20) NOT NULL,
                            first_reward_paid BOOLEAN DEFAULT FALSE,
                            total_earned DOUBLE PRECISION DEFAULT 0.0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            first_reward_at TIMESTAMP
                        )
                    """))
                
                    try:
                        await conn.execute(text("CREATE INDEX idx_referrer ON referral_programs(referrer_id)"))
                        await conn.execute(text("CREATE INDEX idx_referred ON referral_programs(referred_id)"))
                        await conn.execute(text("CREATE INDEX idx_referral_code ON referral_programs(referral_code)"))
                    except Exception as e:
                        logger.warning(f"Some referral_programs indexes may already exist: {e}")
                
                    logger.info("Created referral_programs table")
            
                try:
                    await conn.execute(text("SELECT 1 FROM referral_earnings LIMIT 1"))
                    logger.info("referral_earnings table already exists")
                except Exception:
                    await conn.execute(text("""
                        CREATE TABLE referral_earnings (
                            id SERIAL PRIMARY KEY,
                            referrer_id BIGINT NOT NULL,
                            referred_id BIGINT NOT NULL,
                            amount DOUBLE PRECISION NOT NULL,
                            earning_type VARCHAR(20) NOT NULL,
                            related_payment_id INTEGER,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """))
                
                    try:
                        await conn.execute(text("CREATE INDEX idx_referrer_earnings ON referral_earnings(referrer_id)"))
                        await conn.execute(text("CREATE INDEX idx_referred_earnings ON referral_earnings(referred_id)"))
                        await conn.execute(text("CREATE INDEX idx_earning_type ON referral_earnings(earning_type)"))
                    except Exception as e:
                        logger.warning(f"Some referral_earnings indexes may already exist: {e}")
                
                    logger.info("Created referral_earnings table")
            
                logger.info("Successfully created referral system tables")
        except Exception as e:
            logger.error(f"Error creating referral tables: {e}")
    
    async def close(self):
        await self.engine.dispose()
    
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
                    query = query.where(Subscription.is_imported == False)
                result = await session.execute(query)
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting subscriptions: {e}")
                return []

    async def get_all_subscriptions_admin(self) -> List[Subscription]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(select(Subscription))
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting admin subscriptions: {e}")
                return []

    async def migrate_subscription_imported_field(self):
        try:
            async with self.engine.begin() as conn:
                try:
                    await conn.execute(text("SELECT is_imported FROM subscriptions LIMIT 1"))
                    logger.info("is_imported field already exists")
                except Exception:
                    try:
                        await conn.execute(text("""
                            ALTER TABLE subscriptions 
                            ADD COLUMN is_imported BOOLEAN DEFAULT FALSE
                        """))
                        logger.info("Successfully added is_imported field to subscriptions table")
                    except Exception as e:
                        logger.warning(f"Error adding is_imported field: {e}")
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
                    is_imported=is_imported  
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
        async with self.session_factory() as session:
            try:
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
                from sqlalchemy import select
                existing = await session.execute(
                    select(PromocodeUsage).where(
                        PromocodeUsage.user_id == user_id,
                        PromocodeUsage.promocode_id == promocode.id
                    )
                )
                if existing.scalar_one_or_none():
                    return False
                
                usage = PromocodeUsage(user_id=user_id, promocode_id=promocode.id)
                session.add(usage)
                
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
            
                total_users = await session.execute(
                    select(func.count(User.id))
                )
                total_users = total_users.scalar()
            
                total_subs_non_trial = await session.execute(
                    select(func.count(UserSubscription.id))
                    .join(Subscription, UserSubscription.subscription_id == Subscription.id)
                    .where(Subscription.is_trial == False)
                )
                total_subs_non_trial = total_subs_non_trial.scalar()
            
                total_payments = await session.execute(
                    select(func.sum(Payment.amount)).where(
                        Payment.status == 'completed',
                        Payment.payment_type != 'trial'  
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
        async with self.session_factory() as session:
            try:
                user_subscription.updated_at = datetime.utcnow()
            
                await session.merge(user_subscription)
                await session.commit()
                return True
            except Exception as e:
                logger.error(f"Error updating user subscription: {e}")
                await session.rollback()
                return False

    async def migrate_user_subscriptions(self):
        try:
            async with self.engine.begin() as conn:
                fields_to_add = [
                    ("traffic_limit_gb", "INTEGER"),
                    ("updated_at", "TIMESTAMP")
                ]
            
                for field_name, field_type in fields_to_add:
                    try:
                        await conn.execute(text(f"SELECT {field_name} FROM user_subscriptions LIMIT 1"))
                    except Exception:
                        try:
                            await conn.execute(text(f"""
                                ALTER TABLE user_subscriptions 
                                ADD COLUMN {field_name} {field_type}
                            """))
                            logger.info(f"Added {field_name} column to user_subscriptions")
                        except Exception as e:
                            logger.warning(f"Error adding {field_name} column: {e}")
            
                logger.info("Successfully migrated user_subscriptions table")
        except Exception as e:
            logger.error(f"Error during user_subscriptions migration: {e}")

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
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, desc, func
        
                count_result = await session.execute(
                    select(func.count(Payment.id))
                )
                total_count = count_result.scalar()
        
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
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, desc, func
        
                count_result = await session.execute(
                    select(func.count(Payment.id)).where(Payment.payment_type == payment_type)
                )
                total_count = count_result.scalar()
        
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
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, desc, func
            
                count_result = await session.execute(
                    select(func.count(Payment.id)).where(Payment.status == status)
                )
                total_count = count_result.scalar()
            
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
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
        
                if referred_id == 0:
                    placeholder_id = 999999999 - referrer_id  
            
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
            
                    referral = ReferralProgram(
                        referrer_id=referrer_id,
                        referred_id=placeholder_id,  
                        referral_code=referral_code
                    )
                    session.add(referral)
                    await session.commit()
                    await session.refresh(referral)
                    logger.info(f"Created referral code storage for user {referrer_id}")
                    return referral
            
                existing = await session.execute(
                    select(ReferralProgram).where(
                        ReferralProgram.referred_id == referred_id,
                        ReferralProgram.referred_id < 900000000, 
                        ReferralProgram.referred_id > 0  
                    )
                )
                if existing.scalar_one_or_none():
                    logger.info(f"User {referred_id} already has a real referrer")
                    return None
    
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
            
                logger.info(f"Created real referral: {referrer_id} -> {referred_id}")
                return referral
            
            except Exception as e:
                logger.error(f"Error creating referral: {e}")
                await session.rollback()
                return None

    async def get_referral_by_referred_id(self, referred_id: int) -> Optional[ReferralProgram]:
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
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, and_
                result = await session.execute(
                    select(ReferralProgram).where(
                        and_(
                            ReferralProgram.referrer_id == referrer_id,
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
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
        
                if referred_id == 0:
                    placeholder_id = 999999999 - referrer_id  
            
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
            
                    referral = ReferralProgram(
                        referrer_id=referrer_id,
                        referred_id=placeholder_id,  
                        referral_code=referral_code
                    )
                    session.add(referral)
                    await session.commit()
                    await session.refresh(referral)
                    return referral
        
                existing = await session.execute(
                    select(ReferralProgram).where(ReferralProgram.referred_id == referred_id)
                )
                if existing.scalar_one_or_none():
                    logger.info(f"User {referred_id} already has a referrer")
                    return None
    
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
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, func, and_, or_
        
                placeholder_id = 999999999 - user_id
            
                referrals_count = await session.execute(
                    select(func.count(ReferralProgram.id))
                    .where(
                        and_(
                            ReferralProgram.referrer_id == user_id,
                            ReferralProgram.referred_id != placeholder_id,  
                            ReferralProgram.referred_id != 0  
                        )
                    )
                )
        
                active_referrals = await session.execute(
                    select(func.count(ReferralProgram.id))
                    .where(
                        and_(
                            ReferralProgram.referrer_id == user_id,
                            ReferralProgram.first_reward_paid == True,
                            ReferralProgram.referred_id != placeholder_id,  
                            ReferralProgram.referred_id != 0  
                        )
                    )
                )
            
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
        async with self.session_factory() as session:
            try:
                import secrets
                import string
            
                base_code = f"REF{user_id}"
            
                from sqlalchemy import select
                existing = await session.execute(
                    select(ReferralProgram).where(ReferralProgram.referral_code == base_code)
                )
            
                if not existing.scalar_one_or_none():
                    return base_code
            
                for _ in range(10):
                    random_suffix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
                    code = f"REF{user_id}{random_suffix}"
                
                    existing = await session.execute(
                        select(ReferralProgram).where(ReferralProgram.referral_code == code)
                    )
                
                    if not existing.scalar_one_or_none():
                        return code
            
                return f"REF{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))}"
            
            except Exception as e:
                logger.error(f"Error generating referral code: {e}")
                return f"REF{user_id}ERR"

    async def get_user_referrals(self, referrer_id: int) -> List[ReferralProgram]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, and_
            
                placeholder_id = 999999999 - referrer_id
            
                result = await session.execute(
                    select(ReferralProgram).where(
                        and_(
                            ReferralProgram.referrer_id == referrer_id,
                            ReferralProgram.referred_id != placeholder_id,
                            ReferralProgram.referred_id != 0
                        )
                    ).order_by(ReferralProgram.created_at.desc()) 
                )
                referrals = list(result.scalars().all())
            
                logger.info(f"Found {len(referrals)} real referrals for user {referrer_id} (excluding placeholder {placeholder_id})")
            
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
                
                from sqlalchemy import select, update
                
                referral = await session.execute(
                    select(ReferralProgram).where(
                        ReferralProgram.referrer_id == referrer_id,
                        ReferralProgram.referred_id == referred_id
                    )
                )
                referral_record = referral.scalar_one_or_none()
                
                if referral_record:
                    referral_record.total_earned += amount
                    
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

    async def get_promocode_by_id(self, promocode_id: int) -> Optional[Promocode]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(Promocode).where(Promocode.id == promocode_id)
                )
                return result.scalar_one_or_none()
            except Exception as e:
                logger.error(f"Error getting promocode by ID {promocode_id}: {e}")
                return None

    async def update_promocode(self, promocode: Promocode) -> Promocode:
        async with self.session_factory() as session:
            try:
                await session.merge(promocode)
                await session.commit()
                return promocode
            except Exception as e:
                logger.error(f"Error updating promocode {promocode.id}: {e}")
                await session.rollback()
                raise

    async def delete_promocode(self, promocode_id: int) -> bool:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import delete
            
                await session.execute(
                    delete(PromocodeUsage).where(PromocodeUsage.promocode_id == promocode_id)
                )
            
                result = await session.execute(
                    delete(Promocode).where(Promocode.id == promocode_id)
                )
            
                await session.commit()
                return result.rowcount > 0
            except Exception as e:
                logger.error(f"Error deleting promocode {promocode_id}: {e}")
                await session.rollback()
                return False

    async def get_regular_promocodes(self, include_inactive: bool = True) -> List[Promocode]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, and_
            
                query = select(Promocode).where(~Promocode.code.startswith('REF'))
            
                if not include_inactive:
                    query = query.where(Promocode.is_active == True)
            
                result = await session.execute(query.order_by(Promocode.created_at.desc()))
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting regular promocodes: {e}")
                return []

    async def get_expired_promocodes(self) -> List[Promocode]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, and_
                from datetime import datetime
            
                result = await session.execute(
                    select(Promocode).where(
                        and_(
                            Promocode.expires_at < datetime.utcnow(),
                            ~Promocode.code.startswith('REF')
                        )
                    )
                )
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting expired promocodes: {e}")
                return []

    async def cleanup_expired_promocodes(self) -> int:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import delete, and_
                from datetime import datetime
            
                expired_promos = await session.execute(
                    select(Promocode.id).where(
                        and_(
                            Promocode.expires_at < datetime.utcnow(),
                            ~Promocode.code.startswith('REF')
                        )
                    )
                )
                expired_ids = [row[0] for row in expired_promos.fetchall()]
            
                if not expired_ids:
                    return 0
            
                await session.execute(
                    delete(PromocodeUsage).where(PromocodeUsage.promocode_id.in_(expired_ids))
                )
            
                result = await session.execute(
                    delete(Promocode).where(Promocode.id.in_(expired_ids))
                )
            
                await session.commit()
                return result.rowcount
            
            except Exception as e:
                logger.error(f"Error cleaning up expired promocodes: {e}")
                await session.rollback()
                return 0

    async def deactivate_all_regular_promocodes(self) -> int:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import update, and_
            
                result = await session.execute(
                    update(Promocode)
                    .where(
                        and_(
                            ~Promocode.code.startswith('REF'), 
                            Promocode.is_active == True
                        )
                    )
                    .values(is_active=False)
                )
            
                await session.commit()
                return result.rowcount
                
            except Exception as e:
                logger.error(f"Error deactivating all promocodes: {e}")
                await session.rollback()
                return 0

    async def get_promocode_stats(self) -> Dict:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, func, and_
                from datetime import datetime
            
                total_promos = await session.execute(
                    select(func.count(Promocode.id)).where(~Promocode.code.startswith('REF'))
                )
                total_count = total_promos.scalar() or 0
            
                active_promos = await session.execute(
                    select(func.count(Promocode.id)).where(
                        and_(
                            ~Promocode.code.startswith('REF'),
                            Promocode.is_active == True
                        )
                    )
                )
                active_count = active_promos.scalar() or 0
                
                expired_promos = await session.execute(
                    select(func.count(Promocode.id)).where(
                        and_(
                            ~Promocode.code.startswith('REF'),
                            Promocode.expires_at < datetime.utcnow()
                        )
                    )
                )
                expired_count = expired_promos.scalar() or 0
            
                total_usage = await session.execute(
                    select(func.sum(Promocode.used_count)).where(~Promocode.code.startswith('REF'))
                )
                usage_count = total_usage.scalar() or 0
            
                total_discount = await session.execute(
                    select(func.sum(Promocode.discount_amount * Promocode.used_count)).where(
                        ~Promocode.code.startswith('REF')
                    )
                )
                discount_amount = total_discount.scalar() or 0.0
            
                top_promos = await session.execute(
                    select(Promocode.code, Promocode.used_count, Promocode.discount_amount)
                    .where(~Promocode.code.startswith('REF'))
                    .order_by(Promocode.used_count.desc())
                    .limit(5)
                )
                top_promocodes = list(top_promos.fetchall())
            
                return {
                    'total_promocodes': total_count,
                    'active_promocodes': active_count,
                    'expired_promocodes': expired_count,
                    'total_usage': usage_count,
                    'total_discount_amount': discount_amount,
                    'top_promocodes': top_promocodes
                }
            
            except Exception as e:
                logger.error(f"Error getting promocode stats: {e}")
                return {
                    'total_promocodes': 0,
                    'active_promocodes': 0,
                    'expired_promocodes': 0,
                    'total_usage': 0,
                    'total_discount_amount': 0.0,
                    'top_promocodes': []
                }

    async def get_promocode_usage_by_id(self, promocode_id: int) -> List[PromocodeUsage]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
            
                result = await session.execute(
                    select(PromocodeUsage)
                    .where(PromocodeUsage.promocode_id == promocode_id)
                    .order_by(PromocodeUsage.used_at.desc())
                )
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting promocode usage for {promocode_id}: {e}")
                return []

    async def create_lucky_game(self, user_id: int, chosen_number: int, 
                               winning_numbers: List[int], is_winner: bool, 
                               reward_amount: float) -> Optional['LuckyGame']:
        async with self.session_factory() as session:
            try:
                game = LuckyGame(
                    user_id=user_id,
                    chosen_number=chosen_number,
                    winning_numbers=winning_numbers,
                    is_winner=is_winner,
                    reward_amount=reward_amount
                )
                
                session.add(game)
                await session.commit()
                await session.refresh(game)
            
                logger.info(f"Lucky game created: user_id={user_id}, chosen_number={chosen_number}, is_winner={is_winner}")
                return game
            
            except Exception as e:
                await session.rollback()
                logger.error(f"Error creating lucky game: {e}")
                return None

    async def get_user_last_game_today(self, user_id: int) -> Optional[LuckyGame]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, and_
                from datetime import date
                
                today = date.today()
                result = await session.execute(
                    select(LuckyGame)
                    .where(
                        and_(
                            LuckyGame.user_id == user_id,
                            LuckyGame.played_at >= datetime.combine(today, datetime.min.time()),
                            LuckyGame.played_at < datetime.combine(today + timedelta(days=1), datetime.min.time())
                        )
                    )
                    .order_by(LuckyGame.played_at.desc())
                    .limit(1)
                )
                return result.scalar_one_or_none()
            except Exception as e:
                logger.error(f"Error getting user last game today: {e}")
                return None

    async def get_user_game_stats(self, user_id: int) -> Dict[str, Any]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, func, and_ 
            
                games_count = await session.execute(
                    select(func.count(LuckyGame.id))
                    .where(LuckyGame.user_id == user_id)
                )
                total_games = games_count.scalar() or 0
            
                wins_count = await session.execute(
                    select(func.count(LuckyGame.id))
                    .where(
                        and_(  
                            LuckyGame.user_id == user_id,
                            LuckyGame.is_winner == True
                        )
                    )
                )
                total_wins = wins_count.scalar() or 0
                
                total_reward = await session.execute(
                    select(func.sum(LuckyGame.reward_amount))
                    .where(LuckyGame.user_id == user_id)
                )
                total_won = total_reward.scalar() or 0.0
                
                return {
                    'total_games': total_games,
                    'total_wins': total_wins,
                    'total_won': total_won,
                    'win_rate': (total_wins / total_games * 100) if total_games > 0 else 0
                }
            except Exception as e:
                logger.error(f"Error getting user game stats: {e}")
                return {
                    'total_games': 0,
                    'total_wins': 0, 
                    'total_won': 0.0,
                    'win_rate': 0
                }

    async def get_user_game_history(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                import json
                
                result = await session.execute(
                    select(LuckyGame)
                    .where(LuckyGame.user_id == user_id)
                    .order_by(LuckyGame.played_at.desc())
                    .limit(limit)
                )
                games = result.scalars().all()
                
                history = []
                for game in games:
                    winning_numbers = json.loads(game.winning_numbers) if game.winning_numbers else []
                    history.append({
                        'id': game.id,
                        'chosen_number': game.chosen_number,
                        'winning_numbers': winning_numbers,
                        'is_winner': game.is_winner,
                        'reward_amount': game.reward_amount,
                        'played_at': game.played_at
                    })
                
                return history
            except Exception as e:
                logger.error(f"Error getting user game history: {e}")
                return []

    async def can_play_lucky_game_today(self, user_id: int) -> bool:
        try:
            last_game = await self.get_user_last_game_today(user_id)
            return last_game is None
        except Exception as e:
            logger.error(f"Error checking can play today: {e}")
            return True 

    async def create_star_payment(self, user_id: int, stars_amount: int, rub_amount: float) -> StarPayment:
        """Создать платеж через звезды"""
        async with self.session_factory() as session:
            try:
                payment = StarPayment(
                    user_id=user_id,
                    stars_amount=stars_amount,
                    rub_amount=rub_amount,
                    status='pending'
                )
                session.add(payment)
                await session.commit()
                await session.refresh(payment)
                return payment
            except Exception as e:
                logger.error(f"Error creating star payment: {e}")
                await session.rollback()
                raise

    async def get_star_payment_by_id(self, payment_id: int) -> Optional[StarPayment]:
        """Получить платеж через звезды по ID"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
                result = await session.execute(
                    select(StarPayment).where(StarPayment.id == payment_id)
                )
                return result.scalar_one_or_none()
            except Exception as e:
                logger.error(f"Error getting star payment {payment_id}: {e}")
                return None

    async def complete_star_payment(self, payment_id: int, telegram_payment_charge_id: str) -> bool:
        """Завершить платеж через звезды"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import update
            
                result = await session.execute(
                    update(StarPayment)
                    .where(StarPayment.id == payment_id)
                    .values(
                        status='completed',
                        telegram_payment_charge_id=telegram_payment_charge_id,
                        completed_at=datetime.utcnow()
                    )
                )
            
                if result.rowcount == 0:
                    return False
            
                payment_result = await session.execute(
                    select(StarPayment).where(StarPayment.id == payment_id)
                )
                payment = payment_result.scalar_one_or_none()
            
                if not payment:
                    return False
            
                balance_result = await session.execute(
                    update(User)
                    .where(User.telegram_id == payment.user_id)
                    .values(balance=User.balance + payment.rub_amount)
                )
            
                regular_payment = Payment(
                    user_id=payment.user_id,
                    amount=payment.rub_amount,
                    payment_type='stars',
                    description=f'Пополнение через Telegram Stars ({payment.stars_amount} ⭐)',
                    status='completed'
                )
                session.add(regular_payment)
            
                await session.commit()
                return True
            
            except Exception as e:
                logger.error(f"Error completing star payment {payment_id}: {e}")
                await session.rollback()
                return False

    async def cancel_star_payment(self, payment_id: int) -> bool:
        """Отменить платеж через звезды"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import update
                result = await session.execute(
                    update(StarPayment)
                    .where(StarPayment.id == payment_id)
                    .values(status='cancelled')
                )
                await session.commit()
                return result.rowcount > 0
            except Exception as e:
                logger.error(f"Error cancelling star payment {payment_id}: {e}")
                await session.rollback()
                return False

    async def get_user_star_payments(self, user_id: int, limit: int = 10) -> List[StarPayment]:
        """Получить платежи пользователя через звезды"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, desc
                result = await session.execute(
                    select(StarPayment)
                    .where(StarPayment.user_id == user_id)
                    .order_by(desc(StarPayment.created_at))
                    .limit(limit)
                )
                return list(result.scalars().all())
            except Exception as e:
                logger.error(f"Error getting user star payments for {user_id}: {e}")
                return []

    async def migrate_star_payments_table(self):
        try:
            async with self.engine.begin() as conn:
                try:
                    await conn.execute(text("SELECT 1 FROM star_payments LIMIT 1"))
                    logger.info("star_payments table already exists")
                    return
                except Exception:
                    pass
            
                await conn.execute(text("""
                    CREATE TABLE star_payments (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        stars_amount INTEGER NOT NULL,
                        rub_amount DOUBLE PRECISION NOT NULL,
                        status VARCHAR(50) DEFAULT 'pending',
                        telegram_payment_charge_id VARCHAR(255),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        completed_at TIMESTAMP
                    )
                """))
            
                try:
                    await conn.execute(text("CREATE INDEX idx_star_payments_user ON star_payments(user_id)"))
                    await conn.execute(text("CREATE INDEX idx_star_payments_status ON star_payments(status)"))
                except Exception as e:
                    logger.warning(f"Some indexes may already exist: {e}")
            
                logger.info("Successfully created star_payments table")
        except Exception as e:
            logger.error(f"Error creating star_payments table: {e}")

    async def create_service_rule(self, title: str, content: str, page_order: int = None) -> ServiceRule:
        async with self.session_factory() as session:
            if page_order is None:
                result = await session.execute(
                    select(func.max(ServiceRule.page_order)).where(ServiceRule.is_active == True)
                )
                max_order = result.scalar() or 0
                page_order = max_order + 1
        
            rule = ServiceRule(
                title=title,
                content=content,
                page_order=page_order
            )
            session.add(rule)
            await session.commit()
            await session.refresh(rule)
            return rule

    async def get_all_service_rules(self, active_only: bool = True) -> List[ServiceRule]:
        async with self.session_factory() as session:
            query = select(ServiceRule).order_by(ServiceRule.page_order)
            if active_only:
                query = query.where(ServiceRule.is_active == True)
        
            result = await session.execute(query)
            return result.scalars().all()

    async def get_service_rule_by_id(self, rule_id: int) -> Optional[ServiceRule]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(ServiceRule).where(ServiceRule.id == rule_id)
            )
            return result.scalar_one_or_none()

    async def update_service_rule(self, rule: ServiceRule) -> bool:
        try:
            async with self.session_factory() as session:
                await session.merge(rule)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Error updating service rule: {e}")
            return False

    async def delete_service_rule(self, rule_id: int) -> bool:
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(ServiceRule).where(ServiceRule.id == rule_id)
                )
                rule = result.scalar_one_or_none()
                if rule:
                    await session.delete(rule)
                    await session.commit()
                    return True
                return False
        except Exception as e:
            logger.error(f"Error deleting service rule: {e}")
            return False

    async def reorder_service_rules(self, rule_orders: List[tuple]) -> bool:
        try:
            async with self.session_factory() as session:
                for rule_id, new_order in rule_orders:
                    await session.execute(
                        update(ServiceRule).where(ServiceRule.id == rule_id).values(page_order=new_order)
                    )
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Error reordering service rules: {e}")
            return False

    async def migrate_service_rules_table(self):
        try:
            async with self.engine.begin() as conn:
                try:
                    await conn.execute(text("SELECT 1 FROM service_rules LIMIT 1"))
                    logger.info("service_rules table already exists")
                    return
                except Exception:
                    pass
                
                await conn.execute(text("""
                    CREATE TABLE service_rules (
                        id SERIAL PRIMARY KEY,
                        title VARCHAR(200) NOT NULL,
                        content TEXT NOT NULL,
                        page_order INTEGER NOT NULL DEFAULT 1,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
            
                try:
                    await conn.execute(text("CREATE INDEX idx_service_rules_order ON service_rules(page_order)"))
                    await conn.execute(text("CREATE INDEX idx_service_rules_active ON service_rules(is_active)"))
                except Exception as e:
                    logger.warning(f"Some indexes may already exist: {e}")
            
                check_result = await conn.execute(text("SELECT COUNT(*) FROM service_rules"))
                count = check_result.scalar()
            
                if count == 0:
                    await conn.execute(text("""
                        INSERT INTO service_rules (title, content, page_order) VALUES 
                        ('Общие положения', 
                        '**1. Общие положения**

    Настоящие Правила определяют условия использования VPN-сервиса.

    **1.1** Используя наш сервис, вы соглашаетесь с данными правилами.

    **1.2** Мы оставляем за собой право изменять правила в любое время.

    **1.3** Продолжение использования сервиса после изменений означает ваше согласие с новыми условиями.', 
                        1),

                        ('Права и обязанности', 
                        '**2. Права и обязанности пользователей**

    **2.1 Права пользователя:**
    • Использовать VPN-сервис в соответствии с тарифным планом
    • Получать техническую поддержку
    • Защиту персональных данных

    **2.2 Обязанности пользователя:**
    • Не использовать сервис для незаконной деятельности
    • Не передавать данные доступа третьим лицам
    • Своевременно оплачивать услуги

    **2.3 Запрещается:**
    • Попытки взлома или нарушения работы сервиса
    • Спам и рассылка нежелательных сообщений
    • Нарушение авторских прав', 
                        2),

                        ('Оплата и возврат средств', 
                        '**3. Условия оплаты и возврата**

    **3.1 Оплата:**
    • Все платежи производятся в российских рублях
    • Доступны различные способы оплаты
    • Средства зачисляются автоматически или в течение 24 часов

    **3.2 Возврат средств:**
    • Возврат возможен в течение 7 дней с момента оплаты
    • При технических проблемах возврат производится полностью
    • Обращайтесь в поддержку для возврата

    **3.3 Скидки и промокоды:**
    • Действуют ограничения по времени и количеству использований
    • Нельзя комбинировать несколько скидок', 
                        3)
                    """))
                    logger.info("Inserted default service rules")
            
                logger.info("Successfully created service_rules table")
        except Exception as e:
            logger.error(f"Error creating service_rules table: {e}")

    async def migrate_autopay_fields(self):
        try:
            async with self.engine.begin() as conn:
                try:
                    await conn.execute(text("SELECT auto_pay_enabled FROM user_subscriptions LIMIT 1"))
                    logger.info("auto_pay_enabled field already exists")
                except Exception:
                    try:
                        await conn.execute(text("""
                            ALTER TABLE user_subscriptions 
                            ADD COLUMN auto_pay_enabled BOOLEAN DEFAULT FALSE
                        """))
                        logger.info("Added auto_pay_enabled column")
                    except Exception as e:
                        logger.error(f"Error adding auto_pay_enabled column: {e}")
                        pass
        
            async with self.engine.begin() as conn:
                try:
                    await conn.execute(text("SELECT auto_pay_days_before FROM user_subscriptions LIMIT 1"))
                    logger.info("auto_pay_days_before field already exists")
                except Exception:
                    try:
                        await conn.execute(text("""
                            ALTER TABLE user_subscriptions 
                            ADD COLUMN auto_pay_days_before INTEGER DEFAULT 3
                        """))
                        logger.info("Added auto_pay_days_before column")
                    except Exception as e:
                        logger.error(f"Error adding auto_pay_days_before column: {e}")
                        pass
        
            logger.info("Successfully completed autopay fields migration")
        
        except Exception as e:
            logger.error(f"Error during autopay migration: {e}")


    async def get_autopay_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, desc
            
                result = await session.execute(
                    select(Payment)
                    .where(Payment.payment_type == 'autopay')
                    .order_by(desc(Payment.created_at))
                    .limit(limit)
                )
                
                payments = result.scalars().all()
            
                autopay_history = []
                
                for payment in payments:
                    try:
                        user_result = await session.execute(
                            select(User).where(User.telegram_id == payment.user_id)
                        )
                        user_obj = user_result.scalar_one_or_none()
                    
                        autopay_history.append({
                            'payment_id': payment.id,
                            'user_id': payment.user_id,
                            'username': user_obj.username if user_obj else 'N/A',
                            'first_name': user_obj.first_name if user_obj else 'N/A',
                            'amount': payment.amount,
                            'description': payment.description,
                            'status': payment.status,
                            'created_at': payment.created_at
                        })
                    
                    except Exception as e:
                        logger.warning(f"Error processing autopay history for payment {payment.id}: {e}")
                        continue
            
                return autopay_history
            
            except Exception as e:
                logger.error(f"Error getting autopay history: {e}")
                return []

    async def disable_autopay_for_user(self, user_id: int) -> int:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import update
            
                result = await session.execute(
                    update(UserSubscription)
                    .where(
                        and_(
                            UserSubscription.user_id == user_id,
                            UserSubscription.auto_pay_enabled == True
                        )
                    )
                    .values(auto_pay_enabled=False)
                )
            
                await session.commit()
                return result.rowcount
            
            except Exception as e:
                logger.error(f"Error disabling autopay for user {user_id}: {e}")
                await session.rollback()
                return 0

    async def get_autopay_subscription_by_id(self, subscription_id: int) -> Optional[UserSubscription]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
            
                result = await session.execute(
                    select(UserSubscription).where(
                        and_(
                            UserSubscription.id == subscription_id,
                            UserSubscription.auto_pay_enabled == True
                        )
                    )
                )
            
                return result.scalar_one_or_none()
            
            except Exception as e:
                logger.error(f"Error getting autopay subscription {subscription_id}: {e}")
                return None

    async def get_autopay_statistics(self) -> Dict[str, Any]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, func, and_, case
                from datetime import datetime, timedelta
            
                total_autopay = await session.execute(
                    select(func.count(UserSubscription.id))
                    .where(UserSubscription.auto_pay_enabled == True)
                )
                total_autopay_subscriptions = total_autopay.scalar() or 0
            
                active_autopay = await session.execute(
                    select(func.count(UserSubscription.id))
                    .where(
                        and_(
                            UserSubscription.auto_pay_enabled == True,
                            UserSubscription.is_active == True,
                            UserSubscription.expires_at > datetime.utcnow()
                        )
                    )
                )
                active_autopay_subscriptions = active_autopay.scalar() or 0
            
                expired_autopay = await session.execute(
                    select(func.count(UserSubscription.id))
                    .where(
                        and_(
                            UserSubscription.auto_pay_enabled == True,
                            UserSubscription.expires_at <= datetime.utcnow()
                        )
                    )
                )
                expired_autopay_subscriptions = expired_autopay.scalar() or 0
            
                ready_for_autopay = []
                current_time = datetime.utcnow()
            
                for days in [1, 2, 3, 5, 7]:
                    threshold_date = current_time + timedelta(days=days)
                
                    ready_count = await session.execute(
                        select(func.count(UserSubscription.id))
                        .where(
                            and_(
                                UserSubscription.auto_pay_enabled == True,
                                UserSubscription.is_active == True,
                                UserSubscription.auto_pay_days_before == days,
                                UserSubscription.expires_at <= threshold_date,
                                UserSubscription.expires_at > current_time
                            )
                        )
                    )
                
                    count = ready_count.scalar() or 0
                    ready_for_autopay.append({
                        'days': days,
                        'count': count
                    })
            
                return {
                    'total_autopay_subscriptions': total_autopay_subscriptions,
                    'active_autopay_subscriptions': active_autopay_subscriptions,
                    'expired_autopay_subscriptions': expired_autopay_subscriptions,
                    'ready_for_autopay': ready_for_autopay
                }
            
            except Exception as e:
                logger.error(f"Error getting autopay statistics: {e}")
                return {
                    'total_autopay_subscriptions': 0,
                    'active_autopay_subscriptions': 0,
                    'expired_autopay_subscriptions': 0,
                    'ready_for_autopay': []
                }

    async def get_users_with_insufficient_autopay_balance(self) -> List[Dict[str, Any]]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, and_
                from datetime import datetime, timedelta
            
                current_time = datetime.utcnow()
            
                insufficient_users = []
            
                autopay_subs = await session.execute(
                    select(UserSubscription)
                    .where(
                        and_(
                            UserSubscription.auto_pay_enabled == True,
                            UserSubscription.is_active == True,
                            UserSubscription.expires_at > current_time
                        )
                    )
                )
            
                for user_sub in autopay_subs.scalars().all():
                    try:
                        threshold_date = current_time + timedelta(days=user_sub.auto_pay_days_before)
                    
                        if user_sub.expires_at <= threshold_date:
                            user_result = await session.execute(
                                select(User).where(User.telegram_id == user_sub.user_id)
                            )
                            user_obj = user_result.scalar_one_or_none()
                        
                            if not user_obj:
                                continue
                        
                            sub_result = await session.execute(
                                select(Subscription).where(Subscription.id == user_sub.subscription_id)
                            )
                            subscription = sub_result.scalar_one_or_none()
                        
                            if not subscription:
                                continue
                        
                            if user_obj.balance < subscription.price:
                                days_left = (user_sub.expires_at - current_time).days
                                needed_amount = subscription.price - user_obj.balance
                            
                                insufficient_users.append({
                                    'user_id': user_obj.telegram_id,
                                    'username': user_obj.username or 'N/A',
                                    'first_name': user_obj.first_name or 'N/A',
                                    'current_balance': user_obj.balance,
                                    'subscription_price': subscription.price,
                                    'needed_amount': needed_amount,
                                    'subscription_name': subscription.name,
                                    'expires_in_days': days_left,
                                    'auto_pay_days_before': user_sub.auto_pay_days_before
                                })
                
                    except Exception as e:
                        logger.warning(f"Error processing user subscription {user_sub.id}: {e}")
                        continue
            
                return insufficient_users
            
            except Exception as e:
                logger.error(f"Error getting users with insufficient autopay balance: {e}")
                return []

    async def get_all_user_subscriptions_admin(self, offset: int = 0, limit: int = 20, 
                                             filter_type: str = "all") -> tuple[List[Dict], int]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, func, desc, and_, or_
                from datetime import datetime, timedelta
            
                base_query = select(
                    UserSubscription,
                    Subscription.name.label('subscription_name'),
                    Subscription.price.label('subscription_price'),
                    Subscription.is_trial.label('is_trial'),
                    Subscription.is_imported.label('is_imported'),
                    User.username.label('user_username'),
                    User.first_name.label('user_first_name'),
                    User.telegram_id.label('user_telegram_id')
                ).select_from(
                    UserSubscription.__table__.join(
                        Subscription.__table__, UserSubscription.subscription_id == Subscription.id
                    ).join(
                        User.__table__, UserSubscription.user_id == User.telegram_id
                    )
                )
            
                current_time = datetime.utcnow()
            
                if filter_type == "active":
                    base_query = base_query.where(
                        and_(
                            UserSubscription.is_active == True,
                            UserSubscription.expires_at > current_time
                        )
                    )
                elif filter_type == "expired":
                    base_query = base_query.where(
                        or_(
                            UserSubscription.is_active == False,
                            UserSubscription.expires_at <= current_time
                        )
                    )
                elif filter_type == "expiring":
                    expiring_date = current_time + timedelta(days=7)
                    base_query = base_query.where(
                        and_(
                            UserSubscription.is_active == True,
                            UserSubscription.expires_at > current_time,
                            UserSubscription.expires_at <= expiring_date
                        )
                    )
                elif filter_type == "autopay":
                    base_query = base_query.where(UserSubscription.auto_pay_enabled == True)
                elif filter_type == "trial":
                    base_query = base_query.where(Subscription.is_trial == True)
                elif filter_type == "imported":
                    base_query = base_query.where(Subscription.is_imported == True)
            
                count_query = select(func.count()).select_from(base_query.subquery())
                total_count_result = await session.execute(count_query)
                total_count = total_count_result.scalar() or 0
            
                data_query = base_query.order_by(desc(UserSubscription.created_at)).offset(offset).limit(limit)
                result = await session.execute(data_query)
            
                subscriptions_data = []
                for row in result.fetchall():
                    user_sub = row[0] 
                
                    if user_sub.expires_at <= current_time:
                        status = "expired"
                    elif not user_sub.is_active:
                        status = "inactive"
                    else:
                        days_left = (user_sub.expires_at - current_time).days
                        if days_left <= 3:
                            status = "expiring_soon"
                        elif days_left <= 7:
                            status = "expiring"
                        else:
                            status = "active"
                
                    subscriptions_data.append({
                        'id': user_sub.id,
                        'user_id': row.user_telegram_id,
                        'user_username': row.user_username or 'N/A',
                        'user_first_name': row.user_first_name or 'N/A',
                        'subscription_name': row.subscription_name,
                        'subscription_price': row.subscription_price,
                        'short_uuid': user_sub.short_uuid,
                        'expires_at': user_sub.expires_at,
                        'is_active': user_sub.is_active,
                        'auto_pay_enabled': user_sub.auto_pay_enabled,
                        'auto_pay_days_before': user_sub.auto_pay_days_before,
                        'created_at': user_sub.created_at,
                        'is_trial': row.is_trial,
                        'is_imported': row.is_imported,
                        'status': status,
                        'days_left': (user_sub.expires_at - current_time).days if user_sub.expires_at > current_time else 0
                    })
            
                return subscriptions_data, total_count
            
            except Exception as e:
                logger.error(f"Error getting admin user subscriptions: {e}")
                return [], 0

    async def get_user_subscription_detail_admin(self, subscription_id: int) -> Optional[Dict]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select
            
                query = select(
                    UserSubscription,
                    Subscription.name.label('subscription_name'),
                    Subscription.description.label('subscription_description'),
                    Subscription.price.label('subscription_price'),
                    Subscription.duration_days.label('subscription_duration'),
                    Subscription.traffic_limit_gb.label('subscription_traffic_limit'),
                    Subscription.is_trial.label('is_trial'),
                    Subscription.is_imported.label('is_imported'),
                    User.username.label('user_username'),
                    User.first_name.label('user_first_name'),
                    User.telegram_id.label('user_telegram_id'),
                    User.balance.label('user_balance')
                ).select_from(
                    UserSubscription.__table__.join(
                        Subscription.__table__, UserSubscription.subscription_id == Subscription.id
                    ).join(
                        User.__table__, UserSubscription.user_id == User.telegram_id
                    )
                ).where(UserSubscription.id == subscription_id)
            
                result = await session.execute(query)
                row = result.fetchone()
            
                if not row:
                    return None
            
                user_sub = row[0]
                current_time = datetime.utcnow()
                
                if user_sub.expires_at <= current_time:
                    status = "expired"
                    status_emoji = "❌"
                elif not user_sub.is_active:
                    status = "inactive"
                    status_emoji = "⏸"
                else:
                    days_left = (user_sub.expires_at - current_time).days
                    if days_left <= 1:
                        status = "expiring_today"
                        status_emoji = "🔴"
                    elif days_left <= 3:
                        status = "expiring_soon"
                        status_emoji = "🟡"
                    elif days_left <= 7:
                        status = "expiring"
                        status_emoji = "🟠"
                    else:
                        status = "active"
                        status_emoji = "🟢"
            
                return {
                    'id': user_sub.id,
                    'user_id': row.user_telegram_id,
                    'user_username': row.user_username or 'N/A',
                    'user_first_name': row.user_first_name or 'N/A',
                    'user_balance': row.user_balance,
                    'subscription_name': row.subscription_name,
                    'subscription_description': row.subscription_description,
                    'subscription_price': row.subscription_price,
                    'subscription_duration': row.subscription_duration,
                    'subscription_traffic_limit': row.subscription_traffic_limit,
                    'short_uuid': user_sub.short_uuid,
                    'expires_at': user_sub.expires_at,
                    'is_active': user_sub.is_active,
                    'auto_pay_enabled': user_sub.auto_pay_enabled,
                    'auto_pay_days_before': user_sub.auto_pay_days_before,
                    'created_at': user_sub.created_at,
                    'updated_at': user_sub.updated_at,
                    'is_trial': row.is_trial,
                    'is_imported': row.is_imported,
                    'status': status,
                    'status_emoji': status_emoji,
                    'days_left': (user_sub.expires_at - current_time).days if user_sub.expires_at > current_time else 0
                }
            
            except Exception as e:
                logger.error(f"Error getting user subscription detail: {e}")
                return None

    async def get_user_subscriptions_stats_admin(self) -> Dict[str, Any]:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, func, and_, or_
                from datetime import datetime, timedelta
            
                current_time = datetime.utcnow()
            
                total_subs = await session.execute(
                    select(func.count(UserSubscription.id))
                )
                total_subscriptions = total_subs.scalar() or 0
            
                active_subs = await session.execute(
                    select(func.count(UserSubscription.id)).where(
                        and_(
                            UserSubscription.is_active == True,
                            UserSubscription.expires_at > current_time
                        )
                    )
                )
                active_subscriptions = active_subs.scalar() or 0
            
                expired_subs = await session.execute(
                    select(func.count(UserSubscription.id)).where(
                        or_(
                            UserSubscription.is_active == False,
                            UserSubscription.expires_at <= current_time
                        )
                    )
                )
                expired_subscriptions = expired_subs.scalar() or 0
            
                autopay_subs = await session.execute(
                    select(func.count(UserSubscription.id)).where(
                        UserSubscription.auto_pay_enabled == True
                    )
                )
                autopay_subscriptions = autopay_subs.scalar() or 0
            
                expiring_date = current_time + timedelta(days=7)
                expiring_subs = await session.execute(
                    select(func.count(UserSubscription.id)).where(
                        and_(
                            UserSubscription.is_active == True,
                            UserSubscription.expires_at > current_time,
                            UserSubscription.expires_at <= expiring_date
                        )
                    )
                )
                expiring_subscriptions = expiring_subs.scalar() or 0
            
                trial_subs = await session.execute(
                    select(func.count(UserSubscription.id))
                    .select_from(
                        UserSubscription.__table__.join(
                            Subscription.__table__, UserSubscription.subscription_id == Subscription.id
                        )
                    )
                    .where(Subscription.is_trial == True)
                )
                trial_subscriptions = trial_subs.scalar() or 0
                
                imported_subs = await session.execute(
                    select(func.count(UserSubscription.id))
                    .select_from(
                        UserSubscription.__table__.join(
                            Subscription.__table__, UserSubscription.subscription_id == Subscription.id
                        )
                    )
                    .where(Subscription.is_imported == True)
                )
                imported_subscriptions = imported_subs.scalar() or 0
            
                return {
                    'total_subscriptions': total_subscriptions,
                    'active_subscriptions': active_subscriptions,
                    'expired_subscriptions': expired_subscriptions,
                    'autopay_subscriptions': autopay_subscriptions,
                    'expiring_subscriptions': expiring_subscriptions,
                    'trial_subscriptions': trial_subscriptions,
                    'imported_subscriptions': imported_subscriptions
                }
            
            except Exception as e:
                logger.error(f"Error getting user subscriptions stats: {e}")
                return {
                    'total_subscriptions': 0,
                    'active_subscriptions': 0,
                    'expired_subscriptions': 0,
                    'autopay_subscriptions': 0,
                    'expiring_subscriptions': 0,
                    'trial_subscriptions': 0,
                    'imported_subscriptions': 0
                }

    async def get_lucky_game_admin_stats(self) -> dict:
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, func, case, and_
                from datetime import date
            
                total_games_result = await session.execute(
                    select(func.count(LuckyGame.id))
                )
                total_games = total_games_result.scalar() or 0
                
                total_wins_result = await session.execute(
                    select(func.count(LuckyGame.id)).where(LuckyGame.is_winner == True)
                )
                total_wins = total_wins_result.scalar() or 0
            
                unique_players_result = await session.execute(
                    select(func.count(func.distinct(LuckyGame.user_id)))
                )
                unique_players = unique_players_result.scalar() or 0
            
                total_rewards_result = await session.execute(
                    select(func.sum(LuckyGame.reward_amount))
                )
                total_rewards = total_rewards_result.scalar() or 0.0
            
                avg_reward_result = await session.execute(
                    select(func.avg(LuckyGame.reward_amount)).where(LuckyGame.is_winner == True)
                )
                avg_reward = avg_reward_result.scalar() or 0.0
            
                today = date.today()
            
                games_today_result = await session.execute(
                    select(func.count(LuckyGame.id)).where(
                        func.date(LuckyGame.played_at) == today
                    )
                )
                games_today = games_today_result.scalar() or 0
            
                wins_today_result = await session.execute(
                    select(func.count(LuckyGame.id)).where(
                        and_(
                            func.date(LuckyGame.played_at) == today,
                            LuckyGame.is_winner == True
                        )
                    )
                )
                wins_today = wins_today_result.scalar() or 0
            
                last_game_result = await session.execute(
                    select(func.max(LuckyGame.played_at))
                )
                last_game = last_game_result.scalar()
                
                first_game_result = await session.execute(
                    select(func.min(LuckyGame.played_at))
                )
                first_game = first_game_result.scalar()
            
                stats = {
                    'total_games': total_games,
                    'total_wins': total_wins,
                    'unique_players': unique_players,
                    'total_rewards': float(total_rewards),
                    'avg_reward': float(avg_reward),
                    'games_today': games_today,
                    'wins_today': wins_today,
                    'last_game': last_game.isoformat() if last_game else None,
                    'first_game': first_game.isoformat() if first_game else None
                }
            
                if stats['total_games'] > 0:
                    stats['win_rate'] = (stats['total_wins'] / stats['total_games']) * 100
                    stats['win_rate_today'] = (stats['wins_today'] / stats['games_today']) * 100 if stats['games_today'] > 0 else 0
                else:
                    stats['win_rate'] = 0
                    stats['win_rate_today'] = 0
            
                return stats
            
            except Exception as e:
                logger.error(f"Error getting lucky game admin stats: {e}")
                return {
                    'total_games': 0, 'total_wins': 0, 'unique_players': 0,
                    'total_rewards': 0.0, 'avg_reward': 0.0, 'games_today': 0,
                    'wins_today': 0, 'win_rate': 0, 'win_rate_today': 0,
                    'last_game': None, 'first_game': None
                }

    async def get_lucky_game_top_players(self, limit: int = 5) -> List[dict]:
        """Получает топ игроков по выигрышам"""
        async with self.session_factory() as session:
            try:
                from sqlalchemy import select, func, desc
            
                # Запрос с группировкой по пользователям
                query = select(
                    LuckyGame.user_id,
                    User.username,
                    User.first_name,
                    func.count(LuckyGame.id).label('games_played'),
                    func.count(case((LuckyGame.is_winner == True, 1))).label('wins'),
                    func.sum(LuckyGame.reward_amount).label('total_won'),
                    func.max(LuckyGame.played_at).label('last_game')
                ).select_from(
                    LuckyGame.__table__.join(User.__table__, LuckyGame.user_id == User.telegram_id, isouter=True)
                ).group_by(
                    LuckyGame.user_id, User.username, User.first_name
                ).order_by(
                    desc('total_won'), desc('wins')
                ).limit(limit)
            
                result = await session.execute(query)
                rows = result.all()
            
                return [
                    {
                        'user_id': row.user_id,
                        'username': row.username or 'N/A',
                        'first_name': row.first_name or 'Unknown',
                        'games_played': row.games_played,
                        'wins': row.wins,
                        'total_won': float(row.total_won or 0),
                        'last_game': row.last_game.isoformat() if row.last_game else None,
                        'win_rate': (row.wins / row.games_played) * 100 if row.games_played > 0 else 0
                    }
                    for row in rows
                ]
            
            except Exception as e:
                logger.error(f"Error getting lucky game top players: {e}")
                return []
