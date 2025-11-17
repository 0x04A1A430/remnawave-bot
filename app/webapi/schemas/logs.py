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


class SystemLogPreviewResponse(BaseModel):
    """Ответ с превью системного лог-файла бота."""

    path: str = Field(..., description="Абсолютный путь до лог-файла")
    exists: bool = Field(..., description="Флаг наличия лог-файла")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Дата и время последнего изменения лог-файла",
    )
    size_bytes: int = Field(..., ge=0, description="Размер лог-файла в байтах")
    size_chars: int = Field(..., ge=0, description="Количество символов в лог-файле")
    preview: str = Field(
        default="",
        description="Фрагмент содержимого лог-файла, возвращаемый для предпросмотра",
    )
    preview_chars: int = Field(..., ge=0, description="Размер предпросмотра в символах")
    preview_truncated: bool = Field(
        ..., description="Флаг усечения предпросмотра относительно полного файла"
    )
    download_url: Optional[str] = Field(
        default=None,
        description="Относительный путь до endpoint для скачивания лог-файла",
    )


class SystemLogFullResponse(BaseModel):
    """Полное содержимое системного лог-файла."""

    path: str
    exists: bool
    updated_at: Optional[datetime] = None
    size_bytes: int
    size_chars: int
    content: str
