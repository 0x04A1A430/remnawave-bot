from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    id: int
    name: str
    prefix: str = Field(..., description='Первые символы токена для идентификации')
    description: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    last_used_ip: str | None = None
    created_by: str | None = None


class TokenCreateRequest(BaseModel):
    name: str
    description: str | None = None
    expires_at: datetime | None = None


class TokenCreateResponse(TokenResponse):
    token: str = Field(..., description='Полное значение токена (возвращается один раз)')
