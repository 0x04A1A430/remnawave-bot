"""Schemas for Admin Users management in cabinet."""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


class UserStatusEnum(str, Enum):
    """User status enum."""
    ACTIVE = "active"
    BLOCKED = "blocked"
    DELETED = "deleted"


class SubscriptionStatusEnum(str, Enum):
    """Subscription status enum."""
    TRIAL = "trial"
    ACTIVE = "active"
    EXPIRED = "expired"
    DISABLED = "disabled"
    PENDING = "pending"


class SortByEnum(str, Enum):
    """Sort options for users list."""
    CREATED_AT = "created_at"
    BALANCE = "balance"
    TRAFFIC = "traffic"
    LAST_ACTIVITY = "last_activity"
    TOTAL_SPENT = "total_spent"
    PURCHASE_COUNT = "purchase_count"


# === User Subscription Info ===

class UserSubscriptionInfo(BaseModel):
    """User subscription information."""
    id: int
    status: str
    is_trial: bool
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    traffic_limit_gb: int = 0
    traffic_used_gb: float = 0.0
    device_limit: int = 1
    tariff_id: Optional[int] = None
    tariff_name: Optional[str] = None
    autopay_enabled: bool = False
    is_active: bool = False
    days_remaining: int = 0


class UserPromoGroupInfo(BaseModel):
    """User promo group info."""
    id: int
    name: str
    is_default: bool = False


# === User List ===

class UserListItem(BaseModel):
    """User item in list."""
    id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: str
    status: str
    balance_kopeks: int
    balance_rubles: float
    created_at: datetime
    last_activity: Optional[datetime] = None

    # Subscription summary
    has_subscription: bool = False
    subscription_status: Optional[str] = None
    subscription_is_trial: bool = False
    subscription_end_date: Optional[datetime] = None

    # Promo group
    promo_group_id: Optional[int] = None
    promo_group_name: Optional[str] = None

    # Stats
    total_spent_kopeks: int = 0
    purchase_count: int = 0

    # Restrictions
    has_restrictions: bool = False
    restriction_topup: bool = False
    restriction_subscription: bool = False


class UsersListResponse(BaseModel):
    """Paginated list of users."""
    users: List[UserListItem]
    total: int
    offset: int = 0
    limit: int = 50


# === User Detail ===

class UserTransactionItem(BaseModel):
    """User transaction."""
    id: int
    type: str
    amount_kopeks: int
    amount_rubles: float
    description: Optional[str] = None
    payment_method: Optional[str] = None
    is_completed: bool = True
    created_at: datetime


class UserReferralInfo(BaseModel):
    """User referral info."""
    referral_code: str
    referrals_count: int = 0
    total_earnings_kopeks: int = 0
    commission_percent: Optional[int] = None
    referred_by_id: Optional[int] = None
    referred_by_username: Optional[str] = None


class UserDetailResponse(BaseModel):
    """Detailed user information."""
    id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: str
    status: str
    language: str
    balance_kopeks: int
    balance_rubles: float

    # Email (cabinet)
    email: Optional[str] = None
    email_verified: bool = False

    # Dates
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    cabinet_last_login: Optional[datetime] = None

    # Subscription
    subscription: Optional[UserSubscriptionInfo] = None

    # Promo group
    promo_group: Optional[UserPromoGroupInfo] = None

    # Referral
    referral: UserReferralInfo

    # Stats
    total_spent_kopeks: int = 0
    purchase_count: int = 0
    used_promocodes: int = 0
    has_had_paid_subscription: bool = False
    lifetime_used_traffic_bytes: int = 0

    # Restrictions
    restriction_topup: bool = False
    restriction_subscription: bool = False
    restriction_reason: Optional[str] = None

    # Promo offer
    promo_offer_discount_percent: int = 0
    promo_offer_discount_source: Optional[str] = None
    promo_offer_discount_expires_at: Optional[datetime] = None

    # Recent transactions
    recent_transactions: List[UserTransactionItem] = []


# === User Actions ===

