import logging
from typing import Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select, and_, func, update, delete, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import PromoGroup, ServerSquad, SubscriptionServer, Subscription

logger = logging.getLogger(__name__)


async def _get_default_promo_group_id(db: AsyncSession) -> Optional[int]:
    result = await db.execute(
        select(PromoGroup.id).where(PromoGroup.is_default.is_(True)).limit(1)
    )
    return result.scalar_one_or_none()


async def create_server_squad(
    db: AsyncSession,
    squad_uuid: str,
    display_name: str,
    original_name: str = None,
    country_code: str = None,
    price_kopeks: int = 0,
    description: str = None,
    max_users: int = None,
    is_available: bool = True,
    promo_group_ids: Optional[Iterable[int]] = None,
) -> ServerSquad:

    normalized_group_ids: Sequence[int]
    if promo_group_ids is None:
        default_id = await _get_default_promo_group_id(db)
        normalized_group_ids = [default_id] if default_id is not None else []
    else:
        normalized_group_ids = [int(pg_id) for pg_id in set(promo_group_ids)]

    if not normalized_group_ids:
        raise ValueError("Server squad must be linked to at least one promo group")

    promo_groups_result = await db.execute(
        select(PromoGroup).where(PromoGroup.id.in_(normalized_group_ids))
    )
    promo_groups = promo_groups_result.scalars().all()

    if len(promo_groups) != len(normalized_group_ids):
        logger.warning(
            "Не все промогруппы найдены при создании сервера %s", display_name
        )

    server_squad = ServerSquad(
        squad_uuid=squad_uuid,
        display_name=display_name,
        original_name=original_name,
        country_code=country_code,
        price_kopeks=price_kopeks,
        description=description,
        max_users=max_users,
        is_available=is_available,
        allowed_promo_groups=promo_groups,
    )

    db.add(server_squad)
    await db.commit()
    await db.refresh(server_squad)
    
    logger.info(f"✅ Создан сервер {display_name} (UUID: {squad_uuid})")
    return server_squad


async def get_server_squad_by_uuid(
    db: AsyncSession, 
    squad_uuid: str
) -> Optional[ServerSquad]:
    
    result = await db.execute(
        select(ServerSquad)
        .options(selectinload(ServerSquad.allowed_promo_groups))
        .where(ServerSquad.squad_uuid == squad_uuid)
    )
    return result.scalars().unique().one_or_none()


async def get_server_squad_by_id(
    db: AsyncSession, 
    server_id: int
) -> Optional[ServerSquad]:
    
    result = await db.execute(
        select(ServerSquad)
        .options(selectinload(ServerSquad.allowed_promo_groups))
        .where(ServerSquad.id == server_id)
    )
    return result.scalars().unique().one_or_none()


async def get_all_server_squads(
    db: AsyncSession,
    available_only: bool = False,
    page: int = 1,
    limit: int = 50
) -> Tuple[List[ServerSquad], int]:
    
    query = select(ServerSquad)
    
    if available_only:
        query = query.where(ServerSquad.is_available == True)
    
    count_query = select(func.count(ServerSquad.id))
    if available_only:
        count_query = count_query.where(ServerSquad.is_available == True)
    
    count_result = await db.execute(count_query)
    total_count = count_result.scalar()
    
    offset = (page - 1) * limit
    query = query.order_by(ServerSquad.sort_order, ServerSquad.display_name)
    query = query.offset(offset).limit(limit)
    
    result = await db.execute(query)
    servers = result.scalars().all()
    
    return servers, total_count


async def get_available_server_squads(
    db: AsyncSession,
    promo_group_id: Optional[int] = None,
) -> List[ServerSquad]:

    query = (
        select(ServerSquad)
        .options(selectinload(ServerSquad.allowed_promo_groups))
        .where(ServerSquad.is_available.is_(True))
        .order_by(ServerSquad.sort_order, ServerSquad.display_name)
    )

    if promo_group_id is not None:
        query = query.join(ServerSquad.allowed_promo_groups).where(
            PromoGroup.id == promo_group_id
        )

    result = await db.execute(query)
    return result.scalars().unique().all()


async def get_promo_group_server_count(
    db: AsyncSession,
    promo_group_id: int,
    *,
    include_unavailable: bool = True,
) -> int:
    """Возвращает количество серверов, связанных с промогруппой."""

    query = (
        select(func.count(func.distinct(ServerSquad.id)))
        .join(ServerSquad.allowed_promo_groups)
        .where(PromoGroup.id == promo_group_id)
    )

    if not include_unavailable:
        query = query.where(ServerSquad.is_available.is_(True))

    result = await db.execute(query)
    return result.scalar_one_or_none() or 0


