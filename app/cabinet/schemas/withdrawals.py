"""Withdrawal system schemas for cabinet."""

from datetime import datetime

from pydantic import BaseModel, Field


# ==================== User-facing ====================


class WithdrawalBalanceResponse(BaseModel):
    """Withdrawal balance info for user."""

    total_earned: int
    referral_spent: int
    withdrawn: int
    pending: int
    available_referral: int
    available_total: int
    only_referral_mode: bool
    min_amount_kopeks: int
    is_withdrawal_enabled: bool
    can_request: bool
    cannot_request_reason: str | None = None
    requisites_text: str = ''


class WithdrawalCreateRequest(BaseModel):
    """Request to create a withdrawal."""

    amount_kopeks: int = Field(..., gt=0, le=10_000_000)
    payment_details: str = Field(..., min_length=5, max_length=1000)


class WithdrawalItemResponse(BaseModel):
    """Withdrawal request item."""

    id: int
    amount_kopeks: int
    amount_rubles: float
    status: str
    payment_details: str | None = None
    admin_comment: str | None = None
    created_at: datetime
    processed_at: datetime | None = None

    class Config:
        from_attributes = True


class WithdrawalListResponse(BaseModel):
    """List of user's withdrawal requests."""

    items: list[WithdrawalItemResponse]
    total: int


class WithdrawalCreateResponse(BaseModel):
    """Response after creating withdrawal."""

    id: int
    amount_kopeks: int
    status: str


# ==================== Admin-facing ====================


class AdminWithdrawalItem(BaseModel):
    """Withdrawal request in admin list."""

    id: int
    user_id: int
    username: str | None = None
    first_name: str | None = None
    telegram_id: int | None = None
    amount_kopeks: int
    amount_rubles: float
    status: str
    risk_score: int = 0
    risk_level: str = 'low'
    payment_details: str | None = None
    admin_comment: str | None = None
    created_at: datetime
    processed_at: datetime | None = None


class AdminWithdrawalListResponse(BaseModel):
    """List of withdrawal requests for admin."""

    items: list[AdminWithdrawalItem]
    total: int
    pending_count: int = 0
    pending_total_kopeks: int = 0


class AdminWithdrawalDetailResponse(BaseModel):
    """Detailed withdrawal request for admin."""

    id: int
    user_id: int
    username: str | None = None
    first_name: str | None = None
    telegram_id: int | None = None
    amount_kopeks: int
    amount_rubles: float
    status: str
    risk_score: int = 0
    risk_level: str = 'low'
    risk_analysis: dict | None = None
    payment_details: str | None = None
    admin_comment: str | None = None
    balance_kopeks: int = 0
    total_referrals: int = 0
    total_earnings_kopeks: int = 0
    created_at: datetime
    processed_at: datetime | None = None


class AdminApproveWithdrawalRequest(BaseModel):
    """Request to approve a withdrawal."""

    comment: str | None = Field(None, max_length=2000)


class AdminRejectWithdrawalRequest(BaseModel):
    """Request to reject a withdrawal."""

    comment: str | None = Field(None, max_length=2000)
