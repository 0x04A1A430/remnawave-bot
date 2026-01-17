"""Schemas for advertising campaigns management in cabinet."""

from datetime import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


CampaignBonusType = Literal["balance", "subscription", "none", "tariff"]


class TariffInfo(BaseModel):
    """Tariff info for campaign."""
    id: int
    name: str


class CampaignListItem(BaseModel):
    """Campaign item for list view."""
    id: int
    name: str
    start_parameter: str
    bonus_type: CampaignBonusType
    is_active: bool
    registrations_count: int
    total_revenue_kopeks: int = 0
    conversion_rate: float = 0.0
    created_at: datetime

    class Config:
        from_attributes = True


class CampaignListResponse(BaseModel):
    """Response with list of campaigns."""
    campaigns: List[CampaignListItem]
    total: int


class CampaignDetailResponse(BaseModel):
    """Detailed campaign response."""
    id: int
    name: str
    start_parameter: str
    bonus_type: CampaignBonusType
    is_active: bool
    # Balance bonus
    balance_bonus_kopeks: int = 0
    balance_bonus_rubles: float = 0.0
    # Subscription bonus
    subscription_duration_days: Optional[int] = None
    subscription_traffic_gb: Optional[int] = None
    subscription_device_limit: Optional[int] = None
    subscription_squads: List[str] = Field(default_factory=list)
    # Tariff bonus
    tariff_id: Optional[int] = None
    tariff_duration_days: Optional[int] = None
    tariff: Optional[TariffInfo] = None
    # Meta
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Deep link
    deep_link: Optional[str] = None

    class Config:
        from_attributes = True


class CampaignCreateRequest(BaseModel):
    """Request to create a campaign."""
    name: str = Field(..., min_length=1, max_length=255)
    start_parameter: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    bonus_type: CampaignBonusType
    is_active: bool = True
    # Balance bonus
    balance_bonus_kopeks: int = Field(0, ge=0)
    # Subscription bonus
    subscription_duration_days: Optional[int] = Field(None, ge=1)
    subscription_traffic_gb: Optional[int] = Field(None, ge=0)
    subscription_device_limit: Optional[int] = Field(None, ge=1)
    subscription_squads: List[str] = Field(default_factory=list)
    # Tariff bonus
    tariff_id: Optional[int] = None
    tariff_duration_days: Optional[int] = Field(None, ge=1)


class CampaignUpdateRequest(BaseModel):
    """Request to update a campaign."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    start_parameter: Optional[str] = Field(None, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    bonus_type: Optional[CampaignBonusType] = None
    is_active: Optional[bool] = None
    # Balance bonus
    balance_bonus_kopeks: Optional[int] = Field(None, ge=0)
    # Subscription bonus
    subscription_duration_days: Optional[int] = Field(None, ge=1)
    subscription_traffic_gb: Optional[int] = Field(None, ge=0)
    subscription_device_limit: Optional[int] = Field(None, ge=1)
    subscription_squads: Optional[List[str]] = None
    # Tariff bonus
    tariff_id: Optional[int] = None
    tariff_duration_days: Optional[int] = Field(None, ge=1)


class CampaignToggleResponse(BaseModel):
    """Response after toggling campaign."""
    id: int
    is_active: bool
    message: str


class CampaignStatisticsResponse(BaseModel):
    """Detailed campaign statistics."""
    id: int
    name: str
    start_parameter: str
    bonus_type: CampaignBonusType
    is_active: bool
    # Registration stats
    registrations: int = 0
    balance_issued_kopeks: int = 0
    balance_issued_rubles: float = 0.0
    subscription_issued: int = 0
    last_registration: Optional[datetime] = None
    # Revenue stats
    total_revenue_kopeks: int = 0
    total_revenue_rubles: float = 0.0
    avg_revenue_per_user_kopeks: int = 0
    avg_revenue_per_user_rubles: float = 0.0
    avg_first_payment_kopeks: int = 0
    avg_first_payment_rubles: float = 0.0
    # Trial & Conversion stats
    trial_users_count: int = 0
    active_trials_count: int = 0
    conversion_count: int = 0
    paid_users_count: int = 0
    conversion_rate: float = 0.0
    trial_conversion_rate: float = 0.0
    # Deep link
    deep_link: Optional[str] = None


class CampaignRegistrationItem(BaseModel):
    """Campaign registration item."""
    id: int
    user_id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    bonus_type: str
    balance_bonus_kopeks: int = 0
    subscription_duration_days: Optional[int] = None
    tariff_id: Optional[int] = None
    tariff_duration_days: Optional[int] = None
    created_at: datetime
    # User stats
    user_balance_kopeks: int = 0
    has_subscription: bool = False
    has_paid: bool = False

    class Config:
        from_attributes = True


class CampaignRegistrationsResponse(BaseModel):
    """Response with campaign registrations."""
    registrations: List[CampaignRegistrationItem]
    total: int
    page: int
    per_page: int


class CampaignsOverviewResponse(BaseModel):
    """Overview of all campaigns."""
    total: int
    active: int
    inactive: int
    total_registrations: int
    total_balance_issued_kopeks: int
    total_balance_issued_rubles: float
    total_subscription_issued: int
    total_tariff_issued: int = 0


class ServerSquadInfo(BaseModel):
    """Server squad info for campaign selection."""
    id: int
    squad_uuid: str
    display_name: str
    country_code: Optional[str] = None
