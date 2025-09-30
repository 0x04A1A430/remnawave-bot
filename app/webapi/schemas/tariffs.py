from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class TariffPricePayload(BaseModel):
    period_days: int = Field(..., gt=0)
    price_kopeks: int = Field(..., ge=0)


class TariffResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    traffic_limit_gb: int
    device_limit: int
    is_active: bool
    sort_order: int
    server_squads: List[str] = Field(default_factory=list)
    promo_group_ids: List[int] = Field(default_factory=list)
    prices: List[TariffPricePayload] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TariffCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    traffic_limit_gb: int = Field(..., ge=0)
    device_limit: int = Field(..., ge=1)
    is_active: bool = True
    sort_order: int = 0
    server_squads: List[str] = Field(default_factory=list)
    promo_group_ids: Optional[List[int]] = None
    prices: List[TariffPricePayload] = Field(default_factory=list)


class TariffUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    traffic_limit_gb: Optional[int] = Field(default=None, ge=0)
    device_limit: Optional[int] = Field(default=None, ge=1)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    server_squads: Optional[List[str]] = None
    promo_group_ids: Optional[List[int]] = None
    prices: Optional[List[TariffPricePayload]] = None
