from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.server_group import get_server_group_by_id
from app.database.crud.server_squad import count_active_users_for_squad
from app.database.models import ServerGroup, ServerGroupServer, ServerSquad
from app.services.remnawave_service import RemnaWaveService

logger = logging.getLogger(__name__)


@dataclass
class ServerLoadSnapshot:
    server: ServerSquad
    membership: ServerGroupServer
    active_users: int
    members_count: int
    bandwidth_bytes: int
    download_bytes: int
    upload_bytes: int
    is_available: bool
    is_enabled: bool
    is_overloaded: bool
    load_ratio: float

    @property
    def maintenance(self) -> bool:
        return not self.is_enabled


@dataclass
class ServerGroupSnapshot:
    group: ServerGroup
    servers: List[ServerLoadSnapshot]
    total_active_users: int
    total_bandwidth_bytes: int
    available_servers: List[ServerLoadSnapshot]

    @property
    def is_empty(self) -> bool:
        return not self.servers

    @property
    def is_overloaded(self) -> bool:
        return all(server.is_overloaded or not server.is_available for server in self.available_servers)


async def build_group_snapshot(
    db: AsyncSession,
    remnawave: RemnaWaveService,
    group: ServerGroup | int,
    *,
    refresh: bool = False,
) -> ServerGroupSnapshot:
    if isinstance(group, int):
        group_obj = await get_server_group_by_id(db, group)
    else:
        group_obj = group

    if not group_obj:
        raise ValueError("Server group not found")

    usage_map = await remnawave.get_internal_squad_usage_map(force_refresh=refresh)

    servers: List[ServerLoadSnapshot] = []
    total_active_users = 0
    total_bandwidth_bytes = 0

    for membership in group_obj.servers:
        server = membership.server
        if not server:
            continue

        usage = usage_map.get(server.squad_uuid, {}) or {}
        members_count = int(usage.get("members_count", 0) or 0)
        download_bytes = int(usage.get("download_bytes", 0) or 0)
        upload_bytes = int(usage.get("upload_bytes", 0) or 0)
        bandwidth_bytes = int(usage.get("bandwidth_bytes", download_bytes + upload_bytes) or 0)

        db_active = await count_active_users_for_squad(db, server.squad_uuid)
        active_users = max(db_active, members_count, int(server.current_users or 0))

        capacity = server.max_users or 0
        load_ratio = (active_users / capacity) if capacity else float(active_users)
        is_overloaded = capacity > 0 and active_users >= capacity

        snapshot = ServerLoadSnapshot(
            server=server,
            membership=membership,
            active_users=active_users,
            members_count=members_count,
            bandwidth_bytes=bandwidth_bytes,
            download_bytes=download_bytes,
            upload_bytes=upload_bytes,
            is_available=bool(server.is_available),
            is_enabled=bool(membership.is_enabled),
            is_overloaded=is_overloaded,
            load_ratio=load_ratio,
        )
        servers.append(snapshot)
        total_active_users += active_users
        total_bandwidth_bytes += bandwidth_bytes

    available_servers = [
        server
        for server in servers
        if server.is_available and server.is_enabled
    ]

    return ServerGroupSnapshot(
        group=group_obj,
        servers=servers,
        total_active_users=total_active_users,
        total_bandwidth_bytes=total_bandwidth_bytes,
        available_servers=available_servers,
    )


async def choose_optimal_server(
    db: AsyncSession,
    remnawave: RemnaWaveService,
    group: ServerGroup | int,
    *,
    refresh_stats: bool = False,
    notify_overload: Optional[Callable[[ServerGroupSnapshot], None]] = None,
) -> Optional[tuple[ServerGroupSnapshot, ServerLoadSnapshot]]:
    snapshot = await build_group_snapshot(db, remnawave, group, refresh=refresh_stats)

    if not snapshot.servers:
        logger.warning("Server group %s не содержит серверов", getattr(snapshot.group, "name", snapshot.group.id))
        return None

    candidates = [
        server for server in snapshot.servers if server.is_available and server.is_enabled
    ]

    if not candidates:
        candidates = [server for server in snapshot.servers if server.is_enabled]

    if not candidates:
        logger.error("Нет доступных серверов в группе %s", snapshot.group.name)
        if notify_overload:
            notify_overload(snapshot)
        return None

    candidates.sort(
        key=lambda item: (
            item.active_users,
            item.bandwidth_bytes,
            item.server.current_users or 0,
            item.server.sort_order,
            item.server.display_name,
        )
    )

    best = candidates[0]

    logger.info(
        "Выбран сервер %s (%s) для группы %s: активных=%s, трафик=%s",
        best.server.display_name,
        best.server.squad_uuid,
        snapshot.group.name,
        best.active_users,
        best.bandwidth_bytes,
    )

    if notify_overload and snapshot.is_overloaded:
        notify_overload(snapshot)

    return snapshot, best
