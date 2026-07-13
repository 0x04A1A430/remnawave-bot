from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SubscriptionEventCreate(BaseModel):
    event_type: Literal[
        'activation',
        'purchase',
        'renewal',
        'balance_topup',
        'promocode_activation',
        'referral_link_visit',
        'promo_group_change',
    ]
    user_id: int = Field(..., ge=1)
    subscription_id: int | None = Field(default=None, ge=1)
    transaction_id: int | None = Field(default=None, ge=1)
    amount_kopeks: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=1, max_length=16)
    message: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator('message')
    @classmethod
    def _strip_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class SubscriptionEventResponse(BaseModel):
    id: int
    event_type: str
    user_id: int
    user_full_name: str
    user_username: str | None = None
    user_telegram_id: int | None = None
    subscription_id: int | None = None
    transaction_id: int | None = None
    amount_kopeks: int | None = None
    currency: str | None = None
    message: str | None = None
    occurred_at: datetime
    created_at: datetime
    extra: dict[str, Any] = Field(default_factory=dict)


class SubscriptionEventListResponse(BaseModel):
    items: list[SubscriptionEventResponse]
    total: int
    limit: int
    offset: int
