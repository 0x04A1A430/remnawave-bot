"""Schemas for tariff management in cabinet."""

from datetime import datetime
from typing import List, Optional, Dict
from pydantic import BaseModel, Field


class PeriodPrice(BaseModel):
    """Price for a specific period."""
    days: int = Field(..., ge=1, description="Period in days")
    price_kopeks: int = Field(..., ge=0, description="Price in kopeks")
    price_rubles: Optional[float] = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.price_rubles is None:
            self.price_rubles = self.price_kopeks / 100


class ServerTrafficLimit(BaseModel):
    """Traffic limit for a specific server."""
    traffic_limit_gb: int = Field(0, ge=0, description="0 = use default tariff limit")


class ServerInfo(BaseModel):
    """Server info for tariff."""
    id: int
    squad_uuid: str
    display_name: str
    country_code: Optional[str] = None
    is_selected: bool = False
    traffic_limit_gb: Optional[int] = None  # Индивидуальный лимит для сервера


class PromoGroupInfo(BaseModel):
    """Promo group info for tariff."""
    id: int
    name: str
    is_selected: bool = False


class TariffListItem(BaseModel):
    """Tariff item for list view."""
    id: int
    name: str
    description: Optional[str] = None
    is_active: bool
    is_trial_available: bool
    is_daily: bool = False
    daily_price_kopeks: int = 0
    allow_traffic_topup: bool = True
    traffic_limit_gb: int
    device_limit: int
    tier_level: int
    display_order: int
    servers_count: int
    subscriptions_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class TariffListResponse(BaseModel):
    """Response with list of tariffs."""
    tariffs: List[TariffListItem]
    total: int


class TariffDetailResponse(BaseModel):
    """Detailed tariff response."""
    id: int
    name: str
    description: Optional[str] = None
    is_active: bool
    is_trial_available: bool
    allow_traffic_topup: bool = True
    traffic_topup_enabled: bool = False
    traffic_topup_packages: Dict[str, int] = Field(default_factory=dict)
    max_topup_traffic_gb: int = 0
    traffic_limit_gb: int
    device_limit: int
    device_price_kopeks: Optional[int] = None
    max_device_limit: Optional[int] = None
    tier_level: int
    display_order: int
    period_prices: List[PeriodPrice]
    allowed_squads: List[str]  # UUIDs
    server_traffic_limits: Dict[str, ServerTrafficLimit] = Field(default_factory=dict)  # {uuid: {traffic_limit_gb}}
    servers: List[ServerInfo]
    promo_groups: List[PromoGroupInfo]
    subscriptions_count: int
    # Произвольное количество дней
    custom_days_enabled: bool = False
    price_per_day_kopeks: int = 0
    min_days: int = 1
    max_days: int = 365
    # Произвольный трафик при покупке
    custom_traffic_enabled: bool = False
    traffic_price_per_gb_kopeks: int = 0
    min_traffic_gb: int = 1
    max_traffic_gb: int = 1000
    # Дневной тариф
    is_daily: bool = False
    daily_price_kopeks: int = 0
    # Режим сброса трафика
    traffic_reset_mode: Optional[str] = None  # DAY, WEEK, MONTH, NO_RESET, None = глобальная настройка
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TariffCreateRequest(BaseModel):
    """Request to create a tariff."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    is_active: bool = True
    allow_traffic_topup: bool = True
    traffic_topup_enabled: bool = False
    traffic_topup_packages: Dict[str, int] = Field(default_factory=dict)
    max_topup_traffic_gb: int = Field(0, ge=0)
    traffic_limit_gb: int = Field(0, ge=0, description="0 = unlimited")
    device_limit: int = Field(1, ge=1)
    device_price_kopeks: Optional[int] = Field(None, ge=0)
    max_device_limit: Optional[int] = Field(None, ge=1)
    tier_level: int = Field(1, ge=1, le=10)
    period_prices: List[PeriodPrice] = Field(default_factory=list)
    allowed_squads: List[str] = Field(default_factory=list, description="Server UUIDs")
    server_traffic_limits: Dict[str, ServerTrafficLimit] = Field(default_factory=dict, description="Per-server traffic limits")
    promo_group_ids: List[int] = Field(default_factory=list)
    # Произвольное количество дней
    custom_days_enabled: bool = False
    price_per_day_kopeks: int = Field(0, ge=0)
    min_days: int = Field(1, ge=1)
    max_days: int = Field(365, ge=1)
    # Произвольный трафик при покупке
    custom_traffic_enabled: bool = False
    traffic_price_per_gb_kopeks: int = Field(0, ge=0)
    min_traffic_gb: int = Field(1, ge=1)
    max_traffic_gb: int = Field(1000, ge=1)
    # Дневной тариф
    is_daily: bool = False
    daily_price_kopeks: int = Field(0, ge=0)
    # Режим сброса трафика
    traffic_reset_mode: Optional[str] = None  # DAY, WEEK, MONTH, NO_RESET, None = глобальная настройка


class TariffUpdateRequest(BaseModel):
    """Request to update a tariff."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    is_active: Optional[bool] = None
    allow_traffic_topup: Optional[bool] = None
    traffic_topup_enabled: Optional[bool] = None
    traffic_topup_packages: Optional[Dict[str, int]] = None
    max_topup_traffic_gb: Optional[int] = Field(None, ge=0)
    traffic_limit_gb: Optional[int] = Field(None, ge=0)
    device_limit: Optional[int] = Field(None, ge=1)
    device_price_kopeks: Optional[int] = Field(None, ge=0)
    max_device_limit: Optional[int] = Field(None, ge=1)
    tier_level: Optional[int] = Field(None, ge=1, le=10)
    display_order: Optional[int] = Field(None, ge=0)
    period_prices: Optional[List[PeriodPrice]] = None
    allowed_squads: Optional[List[str]] = None
    server_traffic_limits: Optional[Dict[str, ServerTrafficLimit]] = None
    promo_group_ids: Optional[List[int]] = None
    # Произвольное количество дней
    custom_days_enabled: Optional[bool] = None
    price_per_day_kopeks: Optional[int] = Field(None, ge=0)
    min_days: Optional[int] = Field(None, ge=1)
    max_days: Optional[int] = Field(None, ge=1)
    # Произвольный трафик при покупке
    custom_traffic_enabled: Optional[bool] = None
    traffic_price_per_gb_kopeks: Optional[int] = Field(None, ge=0)
    min_traffic_gb: Optional[int] = Field(None, ge=1)
    max_traffic_gb: Optional[int] = Field(None, ge=1)
    # Дневной тариф
    is_daily: Optional[bool] = None
    daily_price_kopeks: Optional[int] = Field(None, ge=0)
    # Режим сброса трафика
    traffic_reset_mode: Optional[str] = None  # DAY, WEEK, MONTH, NO_RESET, None = глобальная настройка


class TariffToggleResponse(BaseModel):
    """Response after toggling tariff."""
    id: int
    is_active: bool
    message: str


class TariffTrialResponse(BaseModel):
    """Response after setting trial tariff."""
    id: int
    is_trial_available: bool
    message: str


class TariffStatsResponse(BaseModel):
    """Tariff statistics."""
    id: int
    name: str
    subscriptions_count: int
    active_subscriptions: int
    trial_subscriptions: int
    revenue_kopeks: int
    revenue_rubles: float
