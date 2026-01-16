"""Subscription schemas for cabinet."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class ServerInfo(BaseModel):
    """Server info for display."""
    uuid: str
    name: str
    country_code: Optional[str] = None


class TrafficPurchaseInfo(BaseModel):
    """Purchased traffic package info."""
    id: int
    traffic_gb: int
    expires_at: datetime
    created_at: datetime
    days_remaining: int
    progress_percent: float


class SubscriptionResponse(BaseModel):
    """User subscription data."""
    id: int
    status: str
    is_trial: bool
    start_date: datetime
    end_date: datetime
    days_left: int
    hours_left: int = 0
    minutes_left: int = 0
    time_left_display: str = ""  # Human readable format like "2д 5ч" or "5ч 30м"
    traffic_limit_gb: int
    traffic_used_gb: float
    traffic_used_percent: float
    device_limit: int
    connected_squads: List[str] = []
    servers: List[ServerInfo] = []  # Server display info
    autopay_enabled: bool
    autopay_days_before: int
    subscription_url: Optional[str] = None
    is_active: bool
    is_expired: bool
    traffic_purchases: List[TrafficPurchaseInfo] = []
    # Daily tariff fields
    is_daily: bool = False
    is_daily_paused: bool = False
    daily_price_kopeks: Optional[int] = None
    next_daily_charge_at: Optional[datetime] = None  # When next daily charge will happen
    tariff_id: Optional[int] = None
    tariff_name: Optional[str] = None

    class Config:
        from_attributes = True


class RenewalOptionResponse(BaseModel):
    """Available subscription renewal option."""
    period_days: int
    price_kopeks: int
    price_rubles: float
    discount_percent: int = 0
    original_price_kopeks: Optional[int] = None


class RenewalRequest(BaseModel):
    """Request to renew subscription."""
    period_days: int = Field(..., description="Renewal period in days")


class TrafficPackageResponse(BaseModel):
    """Available traffic package."""
    gb: int
    price_kopeks: int
    price_rubles: float
    is_unlimited: bool = False


class TrafficPurchaseRequest(BaseModel):
    """Request to purchase additional traffic."""
    gb: int = Field(..., ge=0, description="GB to purchase (0 = unlimited)")


class DevicePurchaseRequest(BaseModel):
    """Request to purchase additional device slots."""
    devices: int = Field(..., ge=1, description="Number of additional devices")


class AutopayUpdateRequest(BaseModel):
    """Request to update autopay settings."""
    enabled: bool
    days_before: Optional[int] = Field(None, ge=1, le=30, description="Days before expiration to charge")


class TrialInfoResponse(BaseModel):
    """Trial subscription info."""
    is_available: bool
    duration_days: int
    traffic_limit_gb: int
    device_limit: int
    requires_payment: bool = False
    price_kopeks: int = 0
    price_rubles: float = 0.0
    reason_unavailable: Optional[str] = None


# ============ Purchase Options Schemas ============

class PurchaseSelectionRequest(BaseModel):
    """User's selection for subscription purchase."""
    period_id: Optional[str] = Field(None, description="Period ID like 'days:30'")
    period_days: Optional[int] = Field(None, description="Period in days")
    traffic_value: Optional[int] = Field(None, description="Traffic in GB (0 = unlimited)")
    servers: Optional[List[str]] = Field(default_factory=list, description="Server UUIDs")
    devices: Optional[int] = Field(None, description="Device limit")


class PurchasePreviewRequest(BaseModel):
    """Request to preview purchase pricing."""
    selection: PurchaseSelectionRequest


# ============ Tariff Purchase Schemas ============

class TariffPurchaseRequest(BaseModel):
    """Request to purchase a tariff."""
    tariff_id: int = Field(..., description="Tariff ID to purchase")
    period_days: int = Field(..., description="Period in days")
    traffic_gb: Optional[int] = Field(None, ge=0, description="Custom traffic in GB (for custom_traffic_enabled tariffs)")
