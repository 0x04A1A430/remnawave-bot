"""Pydantic-схемы для работы с логами административного API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MonitoringLogEntry(BaseModel):
    """Запись лога мониторинга."""

    id: int
    event_type: str = Field(..., description="Тип события мониторинга")
    message: str = Field(..., description="Краткое описание события")
    data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Дополнительные данные события",
    )
    is_success: bool = Field(..., description="Флаг успешности выполнения операции")
    created_at: datetime = Field(..., description="Дата и время создания записи")


class MonitoringLogListResponse(BaseModel):
    """Ответ со списком логов мониторинга."""

    total: int = Field(..., ge=0)
    limit: int = Field(..., ge=1)
    offset: int = Field(..., ge=0)
    items: List[MonitoringLogEntry]


class MonitoringLogTypeListResponse(BaseModel):
    """Ответ со списком доступных типов событий мониторинга."""

    items: List[str] = Field(default_factory=list)


class SupportAuditLogEntry(BaseModel):
    """Запись аудита модераторов поддержки."""

    id: int
    actor_user_id: Optional[int]
    actor_telegram_id: int
    is_moderator: bool
    action: str
    ticket_id: Optional[int]
    target_user_id: Optional[int]
    details: Optional[Dict[str, Any]] = None
    created_at: datetime


class SupportAuditLogListResponse(BaseModel):
    """Ответ со списком аудита поддержки."""

    total: int = Field(..., ge=0)
    limit: int = Field(..., ge=1)
    offset: int = Field(..., ge=0)
    items: List[SupportAuditLogEntry]


class SupportAuditActionsResponse(BaseModel):
    """Ответ со списком доступных действий аудита поддержки."""

    items: List[str] = Field(default_factory=list)
