"""
CRUD операции для колеса удачи (Fortune Wheel).
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy import select, and_, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    WheelConfig,
    WheelPrize,
    WheelSpin,
    WheelPrizeType,
    WheelSpinPaymentType,
    User,
)

logger = logging.getLogger(__name__)


# ==================== WHEEL CONFIG ====================


async def get_wheel_config(db: AsyncSession) -> Optional[WheelConfig]:
    """Получить текущую конфигурацию колеса (всегда id=1)."""
    result = await db.execute(
        select(WheelConfig)
        .options(selectinload(WheelConfig.prizes))
        .where(WheelConfig.id == 1)
    )
    return result.scalar_one_or_none()


async def get_or_create_wheel_config(db: AsyncSession) -> WheelConfig:
    """Получить или создать конфигурацию колеса."""
    config = await get_wheel_config(db)
    if config:
        return config

    # Создаем дефолтную конфигурацию
    config = WheelConfig(
        id=1,
        is_enabled=False,
        name="Колесо удачи",
        spin_cost_stars=10,
        spin_cost_days=1,
        spin_cost_stars_enabled=True,
        spin_cost_days_enabled=True,
        rtp_percent=80,
        daily_spin_limit=5,
        min_subscription_days_for_day_payment=3,
        promo_prefix="WHEEL",
        promo_validity_days=7,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    logger.info("🎡 Создана дефолтная конфигурация колеса удачи")
    return config


async def update_wheel_config(
    db: AsyncSession,
    **kwargs
) -> WheelConfig:
    """Обновить конфигурацию колеса."""
    config = await get_or_create_wheel_config(db)

    for key, value in kwargs.items():
        if hasattr(config, key) and value is not None:
            setattr(config, key, value)

    config.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(config)
    logger.info(f"🎡 Обновлена конфигурация колеса: {kwargs}")
    return config


# ==================== WHEEL PRIZES ====================


async def get_wheel_prizes(
    db: AsyncSession,
    config_id: int = 1,
    active_only: bool = True
) -> List[WheelPrize]:
    """Получить список призов колеса."""
    query = select(WheelPrize).where(WheelPrize.config_id == config_id)

    if active_only:
        query = query.where(WheelPrize.is_active == True)

    query = query.order_by(WheelPrize.sort_order)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_wheel_prize_by_id(db: AsyncSession, prize_id: int) -> Optional[WheelPrize]:
    """Получить приз по ID."""
    result = await db.execute(
        select(WheelPrize).where(WheelPrize.id == prize_id)
    )
    return result.scalar_one_or_none()


async def create_wheel_prize(
    db: AsyncSession,
    config_id: int,
    prize_type: str,
    prize_value: int,
    display_name: str,
    prize_value_kopeks: int,
    emoji: str = "🎁",
    color: str = "#3B82F6",
    sort_order: int = 0,
    manual_probability: Optional[float] = None,
    is_active: bool = True,
    promo_balance_bonus_kopeks: int = 0,
    promo_subscription_days: int = 0,
    promo_traffic_gb: int = 0,
) -> WheelPrize:
    """Создать новый приз на колесе."""
    prize = WheelPrize(
        config_id=config_id,
        prize_type=prize_type,
        prize_value=prize_value,
        display_name=display_name,
        prize_value_kopeks=prize_value_kopeks,
        emoji=emoji,
        color=color,
        sort_order=sort_order,
        manual_probability=manual_probability,
        is_active=is_active,
        promo_balance_bonus_kopeks=promo_balance_bonus_kopeks,
        promo_subscription_days=promo_subscription_days,
        promo_traffic_gb=promo_traffic_gb,
    )
    db.add(prize)
    await db.commit()
    await db.refresh(prize)
    logger.info(f"🎁 Создан приз колеса: {display_name} ({prize_type})")
    return prize


async def update_wheel_prize(
    db: AsyncSession,
    prize_id: int,
    **kwargs
) -> Optional[WheelPrize]:
    """Обновить приз колеса."""
    prize = await get_wheel_prize_by_id(db, prize_id)
    if not prize:
        return None

    for key, value in kwargs.items():
        if hasattr(prize, key) and value is not None:
            setattr(prize, key, value)

    prize.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(prize)
    logger.info(f"🎁 Обновлен приз колеса ID={prize_id}: {kwargs}")
    return prize


async def delete_wheel_prize(db: AsyncSession, prize_id: int) -> bool:
    """Удалить приз колеса."""
    prize = await get_wheel_prize_by_id(db, prize_id)
    if not prize:
        return False

    await db.delete(prize)
    await db.commit()
    logger.info(f"🗑️ Удален приз колеса ID={prize_id}")
    return True


async def reorder_wheel_prizes(
    db: AsyncSession,
    prize_ids: List[int]
) -> bool:
    """Переупорядочить призы колеса."""
    for index, prize_id in enumerate(prize_ids):
        prize = await get_wheel_prize_by_id(db, prize_id)
        if prize:
            prize.sort_order = index

    await db.commit()
    logger.info(f"🔄 Переупорядочены призы колеса: {prize_ids}")
    return True


# ==================== WHEEL SPINS ====================


async def create_wheel_spin(
    db: AsyncSession,
    user_id: int,
    prize_id: int,
    payment_type: str,
    payment_amount: int,
    payment_value_kopeks: int,
    prize_type: str,
    prize_value: int,
    prize_display_name: str,
    prize_value_kopeks: int,
    generated_promocode_id: Optional[int] = None,
    is_applied: bool = False,
) -> WheelSpin:
    """Создать запись о спине колеса."""
    spin = WheelSpin(
        user_id=user_id,
        prize_id=prize_id,
        payment_type=payment_type,
        payment_amount=payment_amount,
        payment_value_kopeks=payment_value_kopeks,
        prize_type=prize_type,
        prize_value=prize_value,
        prize_display_name=prize_display_name,
        prize_value_kopeks=prize_value_kopeks,
        generated_promocode_id=generated_promocode_id,
        is_applied=is_applied,
        applied_at=datetime.utcnow() if is_applied else None,
    )
    db.add(spin)
    await db.commit()
    await db.refresh(spin)
    logger.info(f"🎰 Создан спин колеса: user_id={user_id}, prize='{prize_display_name}'")
    return spin


async def mark_spin_applied(db: AsyncSession, spin_id: int) -> Optional[WheelSpin]:
    """Отметить спин как примененный."""
    result = await db.execute(
        select(WheelSpin).where(WheelSpin.id == spin_id)
    )
    spin = result.scalar_one_or_none()
    if spin:
        spin.is_applied = True
        spin.applied_at = datetime.utcnow()
        await db.commit()
        await db.refresh(spin)
    return spin


async def get_user_spins_today(db: AsyncSession, user_id: int) -> int:
    """Получить количество спинов пользователя за сегодня."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    result = await db.execute(
        select(func.count(WheelSpin.id))
        .where(
            and_(
                WheelSpin.user_id == user_id,
                WheelSpin.created_at >= today_start,
            )
        )
    )
    return result.scalar() or 0


