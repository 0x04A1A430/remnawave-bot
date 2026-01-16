"""Schemas for server management in cabinet."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class PromoGroupInfo(BaseModel):
    """Promo group info for server."""
    id: int
    name: str
    is_selected: bool = False


class ServerListItem(BaseModel):
    """Server item for list view."""
    id: int
    squad_uuid: str
    display_name: str
    original_name: Optional[str] = None
    country_code: Optional[str] = None
    is_available: bool
    is_trial_eligible: bool
    price_kopeks: int
    price_rubles: float
    max_users: Optional[int] = None
    current_users: int
    sort_order: int
    is_full: bool
    availability_status: str
    created_at: datetime

    class Config:
        from_attributes = True


class ServerListResponse(BaseModel):
    """Response with list of servers."""
    servers: List[ServerListItem]
    total: int


class ServerDetailResponse(BaseModel):
    """Detailed server response."""
    id: int
    squad_uuid: str
    display_name: str
    original_name: Optional[str] = None
    country_code: Optional[str] = None
    description: Optional[str] = None
    is_available: bool
    is_trial_eligible: bool
    price_kopeks: int
    price_rubles: float
    max_users: Optional[int] = None
    current_users: int
    sort_order: int
    is_full: bool
    availability_status: str
    promo_groups: List[PromoGroupInfo]
    active_subscriptions: int
    tariffs_using: List[str]  # Names of tariffs using this server
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ServerUpdateRequest(BaseModel):
    """Request to update a server."""
    display_name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    country_code: Optional[str] = Field(None, max_length=5)
    is_available: Optional[bool] = None
    is_trial_eligible: Optional[bool] = None
    price_kopeks: Optional[int] = Field(None, ge=0)
    max_users: Optional[int] = Field(None, ge=0)
    sort_order: Optional[int] = Field(None, ge=0)
    promo_group_ids: Optional[List[int]] = None


class ServerToggleResponse(BaseModel):
    """Response after toggling server."""
    id: int
    is_available: bool
    message: str


class ServerTrialToggleResponse(BaseModel):
    """Response after toggling trial eligibility."""
    id: int
    is_trial_eligible: bool
    message: str


class ServerStatsResponse(BaseModel):
    """Server statistics."""
    id: int
    display_name: str
    squad_uuid: str
    current_users: int
    max_users: Optional[int]
    active_subscriptions: int
    trial_subscriptions: int
    usage_percent: Optional[float] = None


class ServerSyncResponse(BaseModel):
    """Response after syncing with RemnaWave."""
    created: int
    updated: int
    removed: int
    message: str


class ServerSyncRequest(BaseModel):
    """Request to sync servers."""
    force: bool = False  # Force sync even if recently synced
