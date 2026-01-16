"""Admin routes for Ban System monitoring in cabinet."""

import logging
from typing import Optional, List, Any

from fastapi import APIRouter, Depends, HTTPException, status, Query

from app.config import settings
from app.database.models import User
from app.external.ban_system_api import BanSystemAPI, BanSystemAPIError

from ..dependencies import get_current_admin_user
from ..schemas.ban_system import (
    BanSystemStatusResponse,
    BanSystemStatsResponse,
    BanUsersListResponse,
    BanUserListItem,
    BanUserDetailResponse,
    BanUserIPInfo,
    BanUserRequestLog,
    BanPunishmentsListResponse,
    BanPunishmentItem,
    BanHistoryResponse,
    BanUserRequest,
    UnbanResponse,
    BanNodesListResponse,
    BanNodeItem,
    BanAgentsListResponse,
    BanAgentItem,
    BanAgentsSummary,
    BanTrafficViolationsResponse,
    BanTrafficViolationItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/ban-system", tags=["Cabinet Admin Ban System"])


def _get_ban_api() -> BanSystemAPI:
    """Get Ban System API instance."""
    if not settings.is_ban_system_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ban System integration is disabled",
        )

    if not settings.is_ban_system_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ban System is not configured",
        )

    return BanSystemAPI(
        base_url=settings.get_ban_system_api_url(),
        api_token=settings.get_ban_system_api_token(),
        timeout=settings.get_ban_system_request_timeout(),
    )


async def _api_request(api: BanSystemAPI, method: str, *args, **kwargs) -> Any:
    """Execute API request with error handling."""
    try:
        async with api:
            func = getattr(api, method)
            return await func(*args, **kwargs)
    except BanSystemAPIError as e:
        logger.error(f"Ban System API error: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ban System API error: {e.message}",
        )
    except Exception as e:
        logger.error(f"Ban System unexpected error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal error: {str(e)}",
        )


# === Status ===

@router.get("/status", response_model=BanSystemStatusResponse)
async def get_ban_system_status(
    admin: User = Depends(get_current_admin_user),
) -> BanSystemStatusResponse:
    """Get Ban System integration status."""
    return BanSystemStatusResponse(
        enabled=settings.is_ban_system_enabled(),
        configured=settings.is_ban_system_configured(),
    )


# === Stats ===

@router.get("/stats", response_model=BanSystemStatsResponse)
async def get_stats(
    admin: User = Depends(get_current_admin_user),
) -> BanSystemStatsResponse:
    """Get overall Ban System statistics."""
    api = _get_ban_api()
    data = await _api_request(api, "get_stats")

    return BanSystemStatsResponse(
        total_users=data.get("total_users", 0),
        active_users=data.get("active_users", 0),
        users_over_limit=data.get("users_over_limit", 0),
        total_requests=data.get("total_requests", 0),
        total_punishments=data.get("total_punishments", 0),
        active_punishments=data.get("active_punishments", 0),
        nodes_online=data.get("nodes_online", 0),
        nodes_total=data.get("nodes_total", 0),
        agents_online=data.get("agents_online", 0),
        agents_total=data.get("agents_total", 0),
        panel_connected=data.get("panel_connected", False),
        uptime_seconds=data.get("uptime_seconds"),
    )


# === Users ===

@router.get("/users", response_model=BanUsersListResponse)
async def get_users(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter: over_limit, with_limit, unlimited"),
    admin: User = Depends(get_current_admin_user),
) -> BanUsersListResponse:
    """Get list of users from Ban System."""
    api = _get_ban_api()
    data = await _api_request(api, "get_users", offset=offset, limit=limit, status=status)

    users = []
    for user_data in data.get("users", []):
        users.append(BanUserListItem(
            email=user_data.get("email", ""),
            unique_ip_count=user_data.get("unique_ip_count", 0),
            total_requests=user_data.get("total_requests", 0),
            limit=user_data.get("limit"),
            is_over_limit=user_data.get("is_over_limit", False),
            blocked_count=user_data.get("blocked_count", 0),
        ))

    return BanUsersListResponse(
        users=users,
        total=data.get("total", len(users)),
        offset=offset,
        limit=limit,
    )