async def update_server_squad_promo_groups(
    db: AsyncSession, server_id: int, promo_group_ids: Iterable[int]
) -> Optional[ServerSquad]:
    unique_ids = [int(pg_id) for pg_id in set(promo_group_ids)]

    if not unique_ids:
        raise ValueError("Нужно выбрать хотя бы одну промогруппу")

    server = await get_server_squad_by_id(db, server_id)
    if not server:
        return None

    result = await db.execute(
        select(PromoGroup).where(PromoGroup.id.in_(unique_ids))
    )
    promo_groups = result.scalars().all()

    if not promo_groups:
        raise ValueError("Не найдены промогруппы для обновления сервера")

    server.allowed_promo_groups = promo_groups
    await db.commit()
    await db.refresh(server)

    logger.info(
        "Обновлены промогруппы сервера %s (ID: %s): %s",
        server.display_name,
        server.id,
        ", ".join(pg.name for pg in promo_groups),
    )

    return server


async def update_server_squad(
    db: AsyncSession,
    server_id: int,
    **updates
) -> Optional[ServerSquad]:
    
    valid_fields = {
        'display_name', 'country_code', 'price_kopeks', 'description',
        'max_users', 'is_available', 'sort_order'
    }
    
    filtered_updates = {k: v for k, v in updates.items() if k in valid_fields}
    
    if not filtered_updates:
        return None
    
    await db.execute(
        update(ServerSquad)
        .where(ServerSquad.id == server_id)
        .values(**filtered_updates)
    )
    
    await db.commit()
    
    return await get_server_squad_by_id(db, server_id)


async def delete_server_squad(db: AsyncSession, server_id: int) -> bool:
    
    connections_result = await db.execute(
        select(func.count(SubscriptionServer.id))
        .where(SubscriptionServer.server_squad_id == server_id)
    )
    connections_count = connections_result.scalar()
    
    if connections_count > 0:
        logger.warning(f"⚠ Нельзя удалить сервер {server_id}: есть активные подключения ({connections_count})")
        return False
    
    await db.execute(
        delete(ServerSquad).where(ServerSquad.id == server_id)
    )
    await db.commit()
    
    logger.info(f"🗑️ Удален сервер (ID: {server_id})")
    return True


async def sync_with_remnawave(
    db: AsyncSession,
    remnawave_squads: List[dict]
) -> Tuple[int, int, int]:
    
    created = 0
    updated = 0
    disabled = 0
    
    existing_servers = {}
    result = await db.execute(select(ServerSquad))
    for server in result.scalars().all():
        existing_servers[server.squad_uuid] = server
    
    remnawave_uuids = {squad['uuid'] for squad in remnawave_squads}
    
    for squad in remnawave_squads:
        squad_uuid = squad['uuid']
        original_name = squad.get('name', f'Squad {squad_uuid[:8]}')
        
        if squad_uuid in existing_servers:
            server = existing_servers[squad_uuid]
            if server.original_name != original_name:
                server.original_name = original_name
                updated += 1
        else:
            await create_server_squad(
                db=db,
                squad_uuid=squad_uuid,
                display_name=_generate_display_name(original_name),
                original_name=original_name,
                country_code=_extract_country_code(original_name),
                price_kopeks=1000, 
                is_available=False 
            )
            created += 1
    
    for uuid, server in existing_servers.items():
        if uuid not in remnawave_uuids and server.is_available:
            server.is_available = False
            disabled += 1
    
    await db.commit()
    
    logger.info(f"🔄 Синхронизация завершена: +{created} ~{updated} -{disabled}")
    return created, updated, disabled


def _generate_display_name(original_name: str) -> str:
    
    country_names = {
        'NL': '🇳🇱 Нидерланды',
        'DE': '🇩🇪 Германия', 
        'US': '🇺🇸 США',
        'FR': '🇫🇷 Франция',
        'GB': '🇬🇧 Великобритания',
        'IT': '🇮🇹 Италия',
        'ES': '🇪🇸 Испания',
        'CA': '🇨🇦 Канада',
        'JP': '🇯🇵 Япония',
        'SG': '🇸🇬 Сингапур',
        'AU': '🇦🇺 Австралия',
    }
    
    name_upper = original_name.upper()
    for code, display_name in country_names.items():
        if code in name_upper:
            return display_name
    
    return f"🌍 {original_name}"