async def get_user_spin_history(
    db: AsyncSession,
    user_id: int,
    limit: int = 20,
    offset: int = 0
) -> tuple[List[WheelSpin], int]:
    """Получить историю спинов пользователя."""
    # Общее количество
    count_result = await db.execute(
        select(func.count(WheelSpin.id))
        .where(WheelSpin.user_id == user_id)
    )
    total = count_result.scalar() or 0

    # Спины с пагинацией (eager load prize relationship)
    result = await db.execute(
        select(WheelSpin)
        .options(selectinload(WheelSpin.prize))
        .where(WheelSpin.user_id == user_id)
        .order_by(desc(WheelSpin.created_at))
        .limit(limit)
        .offset(offset)
    )
    spins = list(result.scalars().all())

    return spins, total


async def get_all_spins(
    db: AsyncSession,
    user_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0
) -> tuple[List[WheelSpin], int]:
    """Получить все спины с фильтрами (для админки)."""
    conditions = []

    if user_id:
        conditions.append(WheelSpin.user_id == user_id)
    if date_from:
        conditions.append(WheelSpin.created_at >= date_from)
    if date_to:
        conditions.append(WheelSpin.created_at <= date_to)

    # Общее количество
    count_query = select(func.count(WheelSpin.id))
    if conditions:
        count_query = count_query.where(and_(*conditions))
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Спины с пагинацией
    query = select(WheelSpin).options(selectinload(WheelSpin.user))
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(desc(WheelSpin.created_at)).limit(limit).offset(offset)

    result = await db.execute(query)
    spins = list(result.scalars().all())

    return spins, total


