import logging
from typing import Dict, List, Optional

from sqlalchemy import func, select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Tariff, Subscription, PromoGroup, tariff_promo_groups


logger = logging.getLogger(__name__)


def _normalize_period_prices(period_prices: Optional[Dict[int, int]]) -> Dict[str, int]:
    """Нормализует цены периодов в формат {str: int}."""
    if not period_prices:
        return {}

    normalized: Dict[str, int] = {}

    for key, value in period_prices.items():
        try:
            period = int(key)
            price = int(value)
        except (TypeError, ValueError):
            continue

        if period > 0 and price >= 0:
            normalized[str(period)] = price

    return normalized


async def get_all_tariffs(
    db: AsyncSession,
    *,
    include_inactive: bool = False,
    offset: int = 0,
    limit: Optional[int] = None,
) -> List[Tariff]:
    """Получает все тарифы с опциональной фильтрацией по активности."""
    query = select(Tariff).options(selectinload(Tariff.allowed_promo_groups))

    if not include_inactive:
        query = query.where(Tariff.is_active.is_(True))

    query = query.order_by(Tariff.display_order, Tariff.id)

    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


async def get_tariff_by_id(
    db: AsyncSession,
    tariff_id: int,
    *,
    with_promo_groups: bool = True,
) -> Optional[Tariff]:
    """Получает тариф по ID."""
    query = select(Tariff).where(Tariff.id == tariff_id)

    if with_promo_groups:
        query = query.options(selectinload(Tariff.allowed_promo_groups))

    result = await db.execute(query)
    return result.scalars().first()


async def count_tariffs(db: AsyncSession, *, include_inactive: bool = False) -> int:
    """Подсчитывает количество тарифов."""
    query = select(func.count(Tariff.id))

    if not include_inactive:
        query = query.where(Tariff.is_active.is_(True))

    result = await db.execute(query)
    return int(result.scalar_one())


async def get_trial_tariff(db: AsyncSession) -> Optional[Tariff]:
    """Получает тариф, доступный для триала (is_trial_available=True)."""
    query = (
        select(Tariff)
        .where(Tariff.is_trial_available.is_(True))
        .where(Tariff.is_active.is_(True))
        .options(selectinload(Tariff.allowed_promo_groups))
        .limit(1)
    )
    result = await db.execute(query)
    return result.scalars().first()


async def set_trial_tariff(db: AsyncSession, tariff_id: int) -> Optional[Tariff]:
    """Устанавливает тариф как триальный (снимает флаг с других тарифов)."""
    # Снимаем флаг с всех тарифов
    await db.execute(
        Tariff.__table__.update().values(is_trial_available=False)
    )

    # Устанавливаем флаг на выбранный тариф
    tariff = await get_tariff_by_id(db, tariff_id)
    if tariff:
        tariff.is_trial_available = True
        await db.commit()
        await db.refresh(tariff)

    return tariff


async def clear_trial_tariff(db: AsyncSession) -> None:
    """Снимает флаг триала со всех тарифов."""
    await db.execute(
        Tariff.__table__.update().values(is_trial_available=False)
    )
    await db.commit()


async def get_tariffs_for_user(
    db: AsyncSession,
    promo_group_id: Optional[int] = None,
) -> List[Tariff]:
    """
    Получает тарифы, доступные для пользователя с учетом его промогруппы.
    Если у тарифа нет ограничений по промогруппам - он доступен всем.
    """
    query = (
        select(Tariff)
        .options(selectinload(Tariff.allowed_promo_groups))
        .where(Tariff.is_active.is_(True))
        .order_by(Tariff.display_order, Tariff.id)
    )

    result = await db.execute(query)
    tariffs = result.scalars().all()

    # Фильтруем по промогруппе
    available_tariffs = []
    for tariff in tariffs:
        if not tariff.allowed_promo_groups:
            # Нет ограничений - доступен всем
            available_tariffs.append(tariff)
        elif promo_group_id is not None:
            # Проверяем, есть ли промогруппа пользователя в списке разрешенных
            if any(pg.id == promo_group_id for pg in tariff.allowed_promo_groups):
                available_tariffs.append(tariff)
        # else: пользователь без промогруппы, а у тарифа есть ограничения - пропускаем

    return available_tariffs


