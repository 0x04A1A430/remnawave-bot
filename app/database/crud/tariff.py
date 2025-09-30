import logging
from typing import Iterable, List, Optional, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    PromoGroup,
    ServerSquad,
    Subscription,
    SubscriptionTariff,
    SubscriptionTariffPrice,
)

logger = logging.getLogger(__name__)


async def list_tariffs(
    db: AsyncSession,
    *,
    include_inactive: bool = False,
) -> List[SubscriptionTariff]:
    query = (
        select(SubscriptionTariff)
        .options(
            selectinload(SubscriptionTariff.promo_groups),
            selectinload(SubscriptionTariff.server_squads),
            selectinload(SubscriptionTariff.prices),
        )
        .order_by(SubscriptionTariff.sort_order, SubscriptionTariff.name)
    )

    if not include_inactive:
        query = query.where(SubscriptionTariff.is_active.is_(True))

    result = await db.execute(query)
    return result.scalars().unique().all()


async def get_tariff_by_id(
    db: AsyncSession,
    tariff_id: int,
    *,
    include_inactive: bool = False,
) -> Optional[SubscriptionTariff]:
    query = (
        select(SubscriptionTariff)
        .options(
            selectinload(SubscriptionTariff.promo_groups),
            selectinload(SubscriptionTariff.server_squads),
            selectinload(SubscriptionTariff.prices),
        )
        .where(SubscriptionTariff.id == tariff_id)
    )

    if not include_inactive:
        query = query.where(SubscriptionTariff.is_active.is_(True))

    result = await db.execute(query)
    return result.scalars().unique().one_or_none()


async def _resolve_servers(
    db: AsyncSession,
    server_uuids: Sequence[str],
) -> List[ServerSquad]:
    if not server_uuids:
        return []

    seen = set()
    normalized: List[str] = []
    for raw_uuid in server_uuids:
        if not raw_uuid:
            continue
        cleaned = raw_uuid.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    if not normalized:
        return []

    result = await db.execute(
        select(ServerSquad)
        .options(selectinload(ServerSquad.allowed_promo_groups))
        .where(ServerSquad.squad_uuid.in_(normalized))
    )
    servers = result.scalars().unique().all()

    missing = set(normalized) - {server.squad_uuid for server in servers}
    if missing:
        logger.warning("Не найдены серверы для тарифов: %s", ", ".join(sorted(missing)))

    ordered_servers = sorted(
        servers,
        key=lambda server: normalized.index(server.squad_uuid) if server.squad_uuid in normalized else len(normalized),
    )
    return ordered_servers


async def _resolve_promo_groups(
    db: AsyncSession,
    promo_group_ids: Optional[Iterable[int]],
) -> List[PromoGroup]:
    if promo_group_ids is None:
        return []

    normalized = [int(pg_id) for pg_id in {int(pg_id) for pg_id in promo_group_ids}]
    if not normalized:
        return []

    result = await db.execute(select(PromoGroup).where(PromoGroup.id.in_(normalized)))
    promo_groups = result.scalars().all()

    missing = set(normalized) - {group.id for group in promo_groups}
    if missing:
        logger.warning("Не найдены промогруппы для тарифов: %s", ", ".join(map(str, sorted(missing))))

    return promo_groups


def _normalize_prices(prices: Iterable[dict]) -> List[SubscriptionTariffPrice]:
    unique_periods = {}
    for price in prices or []:
        try:
            period = int(price.get('period_days'))
            amount = int(price.get('price_kopeks'))
        except (TypeError, ValueError):
            continue

        if period <= 0 or amount < 0:
            continue

        unique_periods[period] = amount

    normalized = [
        SubscriptionTariffPrice(period_days=period, price_kopeks=amount)
        for period, amount in sorted(unique_periods.items())
    ]
    return normalized


async def create_tariff(
    db: AsyncSession,
    *,
    name: str,
    description: Optional[str] = None,
    traffic_limit_gb: int = 0,
    device_limit: int = 1,
    server_uuids: Sequence[str] = (),
    promo_group_ids: Optional[Iterable[int]] = None,
    prices: Iterable[dict] = (),
    is_active: bool = True,
    sort_order: int = 0,
) -> SubscriptionTariff:
    servers = await _resolve_servers(db, server_uuids)
    promo_groups = await _resolve_promo_groups(db, promo_group_ids)
    price_models = _normalize_prices(prices)

    tariff = SubscriptionTariff(
        name=name.strip(),
        description=description,
        traffic_limit_gb=max(0, int(traffic_limit_gb or 0)),
        device_limit=max(1, int(device_limit or 1)),
        is_active=bool(is_active),
        sort_order=int(sort_order or 0),
        server_squads=servers,
        promo_groups=promo_groups,
        prices=price_models,
    )

    db.add(tariff)
    await db.commit()
    await db.refresh(tariff)

    logger.info("Создан тариф '%s' (ID: %s)", tariff.name, tariff.id)
    return tariff


async def update_tariff(
    db: AsyncSession,
    tariff_id: int,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    traffic_limit_gb: Optional[int] = None,
    device_limit: Optional[int] = None,
    server_uuids: Optional[Sequence[str]] = None,
    promo_group_ids: Optional[Iterable[int]] = None,
    prices: Optional[Iterable[dict]] = None,
    is_active: Optional[bool] = None,
    sort_order: Optional[int] = None,
) -> Optional[SubscriptionTariff]:
    tariff = await get_tariff_by_id(db, tariff_id, include_inactive=True)
    if not tariff:
        return None

    if name is not None:
        tariff.name = name.strip()
    if description is not None:
        tariff.description = description
    if traffic_limit_gb is not None:
        tariff.traffic_limit_gb = max(0, int(traffic_limit_gb))
    if device_limit is not None:
        tariff.device_limit = max(1, int(device_limit))
    if is_active is not None:
        tariff.is_active = bool(is_active)
    if sort_order is not None:
        tariff.sort_order = int(sort_order)

    if server_uuids is not None:
        tariff.server_squads = await _resolve_servers(db, server_uuids)
    if promo_group_ids is not None:
        tariff.promo_groups = await _resolve_promo_groups(db, promo_group_ids)
    if prices is not None:
        tariff.prices = _normalize_prices(prices)

    await db.commit()
    await db.refresh(tariff)
    logger.info("Обновлен тариф '%s' (ID: %s)", tariff.name, tariff.id)
    return tariff


async def delete_tariff(db: AsyncSession, tariff_id: int) -> bool:
    result = await db.execute(
        select(func.count(Subscription.id)).where(Subscription.tariff_id == tariff_id)
    )
    active_subscriptions = result.scalar() or 0
    if active_subscriptions > 0:
        logger.warning(
            "Нельзя удалить тариф %s: %s подписок использует его",
            tariff_id,
            active_subscriptions,
        )
        return False

    await db.execute(
        delete(SubscriptionTariff).where(SubscriptionTariff.id == tariff_id)
    )
    await db.commit()
    logger.info("Удален тариф ID %s", tariff_id)
    return True


async def get_active_tariffs_for_promo_group(
    db: AsyncSession,
    promo_group_id: Optional[int],
) -> List[SubscriptionTariff]:
    tariffs = await list_tariffs(db, include_inactive=False)
    result = []
    for tariff in tariffs:
        if not tariff.is_available_for_promo_group(promo_group_id):
            continue
        if not tariff.server_squads:
            continue
        available_servers = [
            server
            for server in tariff.server_squads
            if server.is_available and not server.is_full
        ]
        if not available_servers:
            continue
        tariff.server_squads = available_servers
        result.append(tariff)
    return result
