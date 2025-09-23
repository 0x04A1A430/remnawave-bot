import logging
from typing import List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import PromoGroup, User

logger = logging.getLogger(__name__)


async def get_promo_groups_with_counts(
    db: AsyncSession,
) -> List[Tuple[PromoGroup, int]]:
    result = await db.execute(
        select(PromoGroup, func.count(User.id))
        .outerjoin(User, User.promo_group_id == PromoGroup.id)
        .group_by(PromoGroup.id)
        .order_by(PromoGroup.is_default.desc(), PromoGroup.name)
    )
    return result.all()


async def get_promo_group_by_id(db: AsyncSession, group_id: int) -> Optional[PromoGroup]:
    return await db.get(PromoGroup, group_id)


async def get_default_promo_group(db: AsyncSession) -> Optional[PromoGroup]:
    result = await db.execute(
        select(PromoGroup).where(PromoGroup.is_default.is_(True))
    )
    return result.scalars().first()


async def create_promo_group(
    db: AsyncSession,
    name: str,
    *,
    server_discount_percent: int,
    traffic_discount_percent: int,
    device_discount_percent: int,
    auto_assign_enabled: bool = False,
    spent_threshold_kopeks: int = 0,
) -> PromoGroup:
    promo_group = PromoGroup(
        name=name.strip(),
        server_discount_percent=max(0, min(100, server_discount_percent)),
        traffic_discount_percent=max(0, min(100, traffic_discount_percent)),
        device_discount_percent=max(0, min(100, device_discount_percent)),
        auto_assign_enabled=auto_assign_enabled,
        spent_threshold_kopeks=max(0, spent_threshold_kopeks),
        is_default=False,
    )

    db.add(promo_group)
    await db.commit()
    await db.refresh(promo_group)

    logger.info(
        "Создана промогруппа '%s' с скидками (servers=%s%%, traffic=%s%%, devices=%s%%), авто=%s, порог=%s",
        promo_group.name,
        promo_group.server_discount_percent,
        promo_group.traffic_discount_percent,
        promo_group.device_discount_percent,
        promo_group.auto_assign_enabled,
        promo_group.spent_threshold_kopeks,
    )

    return promo_group


async def update_promo_group(
    db: AsyncSession,
    group: PromoGroup,
    *,
    name: Optional[str] = None,
    server_discount_percent: Optional[int] = None,
    traffic_discount_percent: Optional[int] = None,
    device_discount_percent: Optional[int] = None,
    auto_assign_enabled: Optional[bool] = None,
    spent_threshold_kopeks: Optional[int] = None,
) -> PromoGroup:
    if name is not None:
        group.name = name.strip()
    if server_discount_percent is not None:
        group.server_discount_percent = max(0, min(100, server_discount_percent))
    if traffic_discount_percent is not None:
        group.traffic_discount_percent = max(0, min(100, traffic_discount_percent))
    if device_discount_percent is not None:
        group.device_discount_percent = max(0, min(100, device_discount_percent))
    if auto_assign_enabled is not None:
        group.auto_assign_enabled = bool(auto_assign_enabled)
    if spent_threshold_kopeks is not None:
        group.spent_threshold_kopeks = max(0, spent_threshold_kopeks)

    await db.commit()
    await db.refresh(group)

    logger.info(
        "Обновлена промогруппа '%s' (id=%s)",
        group.name,
        group.id,
    )
    return group


async def get_auto_assign_promo_groups(db: AsyncSession) -> List[PromoGroup]:
    result = await db.execute(
        select(PromoGroup)
        .where(PromoGroup.auto_assign_enabled.is_(True))
        .order_by(PromoGroup.spent_threshold_kopeks.desc(), PromoGroup.id)
    )
    return result.scalars().all()


async def delete_promo_group(db: AsyncSession, group: PromoGroup) -> bool:
    if group.is_default:
        logger.warning("Попытка удалить базовую промогруппу запрещена")
        return False

    default_group = await get_default_promo_group(db)
    if not default_group:
        logger.error("Не найдена базовая промогруппа для reassignment")
        return False

    await db.execute(
        update(User)
        .where(User.promo_group_id == group.id)
        .values(promo_group_id=default_group.id)
    )
    await db.delete(group)
    await db.commit()

    logger.info(
        "Промогруппа '%s' (id=%s) удалена, пользователи переведены в '%s'",
        group.name,
        group.id,
        default_group.name,
    )
    return True


async def get_promo_group_members(
    db: AsyncSession,
    group_id: int,
    *,
    offset: int = 0,
    limit: int = 20,
) -> List[User]:
    result = await db.execute(
        select(User)
        .options(selectinload(User.subscription))
        .where(User.promo_group_id == group_id)
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


async def count_promo_group_members(db: AsyncSession, group_id: int) -> int:
    result = await db.execute(
        select(func.count(User.id)).where(User.promo_group_id == group_id)
    )
    return result.scalar_one()
