"""Schemas for RemnaWave management in cabinet admin panel."""

from datetime import datetime, time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ============ Status & Connection ============

class ConnectionStatus(BaseModel):
    """RemnaWave API connection status."""
    status: str
    message: str
    api_url: Optional[str] = None
    status_code: Optional[int] = None
    system_info: Optional[Dict[str, Any]] = None


class RemnaWaveStatusResponse(BaseModel):
    """RemnaWave configuration and connection status."""
    is_configured: bool
    configuration_error: Optional[str] = None
    connection: Optional[ConnectionStatus] = None


# ============ System Statistics ============

class SystemSummary(BaseModel):
    """System summary statistics."""
    users_online: int
    total_users: int
    active_connections: int
    nodes_online: int
    users_last_day: int
    users_last_week: int
    users_never_online: int
    total_user_traffic: int


class ServerInfo(BaseModel):
    """Server hardware info."""
    cpu_cores: int
    cpu_physical_cores: int
    memory_total: int
    memory_used: int
    memory_free: int
    memory_available: int
    uptime_seconds: int


class Bandwidth(BaseModel):
    """Realtime bandwidth statistics."""
    realtime_download: int
    realtime_upload: int
    realtime_total: int


class TrafficPeriod(BaseModel):
    """Traffic statistics for a period."""
    current: int
    previous: int
    difference: Optional[str] = None


class TrafficPeriods(BaseModel):
    """Traffic statistics for multiple periods."""
    last_2_days: TrafficPeriod
    last_7_days: TrafficPeriod
    last_30_days: TrafficPeriod
    current_month: TrafficPeriod
    current_year: TrafficPeriod


class SystemStatsResponse(BaseModel):
    """Full system statistics response."""
    system: SystemSummary
    users_by_status: Dict[str, int]
    server_info: ServerInfo
    bandwidth: Bandwidth
    traffic_periods: TrafficPeriods
    nodes_realtime: List[Dict[str, Any]] = Field(default_factory=list)
    nodes_weekly: List[Dict[str, Any]] = Field(default_factory=list)
    last_updated: Optional[datetime] = None


# ============ Nodes ============

class NodeInfo(BaseModel):
    """Node information."""
    uuid: str
    name: str
    address: str
    country_code: Optional[str] = None
    is_connected: bool
    is_disabled: bool
    is_node_online: bool
    is_xray_running: bool
    users_online: Optional[int] = None
    traffic_used_bytes: Optional[int] = None
    traffic_limit_bytes: Optional[int] = None
    last_status_change: Optional[datetime] = None
    last_status_message: Optional[str] = None
    xray_uptime: Optional[str] = None
    is_traffic_tracking_active: bool = False
    traffic_reset_day: Optional[int] = None
    notify_percent: Optional[int] = None
    consumption_multiplier: float = 1.0
    cpu_count: Optional[int] = None
    cpu_model: Optional[str] = None
    total_ram: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    provider_uuid: Optional[str] = None


class NodesListResponse(BaseModel):
    """List of nodes response."""
    items: List[NodeInfo]
    total: int


class NodesOverview(BaseModel):
    """Nodes overview statistics."""
    total: int
    online: int
    offline: int
    disabled: int
    total_users_online: int
    nodes: List[NodeInfo]


class NodeStatisticsResponse(BaseModel):
    """Node statistics with usage history."""
    node: NodeInfo
    realtime: Optional[Dict[str, Any]] = None
    usage_history: List[Dict[str, Any]] = Field(default_factory=list)
    last_updated: Optional[datetime] = None


class NodeUsageResponse(BaseModel):
    """Node usage history response."""
    items: List[Dict[str, Any]] = Field(default_factory=list)


class NodeActionRequest(BaseModel):
    """Request to perform node action."""
    action: Literal["enable", "disable", "restart"]


class NodeActionResponse(BaseModel):
    """Response after node action."""
    success: bool
    message: Optional[str] = None
    is_disabled: Optional[bool] = None


# ============ Squads (Internal Squads) ============

class SquadInfo(BaseModel):
    """Internal Squad information from RemnaWave."""
    uuid: str
    name: str
    members_count: int
    inbounds_count: int
    inbounds: List[Dict[str, Any]] = Field(default_factory=list)