class UpdateBalanceRequest(BaseModel):
    """Request to update user balance."""
    amount_kopeks: int = Field(..., description="Amount in kopeks (positive to add, negative to subtract)")
    description: str = Field(default="Admin balance adjustment", max_length=500)
    create_transaction: bool = Field(default=True, description="Create transaction record")


class UpdateBalanceResponse(BaseModel):
    """Response after balance update."""
    success: bool
    old_balance_kopeks: int
    new_balance_kopeks: int
    message: str


class UpdateSubscriptionRequest(BaseModel):
    """Request to update user subscription."""
    action: str = Field(..., description="Action: extend, set_end_date, change_tariff, set_traffic, toggle_autopay, cancel")

    # For extend action
    days: Optional[int] = Field(None, ge=1, le=3650, description="Days to extend")

    # For set_end_date action
    end_date: Optional[datetime] = Field(None, description="New end date")

    # For change_tariff action
    tariff_id: Optional[int] = Field(None, description="New tariff ID")

    # For set_traffic action
    traffic_limit_gb: Optional[int] = Field(None, ge=0, description="New traffic limit in GB")
    traffic_used_gb: Optional[float] = Field(None, ge=0, description="Set traffic used in GB")

    # For toggle_autopay
    autopay_enabled: Optional[bool] = Field(None, description="Enable/disable autopay")

    # For create new subscription
    is_trial: Optional[bool] = Field(None, description="Is trial subscription")
    device_limit: Optional[int] = Field(None, ge=1, description="Device limit")


class UpdateSubscriptionResponse(BaseModel):
    """Response after subscription update."""
    success: bool
    message: str
    subscription: Optional[UserSubscriptionInfo] = None


class UpdateUserStatusRequest(BaseModel):
    """Request to update user status."""
    status: UserStatusEnum
    reason: Optional[str] = Field(None, max_length=500, description="Reason for status change")


class UpdateUserStatusResponse(BaseModel):
    """Response after status update."""
    success: bool
    old_status: str
    new_status: str
    message: str


class UpdateRestrictionsRequest(BaseModel):
    """Request to update user restrictions."""
    restriction_topup: Optional[bool] = Field(None, description="Block balance top-up")
    restriction_subscription: Optional[bool] = Field(None, description="Block subscription purchase/renewal")
    restriction_reason: Optional[str] = Field(None, max_length=500, description="Reason for restrictions")


class UpdateRestrictionsResponse(BaseModel):
    """Response after restrictions update."""
    success: bool
    restriction_topup: bool
    restriction_subscription: bool
    restriction_reason: Optional[str] = None
    message: str


class UpdatePromoGroupRequest(BaseModel):
    """Request to update user promo group."""
    promo_group_id: Optional[int] = Field(None, description="New promo group ID (null to remove)")


class UpdatePromoGroupResponse(BaseModel):
    """Response after promo group update."""
    success: bool
    old_promo_group_id: Optional[int] = None
    new_promo_group_id: Optional[int] = None
    promo_group_name: Optional[str] = None
    message: str


class DeleteUserRequest(BaseModel):
    """Request to delete user."""
    soft_delete: bool = Field(default=True, description="Soft delete (mark as deleted) or hard delete")
    reason: Optional[str] = Field(None, max_length=500, description="Reason for deletion")


class DeleteUserResponse(BaseModel):
    """Response after user deletion."""
    success: bool
    message: str


# === Statistics ===

class UsersStatsResponse(BaseModel):
    """Users statistics."""
    total_users: int = 0
    active_users: int = 0
    blocked_users: int = 0
    deleted_users: int = 0
    new_today: int = 0
    new_week: int = 0
    new_month: int = 0

    # Subscription stats
    users_with_subscription: int = 0
    users_with_active_subscription: int = 0
    users_with_trial: int = 0
    users_with_expired_subscription: int = 0

    # Financial stats
    total_balance_kopeks: int = 0
    total_balance_rubles: float = 0.0
    avg_balance_kopeks: int = 0

    # Activity stats
    active_today: int = 0
    active_week: int = 0
    active_month: int = 0


# === Search ===

