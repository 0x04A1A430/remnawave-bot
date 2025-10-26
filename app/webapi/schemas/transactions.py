from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class TransactionResponse(BaseModel):
    id: int
    user_id: int
    type: str
    amount_kopeks: int
    amount_rubles: float
    description: Optional[str] = None
    payment_method: Optional[str] = None
    external_id: Optional[str] = None
    is_completed: bool
    created_at: datetime
    completed_at: Optional[datetime] = None


class TransactionListResponse(BaseModel):
    items: list[TransactionResponse]
    total: int
    limit: int
    offset: int


class PendingPaymentUserResponse(BaseModel):
    id: Optional[int] = None
    telegram_id: Optional[int] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class PendingPaymentResponse(BaseModel):
    id: int
    provider: str
    user: PendingPaymentUserResponse
    amount_kopeks: int
    amount_rubles: float
    currency: str
    status: Optional[str] = None
    is_paid: bool
    description: Optional[str] = None
    payment_url: Optional[str] = None
    external_id: Optional[str] = None
    transaction_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    is_pending: bool


class PendingPaymentDetailResponse(PendingPaymentResponse):
    metadata: Optional[dict[str, Any]] = None


class PendingPaymentListResponse(BaseModel):
    items: list[PendingPaymentResponse]
    total: int


class PendingPaymentCheckResponse(BaseModel):
    payment: PendingPaymentDetailResponse
    check_performed: bool
    status_before: Optional[str] = None
    status_after: Optional[str] = None
    completed: bool


class PendingPaymentBulkCheckResponse(BaseModel):
    total: int
    checked: int
    completed: int
    skipped: int
    results: list[PendingPaymentCheckResponse]
