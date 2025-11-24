from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class PartnerReferrerItem(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    referral_code: Optional[str] = None
    referral_commission_percent: Optional[int] = None
    effective_referral_commission_percent: int
    invited_count: int
    active_referrals: int
    total_earned_kopeks: int
    total_earned_rubles: float
    month_earned_kopeks: int
    month_earned_rubles: float
    created_at: datetime
    last_activity: Optional[datetime] = None


class PartnerReferrerListResponse(BaseModel):
    items: List[PartnerReferrerItem] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class PartnerReferralItem(BaseModel):
    id: int
    telegram_id: int
    full_name: str
    username: Optional[str] = None
    created_at: datetime
    last_activity: Optional[datetime] = None
    has_made_first_topup: bool
    balance_kopeks: int
    balance_rubles: float
    total_earned_kopeks: int
    total_earned_rubles: float
    topups_count: int
    days_since_registration: int
    days_since_activity: Optional[int] = None
    status: str


class PartnerReferralList(BaseModel):
    items: List[PartnerReferralItem] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
    has_next: bool
    has_prev: bool
    current_page: int
    total_pages: int


class PartnerReferrerDetail(BaseModel):
    referrer: PartnerReferrerItem
    referrals: PartnerReferralList


class PartnerReferralCommissionUpdate(BaseModel):
    referral_commission_percent: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description="Индивидуальный процент реферальной комиссии для пользователя",
    )