def _extract_country_code(original_name: str) -> Optional[str]:
    
    codes = ['NL', 'DE', 'US', 'FR', 'GB', 'IT', 'ES', 'CA', 'JP', 'SG', 'AU']
    name_upper = original_name.upper()
    
    for code in codes:
        if code in name_upper:
            return code
    
    return None


async def get_server_statistics(db: AsyncSession) -> dict:
    
    total_result = await db.execute(select(func.count(ServerSquad.id)))
    total_servers = total_result.scalar()
    
    available_result = await db.execute(
        select(func.count(ServerSquad.id))
        .where(ServerSquad.is_available == True)
    )
    available_servers = available_result.scalar()
    
    servers_with_connections = 0
    all_servers_result = await db.execute(select(ServerSquad.squad_uuid))
    all_server_uuids = [row[0] for row in all_servers_result.fetchall()]
    
    for squad_uuid in all_server_uuids:
        count_result = await db.execute(
            text("""
                SELECT COUNT(s.id) 
                FROM subscriptions s 
                WHERE s.status IN ('active', 'trial') 
                AND s.connected_squads::text LIKE :uuid_pattern
            """),
            {"uuid_pattern": f'%"{squad_uuid}"%'}
        )
        user_count = count_result.scalar() or 0
        if user_count > 0:
            servers_with_connections += 1
    
    revenue_result = await db.execute(
        select(func.coalesce(func.sum(SubscriptionServer.paid_price_kopeks), 0))
    )
    total_revenue_kopeks = revenue_result.scalar()
    
    return {
        'total_servers': total_servers,
        'available_servers': available_servers,
        'unavailable_servers': total_servers - available_servers,
        'servers_with_connections': servers_with_connections,
        'total_revenue_kopeks': total_revenue_kopeks,
        'total_revenue_rubles': total_revenue_kopeks / 100
    }

async def add_user_to_servers(
    db: AsyncSession,
    server_squad_ids: List[int]
) -> bool:
    
    try:
        for server_id in server_squad_ids:
            await db.execute(
                update(ServerSquad)
                .where(ServerSquad.id == server_id)
                .values(current_users=ServerSquad.current_users + 1)
            )
        
        await db.commit()
        logger.info(f"✅ Увеличен счетчик пользователей для серверов: {server_squad_ids}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка увеличения счетчика пользователей: {e}")
        await db.rollback()
        return False


async def remove_user_from_servers(
    db: AsyncSession,
    server_squad_ids: List[int]
) -> bool:
    
    try:
        for server_id in server_squad_ids:
            await db.execute(
                update(ServerSquad)
                .where(ServerSquad.id == server_id)
                .values(current_users=func.greatest(ServerSquad.current_users - 1, 0))
            )
        
        await db.commit()
        logger.info(f"✅ Уменьшен счетчик пользователей для серверов: {server_squad_ids}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка уменьшения счетчика пользователей: {e}")
        await db.rollback()
        return False


async def get_server_ids_by_uuids(
    db: AsyncSession,
    squad_uuids: List[str]
) -> List[int]:
    
    result = await db.execute(
        select(ServerSquad.id)
        .where(ServerSquad.squad_uuid.in_(squad_uuids))
    )
    return [row[0] for row in result.fetchall()]


async def sync_server_user_counts(db: AsyncSession) -> int:
    
    try:
        all_servers_result = await db.execute(select(ServerSquad.id, ServerSquad.squad_uuid))
        all_servers = all_servers_result.fetchall()
        
        logger.info(f"🔍 Найдено серверов для синхронизации: {len(all_servers)}")
        
        updated_count = 0
        for server_id, squad_uuid in all_servers:
            count_result = await db.execute(
                text("""
                    SELECT COUNT(s.id) 
                    FROM subscriptions s 
                    WHERE s.status IN ('active', 'trial') 
                    AND s.connected_squads::text LIKE :uuid_pattern
                """),
                {"uuid_pattern": f'%"{squad_uuid}"%'}
            )
            actual_users = count_result.scalar() or 0
            
            logger.info(f"📊 Сервер {server_id} ({squad_uuid[:8]}): {actual_users} пользователей")
            
            await db.execute(
                update(ServerSquad)
                .where(ServerSquad.id == server_id)
                .values(current_users=actual_users)
            )
            updated_count += 1
        
        await db.commit()
        logger.info(f"✅ Синхронизированы счетчики для {updated_count} серверов")
        return updated_count
        
    except Exception as e:
        logger.error(f"Ошибка синхронизации счетчиков пользователей: {e}")
        await db.rollback()
        return 0
