import logging
from typing import List, Optional, Tuple
from sqlalchemy import select, and_, func, update, delete, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    ServerSquad,
    SubscriptionServer,
    Subscription,
    PromoGroup,
    server_squad_promo_groups,
)

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
    is_available: bool = True,
    promo_group_ids: Optional[List[int]] = None,
) -> ServerSquad:
    result = await db.execute(
        select(PromoGroup.id)
        .where(PromoGroup.is_default.is_(True))
        .limit(1)
    )
    default_group_id = result.scalar_one_or_none()

    if promo_group_ids is None:
        promo_group_ids = []
        if default_group_id is not None:
            promo_group_ids.append(default_group_id)
        else:
            fallback_group_result = await db.execute(
                select(PromoGroup.id)
                .order_by(PromoGroup.is_default.desc(), PromoGroup.id)
                .limit(1)
            )
            fallback_group_id = fallback_group_result.scalar_one_or_none()
            if fallback_group_id is not None:
                promo_group_ids.append(fallback_group_id)

    unique_group_ids = list(dict.fromkeys(promo_group_ids or []))

    if not unique_group_ids:
        raise ValueError("Server squad must have at least one promo group")

    groups_result = await db.execute(
        select(PromoGroup).where(PromoGroup.id.in_(unique_group_ids))
    )
    groups = groups_result.scalars().all()

    if len(groups) != len(unique_group_ids):
        raise ValueError("One or more promo groups not found")

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
    await db.flush()

    server_squad.promo_groups = groups

    await db.commit()
    await db.refresh(server_squad)

    logger.info(
        "✅ Создан сервер %s (UUID: %s) с промогруппами: %s",
        display_name,
        squad_uuid,
        ", ".join(group.name for group in groups),
    )
    return server_squad


async def get_server_squad_by_uuid(
    db: AsyncSession, 
    squad_uuid: str
) -> Optional[ServerSquad]:
    
    result = await db.execute(
        select(ServerSquad)
        .options(selectinload(ServerSquad.promo_groups))
        .where(ServerSquad.squad_uuid == squad_uuid)
    )
    return result.scalar_one_or_none()


async def get_server_squad_by_id(
    db: AsyncSession, 
    server_id: int
) -> Optional[ServerSquad]:
    
    result = await db.execute(
        select(ServerSquad)
        .options(selectinload(ServerSquad.promo_groups))
        .where(ServerSquad.id == server_id)
    )
    return result.scalar_one_or_none()


async def get_all_server_squads(
    db: AsyncSession,
    available_only: bool = False,
    page: int = 1,
    limit: int = 50
) -> Tuple[List[ServerSquad], int]:
    
    query = select(ServerSquad).options(selectinload(ServerSquad.promo_groups))
    
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
        .options(selectinload(ServerSquad.promo_groups))
        .where(ServerSquad.is_available == True)
    )

    if promo_group_id is not None:
        query = (
            query.join(
                server_squad_promo_groups,
                server_squad_promo_groups.c.server_squad_id == ServerSquad.id,
            )
            .where(server_squad_promo_groups.c.promo_group_id == promo_group_id)
            .distinct()
        )

    query = query.order_by(ServerSquad.sort_order, ServerSquad.display_name)

    result = await db.execute(query)
    return result.scalars().all()


async def set_server_squad_promo_groups(
    db: AsyncSession,
    server_id: int,
    promo_group_ids: List[int],
) -> Optional[ServerSquad]:

    unique_group_ids = list(dict.fromkeys(promo_group_ids or []))

    if not unique_group_ids:
        logger.warning("Попытка оставить сервер без промогрупп (id=%s)", server_id)
        return None

    server = await get_server_squad_by_id(db, server_id)
    if not server:
        logger.warning("Сервер %s не найден при обновлении промогрупп", server_id)
        return None

    groups_result = await db.execute(
        select(PromoGroup).where(PromoGroup.id.in_(unique_group_ids))
    )
    groups = groups_result.scalars().all()

    if len(groups) != len(unique_group_ids):
        logger.warning(
            "Не все промогруппы найдены для сервера %s: %s",
            server_id,
            unique_group_ids,
        )
        return None

    server.promo_groups = groups
    await db.commit()
    await db.refresh(server)

    logger.info(
        "✅ Обновлены промогруппы сервера %s: %s",
        server.display_name,
        ", ".join(group.name for group in groups),
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