class UserSearchRequest(BaseModel):
    """Request for user search."""
    query: str = Field(..., min_length=1, max_length=255)
    search_by: List[str] = Field(
        default=["telegram_id", "username", "first_name", "last_name", "email"],
        description="Fields to search in"
    )
    limit: int = Field(default=20, ge=1, le=100)


# === Tariffs for User ===

class PeriodPriceInfo(BaseModel):
    """Period price info."""
    days: int
    price_kopeks: int
    price_rubles: float


class UserAvailableTariffItem(BaseModel):
    """Tariff available for user."""
    id: int
    name: str
    description: Optional[str] = None
    is_active: bool = True
    is_trial_available: bool = False
    traffic_limit_gb: int = 0
    device_limit: int = 1
    tier_level: int = 1
    display_order: int = 0

    # Pricing
    period_prices: List[PeriodPriceInfo] = []
    is_daily: bool = False
    daily_price_kopeks: int = 0

    # Custom options
    custom_days_enabled: bool = False
    price_per_day_kopeks: int = 0
    min_days: int = 1
    max_days: int = 365

    # Access info
    is_available: bool = True  # Available for this user's promo group
    requires_promo_group: bool = False  # Requires specific promo group


class UserAvailableTariffsResponse(BaseModel):
    """List of tariffs available for user."""
    user_id: int
    promo_group_id: Optional[int] = None
    promo_group_name: Optional[str] = None
    tariffs: List[UserAvailableTariffItem] = []
    total: int = 0

    # Current subscription tariff
    current_tariff_id: Optional[int] = None
    current_tariff_name: Optional[str] = None


# === Panel Sync ===

class PanelUserInfo(BaseModel):
    """User info from panel."""
    uuid: Optional[str] = None
    short_uuid: Optional[str] = None
    username: Optional[str] = None
    status: Optional[str] = None
    expire_at: Optional[datetime] = None
    traffic_limit_gb: float = 0
    traffic_used_gb: float = 0
    device_limit: int = 1
    subscription_url: Optional[str] = None
    active_squads: List[str] = []


class SyncFromPanelRequest(BaseModel):
    """Request to sync user from panel."""
    update_subscription: bool = Field(default=True, description="Update subscription data")
    update_traffic: bool = Field(default=True, description="Update traffic usage")
    create_if_missing: bool = Field(default=False, description="Create subscription if user exists in panel but not in bot")


class SyncFromPanelResponse(BaseModel):
    """Response after syncing from panel."""
    success: bool
    message: str
    panel_user: Optional[PanelUserInfo] = None
    changes: Dict[str, Any] = {}
    errors: List[str] = []


class SyncToPanelRequest(BaseModel):
    """Request to sync user to panel."""
    create_if_missing: bool = Field(default=True, description="Create user in panel if not exists")
    update_status: bool = Field(default=True, description="Update user status in panel")
    update_traffic_limit: bool = Field(default=True, description="Update traffic limit in panel")
    update_expire_date: bool = Field(default=True, description="Update expire date in panel")
    update_squads: bool = Field(default=True, description="Update connected squads in panel")


class SyncToPanelResponse(BaseModel):
    """Response after syncing to panel."""
    success: bool
    message: str
    action: str = ""  # created, updated, no_changes
    panel_uuid: Optional[str] = None
    changes: Dict[str, Any] = {}
    errors: List[str] = []


class PanelSyncStatusResponse(BaseModel):
    """Panel sync status for user."""
    user_id: int
    telegram_id: int
    remnawave_uuid: Optional[str] = None
    last_sync: Optional[datetime] = None

    # Bot data
    bot_subscription_status: Optional[str] = None
    bot_subscription_end_date: Optional[datetime] = None
    bot_traffic_limit_gb: int = 0
    bot_traffic_used_gb: float = 0
    bot_device_limit: int = 0
    bot_squads: List[str] = []

    # Panel data (if available)
    panel_found: bool = False
    panel_status: Optional[str] = None
    panel_expire_at: Optional[datetime] = None
    panel_traffic_limit_gb: float = 0
    panel_traffic_used_gb: float = 0
    panel_device_limit: int = 0
    panel_squads: List[str] = []

    # Differences
    has_differences: bool = False
    differences: List[str] = []
