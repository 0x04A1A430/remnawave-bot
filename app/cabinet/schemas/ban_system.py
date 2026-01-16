"""Schemas for Ban System integration in cabinet."""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# === Status ===

class BanSystemStatusResponse(BaseModel):
    """Ban System integration status."""
    enabled: bool
    configured: bool


# === Stats ===

class BanSystemStatsResponse(BaseModel):
    """Overall Ban System statistics."""
    total_users: int = 0
    active_users: int = 0
    users_over_limit: int = 0
    total_requests: int = 0
    total_punishments: int = 0
    active_punishments: int = 0
    nodes_online: int = 0
    nodes_total: int = 0
    agents_online: int = 0
    agents_total: int = 0
    panel_connected: bool = False
    uptime_seconds: Optional[int] = None


# === Users ===

class BanUserIPInfo(BaseModel):
    """User IP address information."""
    ip: str
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    node: Optional[str] = None
    request_count: int = 0
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    city: Optional[str] = None


class BanUserRequestLog(BaseModel):
    """User request log entry."""
    timestamp: datetime
    source_ip: str
    destination: Optional[str] = None
    dest_port: Optional[int] = None
    protocol: Optional[str] = None
    action: Optional[str] = None
    node: Optional[str] = None


class BanUserListItem(BaseModel):
    """User in the list."""
    email: str
    unique_ip_count: int = 0
    total_requests: int = 0
    limit: Optional[int] = None
    is_over_limit: bool = False
    blocked_count: int = 0
    last_seen: Optional[datetime] = None


class BanUsersListResponse(BaseModel):
    """Paginated list of users."""
    users: List[BanUserListItem] = []
    total: int = 0
    offset: int = 0
    limit: int = 50


class BanUserDetailResponse(BaseModel):
    """Detailed user information."""
    email: str
    unique_ip_count: int = 0
    total_requests: int = 0
    limit: Optional[int] = None
    is_over_limit: bool = False
    blocked_count: int = 0
    ips: List[BanUserIPInfo] = []
    recent_requests: List[BanUserRequestLog] = []
    network_type: Optional[str] = None  # wifi, mobile, mixed


# === Punishments (Bans) ===

class BanPunishmentItem(BaseModel):
    """Punishment/ban entry."""
    id: Optional[int] = None
    user_id: str
    uuid: Optional[str] = None
    username: str
    reason: Optional[str] = None
    punished_at: datetime
    enable_at: Optional[datetime] = None
    ip_count: int = 0
    limit: int = 0
    enabled: bool = False
    enabled_at: Optional[datetime] = None
    node_name: Optional[str] = None


class BanPunishmentsListResponse(BaseModel):
    """List of active punishments."""
    punishments: List[BanPunishmentItem] = []
    total: int = 0


class BanHistoryResponse(BaseModel):
    """Punishment history."""
    items: List[BanPunishmentItem] = []
    total: int = 0


class BanUserRequest(BaseModel):
    """Request to ban a user."""
    username: str = Field(..., min_length=1)
    minutes: int = Field(default=30, ge=1)
    reason: Optional[str] = Field(None, max_length=500)


class UnbanResponse(BaseModel):
    """Unban response."""
    success: bool
    message: str


# === Nodes ===

class BanNodeItem(BaseModel):
    """Node information."""
    name: str
    address: Optional[str] = None
    is_connected: bool = False
    last_seen: Optional[datetime] = None
    users_count: int = 0
    agent_stats: Optional[Dict[str, Any]] = None


class BanNodesListResponse(BaseModel):
    """List of nodes."""
    nodes: List[BanNodeItem] = []
    total: int = 0
    online: int = 0


# === Agents ===

class BanAgentItem(BaseModel):
    """Monitoring agent information."""
    node_name: str
    sent_total: int = 0
    dropped_total: int = 0
    batches_total: int = 0
    reconnects: int = 0
    failures: int = 0
    queue_size: int = 0
    queue_max: int = 0
    dedup_checked: int = 0
    dedup_skipped: int = 0
    filter_checked: int = 0
    filter_filtered: int = 0
    health: str = "unknown"  # healthy, warning, critical
    is_online: bool = False
    last_report: Optional[datetime] = None


