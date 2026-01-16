"""Схемы для колеса удачи (Fortune Wheel)."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from enum import Enum


# ==================== ENUMS ====================


class WheelPaymentType(str, Enum):
    """Способы оплаты спина."""
    TELEGRAM_STARS = "telegram_stars"
    SUBSCRIPTION_DAYS = "subscription_days"


class WheelPrizeType(str, Enum):
    """Типы призов."""
    SUBSCRIPTION_DAYS = "subscription_days"
    BALANCE_BONUS = "balance_bonus"
    TRAFFIC_GB = "traffic_gb"
    PROMOCODE = "promocode"
    NOTHING = "nothing"


# ==================== USER SCHEMAS ====================


class WheelPrizeDisplay(BaseModel):
    """Отображение приза для пользователя."""
    id: int
    display_name: str
    emoji: str
    color: str
    prize_type: str

    class Config:
        from_attributes = True


class WheelConfigResponse(BaseModel):
    """Конфигурация колеса для пользователя."""
    is_enabled: bool
    name: str
    spin_cost_stars: Optional[int] = None
    spin_cost_days: Optional[int] = None
    spin_cost_stars_enabled: bool
    spin_cost_days_enabled: bool
    prizes: List[WheelPrizeDisplay]
    daily_limit: int
    user_spins_today: int
    can_spin: bool
    can_spin_reason: Optional[str] = None
    can_pay_stars: bool = False
    can_pay_days: bool = False
    user_balance_kopeks: int = 0
    required_balance_kopeks: int = 0


class SpinAvailabilityResponse(BaseModel):
    """Доступность спина."""
    can_spin: bool
    reason: Optional[str] = None
    spins_remaining_today: int
    can_pay_stars: bool
    can_pay_days: bool
    min_subscription_days: int
    user_subscription_days: int
    user_balance_kopeks: int = 0
    required_balance_kopeks: int = 0


class SpinRequest(BaseModel):
    """Запрос на спин."""
    payment_type: WheelPaymentType


class SpinResultResponse(BaseModel):
    """Результат спина."""
    success: bool
    prize_id: Optional[int] = None
    prize_type: Optional[str] = None
    prize_value: int = 0
    prize_display_name: str = ""
    emoji: str = "🎁"
    color: str = "#3B82F6"
    rotation_degrees: float = 0.0
    message: str = ""
    promocode: Optional[str] = None
    error: Optional[str] = None


class SpinHistoryItem(BaseModel):
    """Элемент истории спинов."""
    id: int
    payment_type: str
    payment_amount: int
    prize_type: str
    prize_value: int
    prize_display_name: str
    emoji: str = "🎁"
    color: str = "#3B82F6"
    prize_value_kopeks: int
    created_at: datetime

    class Config:
        from_attributes = True


class SpinHistoryResponse(BaseModel):
    """История спинов с пагинацией."""
    items: List[SpinHistoryItem]
    total: int
    page: int
    per_page: int
    pages: int


# ==================== ADMIN SCHEMAS ====================


class WheelPrizeAdminResponse(BaseModel):
    """Полная информация о призе для админа."""
    id: int
    config_id: int
    prize_type: str
    prize_value: int
    display_name: str
    emoji: str
    color: str
    prize_value_kopeks: int
    sort_order: int
    manual_probability: Optional[float] = None
    is_active: bool
    promo_balance_bonus_kopeks: int = 0
    promo_subscription_days: int = 0
    promo_traffic_gb: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdminWheelConfigResponse(BaseModel):
    """Полная конфигурация колеса для админа."""
    id: int
    is_enabled: bool
    name: str
    spin_cost_stars: int
    spin_cost_days: int
    spin_cost_stars_enabled: bool
    spin_cost_days_enabled: bool
    rtp_percent: int
    daily_spin_limit: int
    min_subscription_days_for_day_payment: int
    promo_prefix: str
    promo_validity_days: int
    prizes: List[WheelPrizeAdminResponse]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UpdateWheelConfigRequest(BaseModel):
    """Запрос на обновление конфига колеса."""
    is_enabled: Optional[bool] = None
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    spin_cost_stars: Optional[int] = Field(None, ge=1, le=1000)
    spin_cost_days: Optional[int] = Field(None, ge=1, le=30)
    spin_cost_stars_enabled: Optional[bool] = None
    spin_cost_days_enabled: Optional[bool] = None
    rtp_percent: Optional[int] = Field(None, ge=0, le=100)
    daily_spin_limit: Optional[int] = Field(None, ge=0, le=100)
    min_subscription_days_for_day_payment: Optional[int] = Field(None, ge=1, le=30)
    promo_prefix: Optional[str] = Field(None, min_length=1, max_length=20)
    promo_validity_days: Optional[int] = Field(None, ge=1, le=365)


class CreatePrizeRequest(BaseModel):
    """Запрос на создание приза."""
    prize_type: WheelPrizeType
    prize_value: int = Field(..., ge=0)
    display_name: str = Field(..., min_length=1, max_length=100)
    emoji: str = Field(default="🎁", max_length=10)
    color: str = Field(default="#3B82F6", pattern=r'^#[0-9A-Fa-f]{6}$')
    prize_value_kopeks: int = Field(..., ge=0)
    sort_order: int = Field(default=0, ge=0)
    manual_probability: Optional[float] = Field(None, ge=0, le=1)
    is_active: bool = True
    promo_balance_bonus_kopeks: int = Field(default=0, ge=0)
    promo_subscription_days: int = Field(default=0, ge=0)
    promo_traffic_gb: int = Field(default=0, ge=0)


class UpdatePrizeRequest(BaseModel):
    """Запрос на обновление приза."""
    prize_type: Optional[WheelPrizeType] = None
    prize_value: Optional[int] = Field(None, ge=0)
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    emoji: Optional[str] = Field(None, max_length=10)
    color: Optional[str] = Field(None, pattern=r'^#[0-9A-Fa-f]{6}$')
    prize_value_kopeks: Optional[int] = Field(None, ge=0)
    sort_order: Optional[int] = Field(None, ge=0)
    manual_probability: Optional[float] = Field(None, ge=0, le=1)
    is_active: Optional[bool] = None
    promo_balance_bonus_kopeks: Optional[int] = Field(None, ge=0)
    promo_subscription_days: Optional[int] = Field(None, ge=0)
    promo_traffic_gb: Optional[int] = Field(None, ge=0)


class ReorderPrizesRequest(BaseModel):
    """Запрос на переупорядочивание призов."""
    prize_ids: List[int]


class AdminSpinItem(BaseModel):
    """Спин для админки."""
    id: int
    user_id: int
    username: Optional[str] = None
    payment_type: str
    payment_amount: int
    payment_value_kopeks: int
    prize_type: str
    prize_value: int
    prize_display_name: str
    prize_value_kopeks: int
    is_applied: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AdminSpinsResponse(BaseModel):
    """Список спинов для админки с пагинацией."""
    items: List[AdminSpinItem]
    total: int
    page: int
    per_page: int
    pages: int


class WheelStatisticsResponse(BaseModel):
    """Статистика колеса."""
    total_spins: int
    total_revenue_kopeks: int
    total_payout_kopeks: int
    actual_rtp_percent: float
    configured_rtp_percent: int
    spins_by_payment_type: dict
    prizes_distribution: List[dict]
    top_wins: List[dict]
    period_from: Optional[str] = None
    period_to: Optional[str] = None
