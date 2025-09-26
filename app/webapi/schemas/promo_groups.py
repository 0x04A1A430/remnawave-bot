from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel, Field


class PromoGroupResponse(BaseModel):
    id: int
    name: str
    server_discount_percent: int
    traffic_discount_percent: int
    device_discount_percent: int
    period_discounts: Dict[int, int] = Field(default_factory=dict)
    auto_assign_total_spent_kopeks: Optional[int] = None
    apply_discounts_to_addons: bool
    is_default: bool
    members_count: int = 0
    created_at: datetime
    updated_at: datetime


class PromoGroupCreateRequest(BaseModel):
    name: str
    server_discount_percent: int = 0
    traffic_discount_percent: int = 0
    device_discount_percent: int = 0
    period_discounts: Optional[Dict[int, int]] = None
    auto_assign_total_spent_kopeks: Optional[int] = None
    apply_discounts_to_addons: bool = True


class PromoGroupUpdateRequest(BaseModel):
    name: Optional[str] = None
    server_discount_percent: Optional[int] = None
    traffic_discount_percent: Optional[int] = None
    device_discount_percent: Optional[int] = None
    period_discounts: Optional[Dict[int, int]] = None
    auto_assign_total_spent_kopeks: Optional[int] = None
    apply_discounts_to_addons: Optional[bool] = None