@router.get("/users/over-limit", response_model=BanUsersListResponse)
async def get_users_over_limit(
    limit: int = Query(50, ge=1, le=100),
    admin: User = Depends(get_current_admin_user),
) -> BanUsersListResponse:
    """Get users who exceeded their device limit."""
    api = _get_ban_api()
    data = await _api_request(api, "get_users_over_limit", limit=limit)

    users = []
    for user_data in data.get("users", []):
        users.append(BanUserListItem(
            email=user_data.get("email", ""),
            unique_ip_count=user_data.get("unique_ip_count", 0),
            total_requests=user_data.get("total_requests", 0),
            limit=user_data.get("limit"),
            is_over_limit=True,
            blocked_count=user_data.get("blocked_count", 0),
        ))

    return BanUsersListResponse(
        users=users,
        total=len(users),
        offset=0,
        limit=limit,
    )


@router.get("/users/search/{query}")
async def search_users(
    query: str,
    admin: User = Depends(get_current_admin_user),
) -> BanUsersListResponse:
    """Search for users."""
    api = _get_ban_api()
    data = await _api_request(api, "search_users", query=query)

    users = []
    users_data = data.get("users", []) if isinstance(data, dict) else data
    for user_data in users_data:
        users.append(BanUserListItem(
            email=user_data.get("email", ""),
            unique_ip_count=user_data.get("unique_ip_count", 0),
            total_requests=user_data.get("total_requests", 0),
            limit=user_data.get("limit"),
            is_over_limit=user_data.get("is_over_limit", False),
            blocked_count=user_data.get("blocked_count", 0),
        ))

    return BanUsersListResponse(
        users=users,
        total=len(users),
        offset=0,
        limit=100,
    )


@router.get("/users/{email}", response_model=BanUserDetailResponse)
async def get_user_detail(
    email: str,
    admin: User = Depends(get_current_admin_user),
) -> BanUserDetailResponse:
    """Get detailed user information."""
    api = _get_ban_api()
    data = await _api_request(api, "get_user", email=email)

    ips = []
    for ip_data in data.get("ips", {}).values() if isinstance(data.get("ips"), dict) else data.get("ips", []):
        ips.append(BanUserIPInfo(
            ip=ip_data.get("ip", ""),
            first_seen=ip_data.get("first_seen"),
            last_seen=ip_data.get("last_seen"),
            node=ip_data.get("node"),
            request_count=ip_data.get("request_count", 0),
            country_code=ip_data.get("country_code"),
            country_name=ip_data.get("country_name"),
            city=ip_data.get("city"),
        ))

    recent_requests = []
    for req_data in data.get("recent_requests", []):
        recent_requests.append(BanUserRequestLog(
            timestamp=req_data.get("timestamp"),
            source_ip=req_data.get("source_ip", ""),
            destination=req_data.get("destination"),
            dest_port=req_data.get("dest_port"),
            protocol=req_data.get("protocol"),
            action=req_data.get("action"),
            node=req_data.get("node"),
        ))

    return BanUserDetailResponse(
        email=data.get("email", email),
        unique_ip_count=data.get("unique_ip_count", 0),
        total_requests=data.get("total_requests", 0),
        limit=data.get("limit"),
        is_over_limit=data.get("is_over_limit", False),
        blocked_count=data.get("blocked_count", 0),
        ips=ips,
        recent_requests=recent_requests,
        network_type=data.get("network_type"),
    )


# === Punishments ===

