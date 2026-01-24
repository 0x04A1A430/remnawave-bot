from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SubscriptionResponse(BaseModel):
    id: int
    user_id: int
    status: str
    actual_status: str
    is_trial: bool
    start_date: datetime
    end_date: datetime
    traffic_limit_gb: int
    traffic_used_gb: float
    device_limit: int
    modem_enabled: bool = False
    autopay_enabled: bool
    autopay_days_before: int | None = None
    subscription_url: str | None = None
    subscription_crypto_link: str | None = None
    connected_squads: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SubscriptionCreateRequest(BaseModel):
    user_id: int
    is_trial: bool = False
    duration_days: int | None = None
    traffic_limit_gb: int | None = None
    device_limit: int | None = None
    squad_uuid: str | None = None
    connected_squads: list[str] | None = None
    replace_existing: bool = False


class SubscriptionExtendRequest(BaseModel):
    days: int = Field(..., gt=0)


class SubscriptionTrafficRequest(BaseModel):
    gb: int = Field(..., gt=0)


class SubscriptionDevicesRequest(BaseModel):
    devices: int = Field(..., gt=0)


class SubscriptionSquadRequest(BaseModel):
    squad_uuid: str


class SubscriptionModemRequest(BaseModel):
    enabled: bool
