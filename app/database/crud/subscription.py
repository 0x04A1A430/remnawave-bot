import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    Subscription, SubscriptionStatus, User, 
    SubscriptionServer
)
from app.config import settings

logger = logging.getLogger(__name__)


async def get_subscription_by_user_id(db: AsyncSession, user_id: int) -> Optional[Subscription]:
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .where(Subscription.user_id == user_id)
    )
    subscription = result.scalar_one_or_none()
    
    if subscription:
        subscription = await check_and_update_subscription_status(db, subscription)
    
    return subscription


async def create_trial_subscription(
    db: AsyncSession,
    user_id: int,
    duration_days: int = None,
    traffic_limit_gb: int = None,
    device_limit: int = None,
    squad_uuid: str = None
) -> Subscription:
    
    duration_days = duration_days or settings.TRIAL_DURATION_DAYS
    traffic_limit_gb = traffic_limit_gb or settings.TRIAL_TRAFFIC_LIMIT_GB
    device_limit = device_limit or settings.TRIAL_DEVICE_LIMIT
    squad_uuid = squad_uuid or settings.TRIAL_SQUAD_UUID
    
    end_date = datetime.utcnow() + timedelta(days=duration_days)
    
    subscription = Subscription(
        user_id=user_id,
        status=SubscriptionStatus.ACTIVE.value,
        is_trial=True,
        start_date=datetime.utcnow(),
        end_date=end_date,
        traffic_limit_gb=traffic_limit_gb,
        device_limit=device_limit,
        connected_squads=[squad_uuid] if squad_uuid else []
    )
    
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"🎁 Создана триальная подписка для пользователя {user_id}")
    return subscription


async def create_paid_subscription(
    db: AsyncSession,
    user_id: int,
    duration_days: int,
    traffic_limit_gb: int = 0, 
    device_limit: int = 1,
    connected_squads: List[str] = None
) -> Subscription:
    
    end_date = datetime.utcnow() + timedelta(days=duration_days)
    
    subscription = Subscription(
        user_id=user_id,
        status=SubscriptionStatus.ACTIVE.value,
        is_trial=False,
        start_date=datetime.utcnow(),
        end_date=end_date,
        traffic_limit_gb=traffic_limit_gb,
        device_limit=device_limit,
        connected_squads=connected_squads or []
    )
    
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"💎 Создана платная подписка для пользователя {user_id}")
    return subscription


async def extend_subscription(
    db: AsyncSession,
    subscription: Subscription,
    days: int
) -> Subscription:
    
    subscription.extend_subscription(days)
    
    if subscription.status == SubscriptionStatus.EXPIRED.value:
        subscription.status = SubscriptionStatus.ACTIVE.value
    
    subscription.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"⏰ Подписка пользователя {subscription.user_id} продлена на {days} дней")
    return subscription


async def add_subscription_traffic(
    db: AsyncSession,
    subscription: Subscription,
    gb: int
) -> Subscription:
    
    subscription.add_traffic(gb)
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"📈 К подписке пользователя {subscription.user_id} добавлено {gb} ГБ трафика")
    return subscription


async def add_subscription_devices(
    db: AsyncSession,
    subscription: Subscription,
    devices: int
) -> Subscription:
    
    subscription.device_limit += devices
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"📱 К подписке пользователя {subscription.user_id} добавлено {devices} устройств")
    return subscription


async def add_subscription_squad(
    db: AsyncSession,
    subscription: Subscription,
    squad_uuid: str
) -> Subscription:
    
    if squad_uuid not in subscription.connected_squads:
        subscription.connected_squads = subscription.connected_squads + [squad_uuid]
        subscription.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(subscription)
        
        logger.info(f"🌍 К подписке пользователя {subscription.user_id} добавлен сквад {squad_uuid}")
    
    return subscription


async def remove_subscription_squad(
    db: AsyncSession,
    subscription: Subscription,
    squad_uuid: str
) -> Subscription:
    
    if squad_uuid in subscription.connected_squads:
        squads = subscription.connected_squads.copy()
        squads.remove(squad_uuid)
        subscription.connected_squads = squads
        subscription.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(subscription)
        
        logger.info(f"🚫 Из подписки пользователя {subscription.user_id} удален сквад {squad_uuid}")
    
    return subscription


async def update_subscription_autopay(
    db: AsyncSession,
    subscription: Subscription,
    enabled: bool,
    days_before: int = 3
) -> Subscription:
    
    subscription.autopay_enabled = enabled
    subscription.autopay_days_before = days_before
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    status = "включен" if enabled else "выключен"
    logger.info(f"💳 Автоплатеж для подписки пользователя {subscription.user_id} {status}")
    return subscription


async def deactivate_subscription(
    db: AsyncSession,
    subscription: Subscription
) -> Subscription:
    
    subscription.status = SubscriptionStatus.DISABLED.value
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"❌ Подписка пользователя {subscription.user_id} деактивирована")
    return subscription