@router.get("/punishments", response_model=BanPunishmentsListResponse)
async def get_punishments(
    admin: User = Depends(get_current_admin_user),
) -> BanPunishmentsListResponse:
    """Get list of active punishments (bans)."""
    api = _get_ban_api()
    data = await _api_request(api, "get_punishments")

    punishments = []
    punishments_data = data if isinstance(data, list) else data.get("punishments", [])
    for p in punishments_data:
        punishments.append(BanPunishmentItem(
            id=p.get("id"),
            user_id=p.get("user_id", ""),
            uuid=p.get("uuid"),
            username=p.get("username", ""),
            reason=p.get("reason"),
            punished_at=p.get("punished_at"),
            enable_at=p.get("enable_at"),
            ip_count=p.get("ip_count", 0),
            limit=p.get("limit", 0),
            enabled=p.get("enabled", False),
            enabled_at=p.get("enabled_at"),
            node_name=p.get("node_name"),
        ))

    return BanPunishmentsListResponse(
        punishments=punishments,
        total=len(punishments),
    )


@router.post("/punishments/{user_id}/unban", response_model=UnbanResponse)
async def unban_user(
    user_id: str,
    admin: User = Depends(get_current_admin_user),
) -> UnbanResponse:
    """Unban (enable) a user."""
    api = _get_ban_api()
    try:
        await _api_request(api, "enable_user", user_id=user_id)
        logger.info(f"Admin {admin.id} unbanned user {user_id} in Ban System")
        return UnbanResponse(success=True, message="User unbanned successfully")
    except HTTPException:
        raise
    except Exception as e:
        return UnbanResponse(success=False, message=str(e))


@router.post("/ban", response_model=UnbanResponse)
async def ban_user(
    request: BanUserRequest,
    admin: User = Depends(get_current_admin_user),
) -> UnbanResponse:
    """Manually ban a user."""
    api = _get_ban_api()
    try:
        await _api_request(
            api,
            "ban_user",
            username=request.username,
            minutes=request.minutes,
            reason=request.reason,
        )
        logger.info(f"Admin {admin.id} banned user {request.username}: {request.reason}")
        return UnbanResponse(success=True, message="User banned successfully")
    except HTTPException:
        raise
    except Exception as e:
        return UnbanResponse(success=False, message=str(e))


@router.get("/history/{query}", response_model=BanHistoryResponse)
async def get_punishment_history(
    query: str,
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin_user),
) -> BanHistoryResponse:
    """Get punishment history for a user."""
    api = _get_ban_api()
    data = await _api_request(api, "get_punishment_history", query=query, limit=limit)

    items = []
    history_data = data if isinstance(data, list) else data.get("items", [])
    for p in history_data:
        items.append(BanPunishmentItem(
            id=p.get("id"),
            user_id=p.get("user_id", ""),
            uuid=p.get("uuid"),
            username=p.get("username", ""),
            reason=p.get("reason"),
            punished_at=p.get("punished_at"),
            enable_at=p.get("enable_at"),
            ip_count=p.get("ip_count", 0),
            limit=p.get("limit", 0),
            enabled=p.get("enabled", False),
            enabled_at=p.get("enabled_at"),
            node_name=p.get("node_name"),
        ))

    return BanHistoryResponse(
        items=items,
        total=len(items),
    )


# === Nodes ===

@router.get("/nodes", response_model=BanNodesListResponse)
async def get_nodes(
    admin: User = Depends(get_current_admin_user),
) -> BanNodesListResponse:
    """Get list of connected nodes."""
    api = _get_ban_api()
    data = await _api_request(api, "get_nodes")

    nodes = []
    nodes_data = data if isinstance(data, list) else data.get("nodes", [])
    online_count = 0
    for n in nodes_data:
        is_connected = n.get("is_connected", False)
        if is_connected:
            online_count += 1
        nodes.append(BanNodeItem(
            name=n.get("name", ""),
            address=n.get("address"),
            is_connected=is_connected,
            last_seen=n.get("last_seen"),
            users_count=n.get("users_count", 0),
            agent_stats=n.get("agent_stats"),
        ))

    return BanNodesListResponse(
        nodes=nodes,
        total=len(nodes),
        online=online_count,
    )


# === Agents ===

