"""Referral program schemas for cabinet."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class ReferralInfoResponse(BaseModel):
    """Referral program info for current user."""
    referral_code: str
    referral_link: str
    total_referrals: int
    active_referrals: int
    total_earnings_kopeks: int
    total_earnings_rubles: float
    commission_percent: int


class ReferralItemResponse(BaseModel):
    """Single referral info."""
    id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    created_at: datetime
    has_subscription: bool
    has_paid: bool


class ReferralListResponse(BaseModel):
    """Paginated referral list."""
    items: List[ReferralItemResponse]
    total: int
    page: int
    per_page: int
    pages: int


class ReferralEarningResponse(BaseModel):
    """Referral earning history item."""
    id: int
    amount_kopeks: int
    amount_rubles: float
    reason: str
    referral_username: Optional[str] = None
    referral_first_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ReferralEarningsListResponse(BaseModel):
    """Paginated referral earnings list."""
    items: List[ReferralEarningResponse]
    total: int
    total_amount_kopeks: int
    total_amount_rubles: float
    page: int
    per_page: int
    pages: int


class ReferralTermsResponse(BaseModel):
    """Referral program terms."""
    is_enabled: bool
    commission_percent: int
    minimum_topup_kopeks: int
    minimum_topup_rubles: float
    first_topup_bonus_kopeks: int
    first_topup_bonus_rubles: float
    inviter_bonus_kopeks: int
    inviter_bonus_rubles: float
