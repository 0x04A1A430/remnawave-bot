from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, validator


def _normalize_text(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("Message text cannot be empty")
    return cleaned


class UserMessageResponse(BaseModel):
    id: int
    message_text: str
    is_active: bool
    sort_order: int
    created_by: Optional[int]
    created_at: datetime
    updated_at: datetime


class UserMessageCreateRequest(BaseModel):
    message_text: str = Field(..., min_length=1, max_length=4000)
    is_active: bool = True
    sort_order: int = Field(0, ge=0)

    _normalize_message_text = validator("message_text", allow_reuse=True)(_normalize_text)


class UserMessageUpdateRequest(BaseModel):
    message_text: Optional[str] = Field(None, min_length=1, max_length=4000)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=0)

    @validator("message_text")
    def validate_message_text(cls, value):  # noqa: D401,B902
        if value is None:
            return value
        return _normalize_text(value)


class UserMessageListResponse(BaseModel):
    items: list[UserMessageResponse]
    total: int
    limit: int
    offset: int