# ==================== STATISTICS ====================


async def get_wheel_statistics(
    db: AsyncSession,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> Dict[str, Any]:
    """Получить статистику колеса удачи."""
    conditions = []
    if date_from:
        conditions.append(WheelSpin.created_at >= date_from)
    if date_to:
        conditions.append(WheelSpin.created_at <= date_to)

    base_query = select(WheelSpin)
    if conditions:
        base_query = base_query.where(and_(*conditions))

    # Общие метрики
    result = await db.execute(
        select(
            func.count(WheelSpin.id).label("total_spins"),
            func.coalesce(func.sum(WheelSpin.payment_value_kopeks), 0).label("total_revenue"),
            func.coalesce(func.sum(WheelSpin.prize_value_kopeks), 0).label("total_payout"),
        ).where(and_(*conditions) if conditions else True)
    )
    row = result.one()
    total_spins = row.total_spins or 0
    total_revenue = row.total_revenue or 0
    total_payout = row.total_payout or 0

    # Фактический RTP
    actual_rtp = (total_payout / total_revenue * 100) if total_revenue > 0 else 0

    # Распределение по типу оплаты
    payment_dist = await db.execute(
        select(
            WheelSpin.payment_type,
            func.count(WheelSpin.id).label("count"),
            func.sum(WheelSpin.payment_value_kopeks).label("total"),
        )
        .where(and_(*conditions) if conditions else True)
        .group_by(WheelSpin.payment_type)
    )
    spins_by_payment_type = {
        row.payment_type: {"count": row.count, "total_kopeks": row.total or 0}
        for row in payment_dist
    }

    # Распределение призов
    prizes_dist = await db.execute(
        select(
            WheelSpin.prize_type,
            WheelSpin.prize_display_name,
            func.count(WheelSpin.id).label("count"),
            func.sum(WheelSpin.prize_value_kopeks).label("total"),
        )
        .where(and_(*conditions) if conditions else True)
        .group_by(WheelSpin.prize_type, WheelSpin.prize_display_name)
    )
    prizes_distribution = [
        {
            "prize_type": row.prize_type,
            "display_name": row.prize_display_name,
            "count": row.count,
            "total_kopeks": row.total or 0,
        }
        for row in prizes_dist
    ]

    # Топ выигрышей
    top_wins_result = await db.execute(
        select(WheelSpin)
        .options(selectinload(WheelSpin.user))
        .where(and_(*conditions) if conditions else True)
        .where(WheelSpin.prize_value_kopeks > 0)
        .order_by(desc(WheelSpin.prize_value_kopeks))
        .limit(10)
    )
    top_wins = [
        {
            "user_id": spin.user_id,
            "username": spin.user.username if spin.user else None,
            "prize_display_name": spin.prize_display_name,
            "prize_value_kopeks": spin.prize_value_kopeks,
            "created_at": spin.created_at.isoformat() if spin.created_at else None,
        }
        for spin in top_wins_result.scalars().all()
    ]

    # Конфигурация для сравнения
    config = await get_wheel_config(db)
    configured_rtp = config.rtp_percent if config else 80

    return {
        "total_spins": total_spins,
        "total_revenue_kopeks": total_revenue,
        "total_payout_kopeks": total_payout,
        "actual_rtp_percent": round(actual_rtp, 2),
        "configured_rtp_percent": configured_rtp,
        "spins_by_payment_type": spins_by_payment_type,
        "prizes_distribution": prizes_distribution,
        "top_wins": top_wins,
        "period_from": date_from.isoformat() if date_from else None,
        "period_to": date_to.isoformat() if date_to else None,
    }