@router.get("/agents", response_model=BanAgentsListResponse)
async def get_agents(
    search: Optional[str] = Query(None),
    health: Optional[str] = Query(None, description="healthy, warning, critical"),
    agent_status: Optional[str] = Query(None, alias="status", description="online, offline"),
    admin: User = Depends(get_current_admin_user),
) -> BanAgentsListResponse:
    """Get list of monitoring agents."""
    api = _get_ban_api()
    data = await _api_request(
        api,
        "get_agents",
        search=search,
        health=health,
        status=agent_status,
    )

    agents = []
    agents_data = data.get("agents", []) if isinstance(data, dict) else data
    online_count = 0
    for a in agents_data:
        is_online = a.get("is_online", False)
        if is_online:
            online_count += 1
        agents.append(BanAgentItem(
            node_name=a.get("node_name", ""),
            sent_total=a.get("sent_total", 0),
            dropped_total=a.get("dropped_total", 0),
            batches_total=a.get("batches_total", 0),
            reconnects=a.get("reconnects", 0),
            failures=a.get("failures", 0),
            queue_size=a.get("queue_size", 0),
            queue_max=a.get("queue_max", 0),
            dedup_checked=a.get("dedup_checked", 0),
            dedup_skipped=a.get("dedup_skipped", 0),
            filter_checked=a.get("filter_checked", 0),
            filter_filtered=a.get("filter_filtered", 0),
            health=a.get("health", "unknown"),
            is_online=is_online,
            last_report=a.get("last_report"),
        ))

    summary = None
    if isinstance(data, dict) and "summary" in data:
        s = data["summary"]
        summary = BanAgentsSummary(
            total_agents=s.get("total_agents", len(agents)),
            online_agents=s.get("online_agents", online_count),
            total_sent=s.get("total_sent", 0),
            total_dropped=s.get("total_dropped", 0),
            avg_queue_size=s.get("avg_queue_size", 0.0),
            healthy_count=s.get("healthy_count", 0),
            warning_count=s.get("warning_count", 0),
            critical_count=s.get("critical_count", 0),
        )

    return BanAgentsListResponse(
        agents=agents,
        summary=summary,
        total=len(agents),
        online=online_count,
    )


@router.get("/agents/summary", response_model=BanAgentsSummary)
async def get_agents_summary(
    admin: User = Depends(get_current_admin_user),
) -> BanAgentsSummary:
    """Get agents summary statistics."""
    api = _get_ban_api()
    data = await _api_request(api, "get_agents_summary")

    return BanAgentsSummary(
        total_agents=data.get("total_agents", 0),
        online_agents=data.get("online_agents", 0),
        total_sent=data.get("total_sent", 0),
        total_dropped=data.get("total_dropped", 0),
        avg_queue_size=data.get("avg_queue_size", 0.0),
        healthy_count=data.get("healthy_count", 0),
        warning_count=data.get("warning_count", 0),
        critical_count=data.get("critical_count", 0),
    )


# === Traffic Violations ===

@router.get("/traffic/violations", response_model=BanTrafficViolationsResponse)
async def get_traffic_violations(
    limit: int = Query(50, ge=1, le=100),
    admin: User = Depends(get_current_admin_user),
) -> BanTrafficViolationsResponse:
    """Get list of traffic limit violations."""
    api = _get_ban_api()
    data = await _api_request(api, "get_traffic_violations", limit=limit)

    violations = []
    violations_data = data if isinstance(data, list) else data.get("violations", [])
    for v in violations_data:
        violations.append(BanTrafficViolationItem(
            id=v.get("id"),
            username=v.get("username", ""),
            email=v.get("email"),
            violation_type=v.get("violation_type", v.get("type", "")),
            description=v.get("description"),
            bytes_used=v.get("bytes_used", 0),
            bytes_limit=v.get("bytes_limit", 0),
            detected_at=v.get("detected_at"),
            resolved=v.get("resolved", False),
        ))

    return BanTrafficViolationsResponse(
        violations=violations,
        total=len(violations),
    )