class BanAgentsSummary(BaseModel):
    """Agents summary statistics."""
    total_agents: int = 0
    online_agents: int = 0
    total_sent: int = 0
    total_dropped: int = 0
    avg_queue_size: float = 0.0
    healthy_count: int = 0
    warning_count: int = 0
    critical_count: int = 0


class BanAgentsListResponse(BaseModel):
    """List of agents."""
    agents: List[BanAgentItem] = []
    summary: Optional[BanAgentsSummary] = None
    total: int = 0
    online: int = 0


# === Traffic ===

class BanTrafficStats(BaseModel):
    """Traffic statistics."""
    total_bytes: int = 0
    upload_bytes: int = 0
    download_bytes: int = 0
    total_users: int = 0
    violators_count: int = 0


class BanTrafficUserItem(BaseModel):
    """User traffic information."""
    username: str
    email: Optional[str] = None
    total_bytes: int = 0
    upload_bytes: int = 0
    download_bytes: int = 0
    limit_bytes: Optional[int] = None
    is_over_limit: bool = False


class BanTrafficViolationItem(BaseModel):
    """Traffic limit violation entry."""
    id: Optional[int] = None
    username: str
    email: Optional[str] = None
    violation_type: str
    description: Optional[str] = None
    bytes_used: int = 0
    bytes_limit: int = 0
    detected_at: datetime
    resolved: bool = False


class BanTrafficViolationsResponse(BaseModel):
    """List of traffic violations."""
    violations: List[BanTrafficViolationItem] = []
    total: int = 0


class BanTrafficTopItem(BaseModel):
    """Top user by traffic."""
    username: str
    bytes_total: int = 0
    bytes_limit: Optional[int] = None
    over_limit: bool = False


class BanTrafficResponse(BaseModel):
    """Full traffic statistics response."""
    enabled: bool = False
    stats: Optional[Dict[str, Any]] = None
    top_users: List[BanTrafficTopItem] = []
    recent_violations: List[BanTrafficViolationItem] = []


# === Settings ===

class BanSettingDefinition(BaseModel):
    """Setting definition with value."""
    key: str
    value: Any
    type: str  # bool, int, str, list
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    editable: bool = True
    description: Optional[str] = None
    category: Optional[str] = None


class BanSettingsResponse(BaseModel):
    """All settings response."""
    settings: List[BanSettingDefinition] = []


class BanSettingUpdateRequest(BaseModel):
    """Request to update a setting."""
    value: Any


class BanWhitelistRequest(BaseModel):
    """Request to add/remove from whitelist."""
    username: str = Field(..., min_length=1)


# === Reports ===

class BanReportTopViolator(BaseModel):
    """Top violator in report."""
    username: str
    count: int = 0


class BanReportResponse(BaseModel):
    """Period report response."""
    period_hours: int = 24
    current_users: int = 0
    current_ips: int = 0
    punishment_stats: Optional[Dict[str, Any]] = None
    top_violators: List[BanReportTopViolator] = []


# === Health ===

class BanHealthComponent(BaseModel):
    """Health component status."""
    name: str
    status: str  # healthy, degraded, unhealthy
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class BanHealthResponse(BaseModel):
    """Health status response."""
    status: str  # healthy, degraded, unhealthy
    uptime: Optional[int] = None
    components: List[BanHealthComponent] = []


class BanHealthDetailedResponse(BaseModel):
    """Detailed health response."""
    status: str
    uptime: Optional[int] = None
    components: Dict[str, Any] = {}


# === Agent History ===

class BanAgentHistoryItem(BaseModel):
    """Agent history item."""
    timestamp: datetime
    sent_total: int = 0
    dropped_total: int = 0
    queue_size: int = 0
    batches_total: int = 0


class BanAgentHistoryResponse(BaseModel):
    """Agent history response."""
    node: str
    hours: int = 24
    records: int = 0
    delta: Optional[Dict[str, Any]] = None
    first: Optional[Dict[str, Any]] = None
    last: Optional[Dict[str, Any]] = None
    history: List[BanAgentHistoryItem] = []
