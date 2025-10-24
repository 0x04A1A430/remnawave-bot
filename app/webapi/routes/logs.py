"""Маршруты административного API для просмотра логов."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Security
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.ticket import TicketCRUD
from app.services.monitoring_service import monitoring_service

from ..dependencies import get_db_session, require_api_token
from ..schemas.logs import (
    MonitoringLogEntry,
    MonitoringLogListResponse,
    MonitoringLogTypeListResponse,
    SupportAuditActionsResponse,
    SupportAuditLogEntry,
    SupportAuditLogListResponse,
)

router = APIRouter()


@router.get("/monitoring", response_model=MonitoringLogListResponse)
async def list_monitoring_logs(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200, description="Количество записей на странице"),
    offset: int = Query(0, ge=0, description="Смещение от начала списка"),
    event_type: Optional[str] = Query(
        default=None,
        max_length=100,
        description="Фильтр по типу события",
    ),
) -> MonitoringLogListResponse:
    """Получить список логов мониторинга с пагинацией."""

    per_page = limit
    page = (offset // per_page) + 1

    raw_logs = await monitoring_service.get_monitoring_logs(
        db,
        event_type=event_type,
        page=page,
        per_page=per_page,
    )
    total = await monitoring_service.get_monitoring_logs_count(db, event_type=event_type)

    return MonitoringLogListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[MonitoringLogEntry(**entry) for entry in raw_logs],
    )


@router.get("/monitoring/event-types", response_model=MonitoringLogTypeListResponse)
async def list_monitoring_event_types(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> MonitoringLogTypeListResponse:
    """Получить список доступных типов событий мониторинга."""

    event_types = await monitoring_service.get_monitoring_event_types(db)
    return MonitoringLogTypeListResponse(items=event_types)


@router.get("/support", response_model=SupportAuditLogListResponse)
async def list_support_audit_logs(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200, description="Количество записей на странице"),
    offset: int = Query(0, ge=0, description="Смещение от начала списка"),
    action: Optional[str] = Query(
        default=None,
        max_length=50,
        description="Фильтр по типу действия модератора",
    ),
) -> SupportAuditLogListResponse:
    """Получить список аудита действий модераторов поддержки."""

    logs = await TicketCRUD.list_support_audit(
        db,
        limit=limit,
        offset=offset,
        action=action,
    )
    total = await TicketCRUD.count_support_audit(db, action=action)

    return SupportAuditLogListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            SupportAuditLogEntry(
                id=log.id,
                actor_user_id=log.actor_user_id,
                actor_telegram_id=log.actor_telegram_id,
                is_moderator=log.is_moderator,
                action=log.action,
                ticket_id=log.ticket_id,
                target_user_id=log.target_user_id,
                details=log.details,
                created_at=log.created_at,
            )
            for log in logs
        ],
    )


@router.get("/support/actions", response_model=SupportAuditActionsResponse)
async def list_support_audit_actions(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> SupportAuditActionsResponse:
    """Получить список действий, доступных в аудите поддержки."""

    actions = await TicketCRUD.list_support_audit_actions(db)
    return SupportAuditActionsResponse(items=actions)