async def create_tariff(
    db: AsyncSession,
    name: str,
    *,
    description: Optional[str] = None,
    display_order: int = 0,
    is_active: bool = True,
    traffic_limit_gb: int = 100,
    device_limit: int = 1,
    allowed_squads: Optional[List[str]] = None,
    period_prices: Optional[Dict[int, int]] = None,
    tier_level: int = 1,
    is_trial_available: bool = False,
    promo_group_ids: Optional[List[int]] = None,
) -> Tariff:
    """Создает новый тариф."""
    normalized_prices = _normalize_period_prices(period_prices)

    tariff = Tariff(
        name=name.strip(),
        description=description.strip() if description else None,
        display_order=max(0, display_order),
        is_active=is_active,
        traffic_limit_gb=max(0, traffic_limit_gb),
        device_limit=max(1, device_limit),
        allowed_squads=allowed_squads or [],
        period_prices=normalized_prices,
        tier_level=max(1, tier_level),
        is_trial_available=is_trial_available,
    )

    db.add(tariff)
    await db.flush()

    # Добавляем промогруппы если указаны
    if promo_group_ids:
        promo_groups_result = await db.execute(
            select(PromoGroup).where(PromoGroup.id.in_(promo_group_ids))
        )
        promo_groups = promo_groups_result.scalars().all()
        tariff.allowed_promo_groups = list(promo_groups)

    await db.commit()
    await db.refresh(tariff)

    logger.info(
        "Создан тариф '%s' (id=%s, tier=%s, traffic=%sGB, devices=%s, prices=%s)",
        tariff.name,
        tariff.id,
        tariff.tier_level,
        tariff.traffic_limit_gb,
        tariff.device_limit,
        normalized_prices,
    )

    return tariff


async def update_tariff(
    db: AsyncSession,
    tariff: Tariff,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    display_order: Optional[int] = None,
    is_active: Optional[bool] = None,
    traffic_limit_gb: Optional[int] = None,
    device_limit: Optional[int] = None,
    device_price_kopeks: Optional[int] = ...,  # ... = не передан, None = сбросить
    allowed_squads: Optional[List[str]] = None,
    period_prices: Optional[Dict[int, int]] = None,
    tier_level: Optional[int] = None,
    is_trial_available: Optional[bool] = None,
    promo_group_ids: Optional[List[int]] = None,
) -> Tariff:
    """Обновляет существующий тариф."""
    if name is not None:
        tariff.name = name.strip()
    if description is not None:
        tariff.description = description.strip() if description else None
    if display_order is not None:
        tariff.display_order = max(0, display_order)
    if is_active is not None:
        tariff.is_active = is_active
    if traffic_limit_gb is not None:
        tariff.traffic_limit_gb = max(0, traffic_limit_gb)
    if device_limit is not None:
        tariff.device_limit = max(1, device_limit)
    if device_price_kopeks is not ...:
        # Если передан device_price_kopeks (включая None) - обновляем
        tariff.device_price_kopeks = device_price_kopeks
    if allowed_squads is not None:
        tariff.allowed_squads = allowed_squads
    if period_prices is not None:
        tariff.period_prices = _normalize_period_prices(period_prices)
    if tier_level is not None:
        tariff.tier_level = max(1, tier_level)
    if is_trial_available is not None:
        tariff.is_trial_available = is_trial_available

    # Обновляем промогруппы если указаны
    if promo_group_ids is not None:
        if promo_group_ids:
            promo_groups_result = await db.execute(
                select(PromoGroup).where(PromoGroup.id.in_(promo_group_ids))
            )
            promo_groups = promo_groups_result.scalars().all()
            tariff.allowed_promo_groups = list(promo_groups)
        else:
            tariff.allowed_promo_groups = []

    await db.commit()
    await db.refresh(tariff)

    logger.info(
        "Обновлен тариф '%s' (id=%s)",
        tariff.name,
        tariff.id,
    )

    return tariff


