import logging
from typing import List, Optional, Tuple
from sqlalchemy import select, and_, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import ServerSquad, SubscriptionServer

logger = logging.getLogger(__name__)


async def create_server_squad(
    db: AsyncSession,
    squad_uuid: str,
    display_name: str,
    original_name: str = None,
    country_code: str = None,
    price_kopeks: int = 0,
    description: str = None,
    max_users: int = None,
    is_available: bool = True
) -> ServerSquad:
    
    server_squad = ServerSquad(
        squad_uuid=squad_uuid,
        display_name=display_name,
        original_name=original_name,
        country_code=country_code,
        price_kopeks=price_kopeks,
        description=description,
        max_users=max_users,
        is_available=is_available
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
        select(ServerSquad).where(ServerSquad.squad_uuid == squad_uuid)
    )
    return result.scalar_one_or_none()


async def get_server_squad_by_id(
    db: AsyncSession, 
    server_id: int
) -> Optional[ServerSquad]:
    
    result = await db.execute(
        select(ServerSquad).where(ServerSquad.id == server_id)
    )
    return result.scalar_one_or_none()


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


async def get_available_server_squads(db: AsyncSession) -> List[ServerSquad]:
    
    result = await db.execute(
        select(ServerSquad)
        .where(ServerSquad.is_available == True)
        .order_by(ServerSquad.sort_order, ServerSquad.display_name)
    )
    return result.scalars().all()


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
        logger.warning(f"❌ Нельзя удалить сервер {server_id}: есть активные подключения ({connections_count})")
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
    
    with_connections_result = await db.execute(
        select(func.count(func.distinct(SubscriptionServer.server_squad_id)))
    )
    servers_with_connections = with_connections_result.scalar()
    
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
        result = await db.execute(
            select(
                ServerSquad.id,
                ServerSquad.squad_uuid,
                func.count(SubscriptionServer.id).label('actual_users')
            )
            .outerjoin(SubscriptionServer, ServerSquad.id == SubscriptionServer.server_squad_id)
            .join(Subscription, SubscriptionServer.subscription_id == Subscription.id)
            .where(Subscription.status == 'active')
            .group_by(ServerSquad.id, ServerSquad.squad_uuid)
        )
        
        updated_count = 0
        for server_id, squad_uuid, actual_users in result.fetchall():
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