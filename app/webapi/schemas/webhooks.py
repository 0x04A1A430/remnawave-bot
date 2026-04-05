from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WebhookCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1)
    event_type: str = Field(..., min_length=1, max_length=50)
    secret: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None)


class WebhookUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=1)
    secret: str | None = Field(default=None, max_length=128)
    description: str | None = None
    is_active: bool | None = None


class WebhookResponse(BaseModel):
    id: int
    name: str
    url: str
    event_type: str
    is_active: bool
    description: str | None
    created_at: datetime
    updated_at: datetime
    last_triggered_at: datetime | None
    failure_count: int
    success_count: int

    class Config:
        from_attributes = True


class WebhookListResponse(BaseModel):
    items: list[WebhookResponse]
    total: int
    limit: int
    offset: int


class WebhookDeliveryResponse(BaseModel):
    id: int
    webhook_id: int
    event_type: str
    payload: dict[str, Any]
    response_status: int | None
    response_body: str | None
    status: str
    error_message: str | None
    attempt_number: int
    created_at: datetime
    delivered_at: datetime | None
    next_retry_at: datetime | None

    class Config:
        from_attributes = True


class WebhookDeliveryListResponse(BaseModel):
    items: list[WebhookDeliveryResponse]
    total: int
    limit: int
    offset: int


class WebhookStatsResponse(BaseModel):
    total_webhooks: int
    active_webhooks: int
    total_deliveries: int
    successful_deliveries: int
    failed_deliveries: int
    success_rate: float