async def get_expiring_subscriptions(
    db: AsyncSession,
    days_before: int = 3
) -> List[Subscription]:
    
    threshold_date = datetime.utcnow() + timedelta(days=days_before)
    
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .where(
            and_(
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.end_date <= threshold_date,
                Subscription.end_date > datetime.utcnow()
            )
        )
    )
    return result.scalars().all()


async def get_expired_subscriptions(db: AsyncSession) -> List[Subscription]:
    
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .where(
            and_(
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.end_date <= datetime.utcnow()
            )
        )
    )
    return result.scalars().all()


async def get_subscriptions_for_autopay(db: AsyncSession) -> List[Subscription]:
    current_time = datetime.utcnow()
    
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .where(
            and_(
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.autopay_enabled == True,
                Subscription.is_trial == False 
            )
        )
    )
    all_autopay_subscriptions = result.scalars().all()
    
    ready_for_autopay = []
    for subscription in all_autopay_subscriptions:
        days_until_expiry = (subscription.end_date - current_time).days
        
        if days_until_expiry <= subscription.autopay_days_before and subscription.end_date > current_time:
            ready_for_autopay.append(subscription)
    
    return ready_for_autopay


async def get_subscriptions_statistics(db: AsyncSession) -> dict:
    
    total_result = await db.execute(select(func.count(Subscription.id)))
    total_subscriptions = total_result.scalar()
    
    active_result = await db.execute(
        select(func.count(Subscription.id))
        .where(Subscription.status == SubscriptionStatus.ACTIVE.value)
    )
    active_subscriptions = active_result.scalar()
    
    trial_result = await db.execute(
        select(func.count(Subscription.id))
        .where(
            and_(
                Subscription.is_trial == True,
                Subscription.status == SubscriptionStatus.ACTIVE.value
            )
        )
    )
    trial_subscriptions = trial_result.scalar()
    
    paid_subscriptions = active_subscriptions - trial_subscriptions
    
    today = datetime.utcnow().date()
    today_result = await db.execute(
        select(func.count(Subscription.id))
        .where(
            and_(
                Subscription.created_at >= today,
                Subscription.is_trial == False
            )
        )
    )
    purchased_today = today_result.scalar()
    
    week_ago = datetime.utcnow() - timedelta(days=7)
    week_result = await db.execute(
        select(func.count(Subscription.id))
        .where(
            and_(
                Subscription.created_at >= week_ago,
                Subscription.is_trial == False
            )
        )
    )
    purchased_week = week_result.scalar()
    
    month_ago = datetime.utcnow() - timedelta(days=30)
    month_result = await db.execute(
        select(func.count(Subscription.id))
        .where(
            and_(
                Subscription.created_at >= month_ago,
                Subscription.is_trial == False
            )
        )
    )
    purchased_month = month_result.scalar()
    
    return {
        "total_subscriptions": total_subscriptions,
        "active_subscriptions": active_subscriptions,
        "trial_subscriptions": trial_subscriptions,
        "paid_subscriptions": paid_subscriptions,
        "purchased_today": purchased_today,
        "purchased_week": purchased_week,
        "purchased_month": purchased_month
    }


async def update_subscription_usage(
    db: AsyncSession,
    subscription: Subscription,
    used_gb: float
) -> Subscription:
    subscription.traffic_used_gb = used_gb
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    return subscription

