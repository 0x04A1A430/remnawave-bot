from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TransactionResponse(BaseModel):
    id: int
    user_id: int
    type: str
    amount_kopeks: int
    amount_rubles: float
    description: str | None = None
    payment_method: str | None = None
    external_id: str | None = None
    is_completed: bool
    created_at: datetime
    completed_at: datetime | None = None


class TransactionListResponse(BaseModel):
    items: list[TransactionResponse]
    total: int
    limit: int
    offset: int
