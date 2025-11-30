from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class SubscriptionEventCreate(BaseModel):
    event_type: Literal[
        "activation",
        "purchase",
        "renewal",
        "balance_topup",
        "promocode_activation",
        "referral_link_visit",
        "promo_group_change",
    ]
    user_id: int = Field(..., ge=1)
    subscription_id: Optional[int] = Field(default=None, ge=1)
    transaction_id: Optional[int] = Field(default=None, ge=1)
    amount_kopeks: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=1, max_length=16)
    message: Optional[str] = Field(default=None, max_length=2000)
    occurred_at: Optional[datetime] = None
    extra: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def _strip_message(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class SubscriptionEventResponse(BaseModel):
    id: int
    event_type: str
    user_id: int
    user_full_name: str
    user_username: Optional[str] = None
    user_telegram_id: int
    subscription_id: Optional[int] = None
    transaction_id: Optional[int] = None
    amount_kopeks: Optional[int] = None
    currency: Optional[str] = None
    message: Optional[str] = None
    occurred_at: datetime
    created_at: datetime
    extra: Dict[str, Any] = Field(default_factory=dict)


class SubscriptionEventListResponse(BaseModel):
    items: list[SubscriptionEventResponse]
    total: int
    limit: int
    offset: int