class SquadWithLocalInfo(BaseModel):
    """Squad with local database info."""
    uuid: str
    name: str
    members_count: int
    inbounds_count: int
    inbounds: List[Dict[str, Any]] = Field(default_factory=list)
    # Local DB info
    local_id: Optional[int] = None
    display_name: Optional[str] = None
    country_code: Optional[str] = None
    is_available: Optional[bool] = None
    is_trial_eligible: Optional[bool] = None
    price_kopeks: Optional[int] = None
    max_users: Optional[int] = None
    current_users: Optional[int] = None
    is_synced: bool = False


class SquadsListResponse(BaseModel):
    """List of squads response."""
    items: List[SquadWithLocalInfo]
    total: int


class SquadDetailResponse(BaseModel):
    """Detailed squad response."""
    uuid: str
    name: str
    members_count: int
    inbounds_count: int
    inbounds: List[Dict[str, Any]] = Field(default_factory=list)
    # Local DB info if synced
    local_id: Optional[int] = None
    display_name: Optional[str] = None
    country_code: Optional[str] = None
    description: Optional[str] = None
    is_available: Optional[bool] = None
    is_trial_eligible: Optional[bool] = None
    price_kopeks: Optional[int] = None
    max_users: Optional[int] = None
    current_users: Optional[int] = None
    sort_order: Optional[int] = None
    is_synced: bool = False
    active_subscriptions: int = 0


class SquadCreateRequest(BaseModel):
    """Request to create a new squad."""
    name: str = Field(..., min_length=1, max_length=255)
    inbound_uuids: List[str] = Field(default_factory=list)


class SquadUpdateRequest(BaseModel):
    """Request to update a squad."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    inbound_uuids: Optional[List[str]] = None


class SquadActionRequest(BaseModel):
    """Request to perform squad action."""
    action: Literal["add_all_users", "remove_all_users", "delete", "rename", "update_inbounds"]
    name: Optional[str] = None
    inbound_uuids: Optional[List[str]] = None


class SquadOperationResponse(BaseModel):
    """Response after squad operation."""
    success: bool
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


# ============ Migration ============

class MigrationPreviewResponse(BaseModel):
    """Preview of squad migration."""
    squad_uuid: str
    squad_name: str
    current_users: int
    max_users: Optional[int] = None
    users_to_migrate: int


class MigrationRequest(BaseModel):
    """Request to migrate users between squads."""
    source_uuid: str
    target_uuid: str


class MigrationStats(BaseModel):
    """Migration statistics."""
    source_uuid: str
    target_uuid: str
    total: int = 0
    updated: int = 0
    panel_updated: int = 0
    panel_failed: int = 0
    source_removed: int = 0
    target_added: int = 0


class MigrationResponse(BaseModel):
    """Response after migration."""
    success: bool
    message: Optional[str] = None
    error: Optional[str] = None
    data: Optional[MigrationStats] = None


# ============ Inbounds ============

class InboundInfo(BaseModel):
    """Inbound information."""
    uuid: str
    tag: str
    type: Optional[str] = None
    network: Optional[str] = None
    security: Optional[str] = None


class InboundsListResponse(BaseModel):
    """List of inbounds response."""
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


# ============ Auto Sync ============

class AutoSyncTime(BaseModel):
    """Scheduled sync time."""
    hour: int
    minute: int


class AutoSyncStatus(BaseModel):
    """Auto sync status."""
    enabled: bool
    times: List[str] = Field(default_factory=list)  # HH:MM format
    next_run: Optional[datetime] = None
    is_running: bool = False
    last_run_started_at: Optional[datetime] = None
    last_run_finished_at: Optional[datetime] = None
    last_run_success: Optional[bool] = None
    last_run_reason: Optional[str] = None
    last_run_error: Optional[str] = None
    last_user_stats: Optional[Dict[str, Any]] = None
    last_server_stats: Optional[Dict[str, Any]] = None


class AutoSyncToggleRequest(BaseModel):
    """Request to toggle auto sync."""
    enabled: bool


class AutoSyncRunResponse(BaseModel):
    """Response after running sync."""
    started: bool
    success: Optional[bool] = None
    error: Optional[str] = None
    user_stats: Optional[Dict[str, Any]] = None
    server_stats: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None


# ============ Manual Sync ============

class SyncMode(BaseModel):
    """Sync mode options."""
    mode: Literal["all", "new_only", "update_only"] = "all"


class SyncResponse(BaseModel):
    """Response after sync operation."""
    success: bool
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class SyncRecommendations(BaseModel):
    """Sync recommendations."""
    success: bool
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
