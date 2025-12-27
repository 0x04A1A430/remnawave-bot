from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ContestTemplateResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    prize_type: str
    prize_value: str
    max_winners: int
    attempts_per_user: int
    times_per_day: int
    schedule_times: Optional[str] = None
    cooldown_hours: int
    payload: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class ContestTemplateListResponse(BaseModel):
    items: List[ContestTemplateResponse]


class ContestTemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    prize_type: Optional[str] = None
    prize_value: Optional[str] = None
    max_winners: Optional[int] = Field(None, ge=1)
    attempts_per_user: Optional[int] = Field(None, ge=1)
    times_per_day: Optional[int] = Field(None, ge=1)
    schedule_times: Optional[str] = None
    cooldown_hours: Optional[int] = Field(None, ge=1)
    payload: Optional[Dict[str, Any]] = None
    is_enabled: Optional[bool] = None


class StartRoundRequest(BaseModel):
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    cooldown_hours: Optional[int] = Field(None, ge=1)
    payload: Optional[Dict[str, Any]] = None
    force: bool = False


class ContestRoundResponse(BaseModel):
    id: int
    template_id: int
    template_slug: str
    template_name: Optional[str] = None
    starts_at: datetime
    ends_at: datetime
    status: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    winners_count: int
    max_winners: int
    attempts_per_user: int
    created_at: datetime
    updated_at: datetime


class ContestRoundListResponse(BaseModel):
    items: List[ContestRoundResponse]
    total: int
    limit: int
    offset: int


class ContestAttemptUser(BaseModel):
    id: int
    telegram_id: Optional[int] = None
    username: Optional[str] = None
    full_name: Optional[str] = None


class ContestAttemptResponse(BaseModel):
    id: int
    round_id: int
    user: ContestAttemptUser
    answer: Optional[str] = None
    is_winner: bool
    created_at: datetime


class ContestAttemptListResponse(BaseModel):
    items: List[ContestAttemptResponse]
    total: int
    limit: int
    offset: int


class ReferralContestResponse(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    prize_text: Optional[str] = None
    contest_type: str
    start_at: datetime
    end_at: datetime
    daily_summary_time: time
    daily_summary_times: Optional[str] = None
    timezone: str
    is_active: bool
    last_daily_summary_date: Optional[date] = None
    last_daily_summary_at: Optional[datetime] = None
    final_summary_sent: bool
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class ReferralContestListResponse(BaseModel):
    items: List[ReferralContestResponse]
    total: int
    limit: int
    offset: int


class ReferralContestCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    prize_text: Optional[str] = None
    contest_type: str = Field("referral_paid", min_length=1)
    start_at: datetime
    end_at: datetime
    daily_summary_time: time = Field(default=time(hour=12))
    daily_summary_times: Optional[str] = Field(
        default=None, description="Список времён ЧЧ:ММ через запятую (например, 12:00,18:00)"
    )
    timezone: str = Field(default="UTC")
    is_active: bool = True
    created_by: Optional[int] = None


class ReferralContestUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    prize_text: Optional[str] = None
    contest_type: Optional[str] = Field(None, min_length=1)
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    daily_summary_time: Optional[time] = None
    daily_summary_times: Optional[str] = Field(
        default=None, description="Список времён ЧЧ:ММ через запятую"
    )
    timezone: Optional[str] = None
    is_active: Optional[bool] = None
    final_summary_sent: Optional[bool] = None
    created_by: Optional[int] = None


class ReferralContestLeaderboardItem(BaseModel):
    user_id: int
    telegram_id: Optional[int] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    referrals_count: int
    total_amount_kopeks: int
    total_amount_rubles: float


class ReferralContestDetailResponse(ReferralContestResponse):
    total_events: Optional[int] = None
    leaderboard: Optional[List[ReferralContestLeaderboardItem]] = None


class ReferralContestEventUser(BaseModel):
    id: int
    telegram_id: Optional[int] = None
    username: Optional[str] = None
    full_name: Optional[str] = None


class ReferralContestEventResponse(BaseModel):
    id: int
    contest_id: int
    referrer: ReferralContestEventUser
    referral: ReferralContestEventUser
    event_type: str
    amount_kopeks: int
    amount_rubles: float
    occurred_at: datetime


class ReferralContestEventListResponse(BaseModel):
    items: List[ReferralContestEventResponse]
    total: int
    limit: int
    offset: int


class ReferralContestParticipant(BaseModel):
    referrer_id: int
    full_name: str
    total_referrals: int
    paid_referrals: int
    unpaid_referrals: int
    total_paid_amount: int


class ReferralContestDetailedStatsResponse(BaseModel):
    total_participants: int
    total_invited: int
    total_paid_amount: int
    total_unpaid: int
    participants: List[ReferralContestParticipant]
