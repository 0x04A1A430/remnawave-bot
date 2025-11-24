from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, validator


def _normalize_text(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("Text cannot be empty")
    return cleaned


class WelcomeTextResponse(BaseModel):
    id: int
    text: str
    is_active: bool
    is_enabled: bool
    created_by: Optional[int]
    created_at: datetime
    updated_at: datetime


class WelcomeTextCreateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    is_enabled: bool = True
    is_active: bool = True

    _normalize_text = validator("text", allow_reuse=True)(_normalize_text)


class WelcomeTextUpdateRequest(BaseModel):
    text: Optional[str] = Field(None, min_length=1, max_length=4000)
    is_enabled: Optional[bool] = None
    is_active: Optional[bool] = None

    @validator("text")
    def validate_text(cls, value):  # noqa: D401,B902
        if value is None:
            return value
        return _normalize_text(value)


class WelcomeTextListResponse(BaseModel):
    items: list[WelcomeTextResponse]
    total: int
    limit: int
    offset: int
