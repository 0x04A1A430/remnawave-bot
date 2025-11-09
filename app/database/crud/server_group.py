from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

from sqlalchemy import and_, delete, func, select, update, cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    ServerGroup,
    ServerGroupServer,
    ServerSquad,
    Subscription,
    SubscriptionStatus,
)

logger = logging.getLogger(__name__)


async def get_server_groups(
    db: AsyncSession,
    *,
    include_servers: bool = True,
) -> List[ServerGroup]:
    """Возвращает список всех групп серверов."""

    query = (
        select(ServerGroup)
        .order_by(ServerGroup.sort_order, ServerGroup.name)
    )

    if include_servers:
        query = query.options(
            selectinload(ServerGroup.servers).selectinload(ServerGroupServer.server)
        )

    result = await db.execute(query)
    return list(result.scalars().unique().all())


async def get_server_group_by_id(
    db: AsyncSession,
    group_id: int,
    *,
    include_servers: bool = True,
) -> Optional[ServerGroup]:
    query = select(ServerGroup).where(ServerGroup.id == group_id)
    if include_servers:
        query = query.options(
            selectinload(ServerGroup.servers).selectinload(ServerGroupServer.server)
        )

    result = await db.execute(query)
    return result.scalars().unique().one_or_none()


async def get_server_group_by_name(db: AsyncSession, name: str) -> Optional[ServerGroup]:
    result = await db.execute(
        select(ServerGroup).where(func.lower(ServerGroup.name) == func.lower(name))
    )
    return result.scalars().one_or_none()


async def create_server_group(
    db: AsyncSession,
    *,
    name: str,
    server_ids: Optional[Sequence[int]] = None,
    sort_order: int = 0,
    is_active: bool = True,
) -> ServerGroup:
    existing = await get_server_group_by_name(db, name)
    if existing:
        raise ValueError("Группа с таким названием уже существует")

    group = ServerGroup(name=name.strip(), sort_order=sort_order, is_active=is_active)
    db.add(group)
    await db.flush()

    await _sync_group_servers(db, group, server_ids or [])
    await db.commit()
    await db.refresh(group)
    return group


async def update_server_group(
    db: AsyncSession,
    group_id: int,
    *,
    name: Optional[str] = None,
    server_ids: Optional[Sequence[int]] = None,
    sort_order: Optional[int] = None,
    is_active: Optional[bool] = None,
) -> Optional[ServerGroup]:
    group = await get_server_group_by_id(db, group_id)
    if not group:
        return None

    updates: Dict = {}
    if name is not None:
        trimmed = name.strip()
        if trimmed and trimmed.lower() != group.name.lower():
            duplicate = await get_server_group_by_name(db, trimmed)
            if duplicate and duplicate.id != group.id:
                raise ValueError("Группа с таким названием уже существует")
        updates["name"] = trimmed
    if sort_order is not None:
        updates["sort_order"] = int(sort_order)
    if is_active is not None:
        updates["is_active"] = bool(is_active)

    if updates:
        await db.execute(
            update(ServerGroup)
            .where(ServerGroup.id == group.id)
            .values(**updates)
        )

    if server_ids is not None:
        await _sync_group_servers(db, group, server_ids)

    await db.commit()
    return await get_server_group_by_id(db, group_id)


async def delete_server_group(db: AsyncSession, group_id: int) -> bool:
    group = await get_server_group_by_id(db, group_id)
    if not group:
        return False

    if await is_group_in_use(db, group):
        raise ValueError("Нельзя удалить группу, пока её серверы используются активными подписками")

    await db.execute(delete(ServerGroup).where(ServerGroup.id == group.id))
    await db.commit()
    return True


async def toggle_group_server(
    db: AsyncSession,
    group_id: int,
    server_id: int,
    *,
    is_enabled: bool,
) -> bool:
    result = await db.execute(
        update(ServerGroupServer)
        .where(
            and_(
                ServerGroupServer.group_id == group_id,
                ServerGroupServer.server_squad_id == server_id,
            )
        )
        .values(is_enabled=is_enabled)
    )
    if getattr(result, "rowcount", 0):
        await db.commit()
        return True
    return False


async def _sync_group_servers(
    db: AsyncSession,
    group: ServerGroup,
    server_ids: Sequence[int],
) -> None:
    unique_ids = {int(server_id) for server_id in server_ids if server_id}

    existing_ids = {member.server_squad_id for member in group.servers}

    to_remove = existing_ids - unique_ids
    to_add = unique_ids - existing_ids

    if to_remove:
        await db.execute(
            delete(ServerGroupServer).where(
                and_(
                    ServerGroupServer.group_id == group.id,
                    ServerGroupServer.server_squad_id.in_(to_remove),
                )
            )
        )

    if to_add:
        new_relations = [
            ServerGroupServer(group_id=group.id, server_squad_id=server_id)
            for server_id in to_add
        ]
        db.add_all(new_relations)

    await db.flush()


async def is_group_in_use(
    db: AsyncSession,
    group: ServerGroup | int,
) -> bool:
    if isinstance(group, int):
        group_obj = await get_server_group_by_id(db, group)
    else:
        group_obj = group

    if not group_obj:
        return False

    squad_uuids = [
        member.server.squad_uuid
        for member in group_obj.servers
        if member.server and member.server.squad_uuid
    ]

    if not squad_uuids:
        return False

    like_filters = [
        cast(Subscription.connected_squads, String).like(f'%"{uuid}"%')
        for uuid in squad_uuids
    ]

    condition = like_filters[0]
    for clause in like_filters[1:]:
        condition = condition | clause

    result = await db.execute(
        select(func.count(Subscription.id)).where(
            Subscription.status.in_(
                [
                    SubscriptionStatus.ACTIVE.value,
                    SubscriptionStatus.TRIAL.value,
                ]
            ),
            condition,
        )
    )

    return (result.scalar() or 0) > 0


async def get_group_server_ids(group: ServerGroup) -> List[int]:
    return [member.server_squad_id for member in group.servers if member.server_squad_id]


async def get_group_server_uuids(group: ServerGroup) -> List[str]:
    return [
        member.server.squad_uuid
        for member in group.servers
        if member.server and member.server.squad_uuid
    ]