async def delete_tariff(db: AsyncSession, tariff: Tariff) -> bool:
    """
    Удаляет тариф.
    Подписки с этим тарифом получат tariff_id = NULL.
    """
    tariff_id = tariff.id
    tariff_name = tariff.name

    # Подсчитываем подписки с этим тарифом
    subscriptions_count = await db.execute(
        select(func.count(Subscription.id)).where(Subscription.tariff_id == tariff_id)
    )
    affected_subscriptions = subscriptions_count.scalar_one()

    # Удаляем тариф (FK с ondelete=SET NULL автоматически обнулит tariff_id в подписках)
    await db.delete(tariff)
    await db.commit()

    logger.info(
        "Удален тариф '%s' (id=%s), затронуто подписок: %s",
        tariff_name,
        tariff_id,
        affected_subscriptions,
    )

    return True


async def get_tariff_subscriptions_count(db: AsyncSession, tariff_id: int) -> int:
    """Подсчитывает количество подписок на тарифе."""
    result = await db.execute(
        select(func.count(Subscription.id)).where(Subscription.tariff_id == tariff_id)
    )
    return int(result.scalar_one())


async def set_tariff_promo_groups(
    db: AsyncSession,
    tariff: Tariff,
    promo_group_ids: List[int],
) -> Tariff:
    """Устанавливает промогруппы для тарифа."""
    if promo_group_ids:
        promo_groups_result = await db.execute(
            select(PromoGroup).where(PromoGroup.id.in_(promo_group_ids))
        )
        promo_groups = promo_groups_result.scalars().all()
        tariff.allowed_promo_groups = list(promo_groups)
    else:
        tariff.allowed_promo_groups = []

    await db.commit()
    await db.refresh(tariff)

    return tariff


async def add_promo_group_to_tariff(
    db: AsyncSession,
    tariff: Tariff,
    promo_group_id: int,
) -> bool:
    """Добавляет промогруппу к тарифу."""
    promo_group = await db.get(PromoGroup, promo_group_id)
    if not promo_group:
        return False

    if promo_group not in tariff.allowed_promo_groups:
        tariff.allowed_promo_groups.append(promo_group)
        await db.commit()

    return True


async def remove_promo_group_from_tariff(
    db: AsyncSession,
    tariff: Tariff,
    promo_group_id: int,
) -> bool:
    """Удаляет промогруппу из тарифа."""
    for pg in tariff.allowed_promo_groups:
        if pg.id == promo_group_id:
            tariff.allowed_promo_groups.remove(pg)
            await db.commit()
            return True
    return False


async def get_tariffs_with_subscriptions_count(
    db: AsyncSession,
    *,
    include_inactive: bool = False,
) -> List[tuple]:
    """Получает тарифы с количеством подписок."""
    query = (
        select(Tariff, func.count(Subscription.id))
        .outerjoin(Subscription, Subscription.tariff_id == Tariff.id)
        .group_by(Tariff.id)
        .order_by(Tariff.display_order, Tariff.id)
    )

    if not include_inactive:
        query = query.where(Tariff.is_active.is_(True))

    result = await db.execute(query)
    return result.all()


async def reorder_tariffs(
    db: AsyncSession,
    tariff_order: List[int],
) -> None:
    """Изменяет порядок отображения тарифов."""
    for order, tariff_id in enumerate(tariff_order):
        await db.execute(
            update(Tariff)
            .where(Tariff.id == tariff_id)
            .values(display_order=order)
        )

    await db.commit()

    logger.info("Изменен порядок тарифов: %s", tariff_order)