async def get_all_subscriptions(
    db: AsyncSession, 
    page: int = 1, 
    limit: int = 10
) -> Tuple[List[Subscription], int]:
    count_result = await db.execute(
        select(func.count(Subscription.id))
    )
    total_count = count_result.scalar()
    
    offset = (page - 1) * limit
    
    result = await db.execute(
        select(Subscription)
        .options(selectinload(Subscription.user))
        .order_by(Subscription.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    
    subscriptions = result.scalars().all()
    
    return subscriptions, total_count

async def add_subscription_servers(
    db: AsyncSession,
    subscription: Subscription,
    server_squad_ids: List[int],
    paid_prices: List[int] = None
) -> Subscription:
    
    if paid_prices is None:
        paid_prices = [0] * len(server_squad_ids)
    
    for i, server_id in enumerate(server_squad_ids):
        subscription_server = SubscriptionServer(
            subscription_id=subscription.id,
            server_squad_id=server_id,
            paid_price_kopeks=paid_prices[i] if i < len(paid_prices) else 0
        )
        db.add(subscription_server)
    
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"🌍 К подписке {subscription.id} добавлено {len(server_squad_ids)} серверов")
    return subscription

async def get_subscription_server_ids(
    db: AsyncSession,
    subscription_id: int
) -> List[int]:
    
    result = await db.execute(
        select(SubscriptionServer.server_squad_id)
        .where(SubscriptionServer.subscription_id == subscription_id)
    )
    return [row[0] for row in result.fetchall()]


async def get_subscription_servers(
    db: AsyncSession,
    subscription_id: int
) -> List[dict]:
    
    from app.database.models import ServerSquad
    
    result = await db.execute(
        select(SubscriptionServer, ServerSquad)
        .join(ServerSquad, SubscriptionServer.server_squad_id == ServerSquad.id)
        .where(SubscriptionServer.subscription_id == subscription_id)
    )
    
    servers_info = []
    for sub_server, server_squad in result.fetchall():
        servers_info.append({
            'server_id': server_squad.id,
            'squad_uuid': server_squad.squad_uuid,
            'display_name': server_squad.display_name,
            'country_code': server_squad.country_code,
            'paid_price_kopeks': sub_server.paid_price_kopeks,
            'connected_at': sub_server.connected_at,
            'is_available': server_squad.is_available
        })
    
    return servers_info

async def remove_subscription_servers(
    db: AsyncSession,
    subscription_id: int,
    server_squad_ids: List[int]
) -> bool:
    try:
        from app.database.models import SubscriptionServer
        from sqlalchemy import delete
        
        await db.execute(
            delete(SubscriptionServer)
            .where(
                SubscriptionServer.subscription_id == subscription_id,
                SubscriptionServer.server_squad_id.in_(server_squad_ids)
            )
        )
        
        await db.commit()
        logger.info(f"🗑️ Удалены серверы {server_squad_ids} из подписки {subscription_id}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка удаления серверов из подписки: {e}")
        await db.rollback()
        return False


async def get_subscription_renewal_cost(
    db: AsyncSession,
    subscription_id: int,
    period_days: int
) -> int:
    try:
        from app.config import PERIOD_PRICES, TRAFFIC_PRICES, settings
        
        base_price = PERIOD_PRICES.get(period_days, 0)
        
        servers_info = await get_subscription_servers(db, subscription_id)
        servers_cost = sum(server_info['paid_price_kopeks'] for server_info in servers_info)
        
        subscription = await db.get(Subscription, subscription_id)
        if not subscription:
            return base_price
        
        traffic_cost = 0
        if subscription.traffic_limit_gb > 0:
            traffic_cost = TRAFFIC_PRICES.get(subscription.traffic_limit_gb, 0)
        
        devices_cost = max(0, subscription.device_limit - 1) * settings.PRICE_PER_DEVICE
        
        total_cost = base_price + servers_cost + traffic_cost + devices_cost
        
        logger.info(f"💰 Расчет продления подписки {subscription_id} на {period_days} дней:")
        logger.info(f"   📅 Период: {base_price/100}₽")
        logger.info(f"   🌍 Серверы: {servers_cost/100}₽")
        logger.info(f"   📊 Трафик: {traffic_cost/100}₽")
        logger.info(f"   📱 Устройства: {devices_cost/100}₽")
        logger.info(f"   💎 ИТОГО: {total_cost/100}₽")
        
        return total_cost
        
    except Exception as e:
        logger.error(f"Ошибка расчета стоимости продления: {e}")
        from app.config import PERIOD_PRICES
        return PERIOD_PRICES.get(period_days, 0)

async def expire_subscription(
    db: AsyncSession,
    subscription: Subscription
) -> Subscription:
    
    subscription.status = SubscriptionStatus.EXPIRED.value
    subscription.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"⏰ Подписка пользователя {subscription.user_id} помечена как истёкшая")
    return subscription


async def check_and_update_subscription_status(
    db: AsyncSession,
    subscription: Subscription
) -> Subscription:
    
    current_time = datetime.utcnow()
    
    if (subscription.status == SubscriptionStatus.ACTIVE.value and 
        subscription.end_date <= current_time):
        
        subscription.status = SubscriptionStatus.EXPIRED.value
        subscription.updated_at = current_time
        
        await db.commit()
        await db.refresh(subscription)
        
        logger.info(f"⏰ Статус подписки пользователя {subscription.user_id} изменен на 'expired'")
    
    return subscription

async def create_subscription(
    db: AsyncSession,
    user_id: int,
    status: str = "trial",
    is_trial: bool = True,
    end_date: datetime = None,
    traffic_limit_gb: int = 10,
    traffic_used_gb: float = 0.0,
    device_limit: int = 1,
    connected_squads: list = None,
    remnawave_short_uuid: str = None,
    subscription_url: str = ""
) -> Subscription:
    
    if end_date is None:
        end_date = datetime.utcnow() + timedelta(days=3)
    
    if connected_squads is None:
        connected_squads = []
    
    subscription = Subscription(
        user_id=user_id,
        status=status,
        is_trial=is_trial,
        end_date=end_date,
        traffic_limit_gb=traffic_limit_gb,
        traffic_used_gb=traffic_used_gb,
        device_limit=device_limit,
        connected_squads=connected_squads,
        remnawave_short_uuid=remnawave_short_uuid,
        subscription_url=subscription_url
    )
    
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    
    logger.info(f"✅ Создана подписка для пользователя {user_id}")
    return subscription
