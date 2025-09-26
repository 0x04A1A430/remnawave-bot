from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Ticket, TicketStatus
from app.webapi.dependencies import get_db, require_permission
from app.webapi.schemas import (
    Pagination,
    TicketListResponse,
    TicketMessageSchema,
    TicketSchema,
    TicketUpdateRequest,
)

router = APIRouter(prefix="/tickets")


def _ticket_to_schema(ticket: Ticket) -> TicketSchema:
    messages = sorted(ticket.messages, key=lambda message: message.created_at)
    return TicketSchema(
        id=ticket.id,
        user_id=ticket.user_id,
        title=ticket.title,
        status=ticket.status,
        priority=ticket.priority,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        closed_at=ticket.closed_at,
        user_reply_block_permanent=ticket.user_reply_block_permanent,
        user_reply_block_until=ticket.user_reply_block_until,
        messages=[TicketMessageSchema.model_validate(message) for message in messages],
    )


@router.get("", response_model=TicketListResponse)
async def list_tickets(
    status_filter: Optional[TicketStatus] = Query(default=None, alias="status"),
    user_id: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.tickets:read")),
) -> TicketListResponse:
    stmt = select(Ticket).options(selectinload(Ticket.messages))
    count_stmt = select(func.count(Ticket.id))

    if status_filter:
        stmt = stmt.where(Ticket.status == status_filter.value)
        count_stmt = count_stmt.where(Ticket.status == status_filter.value)
    if user_id:
        stmt = stmt.where(Ticket.user_id == user_id)
        count_stmt = count_stmt.where(Ticket.user_id == user_id)

    stmt = stmt.order_by(Ticket.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(stmt)
    tickets = result.scalars().unique().all()

    total = await db.scalar(count_stmt) or 0
    pagination = Pagination(total=total, limit=limit, offset=offset)

    return TicketListResponse(
        pagination=pagination,
        items=[_ticket_to_schema(ticket) for ticket in tickets],
    )


@router.get("/{ticket_id}", response_model=TicketSchema)
async def get_ticket(
    ticket_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.tickets:read")),
) -> TicketSchema:
    stmt = (
        select(Ticket)
        .options(selectinload(Ticket.messages))
        .where(Ticket.id == ticket_id)
    )
    result = await db.execute(stmt)
    ticket = result.scalars().unique().one_or_none()

    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тикет не найден")

    return _ticket_to_schema(ticket)


@router.patch("/{ticket_id}", response_model=TicketSchema)
async def update_ticket(
    payload: TicketUpdateRequest,
    ticket_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.tickets:write")),
) -> TicketSchema:
    stmt = (
        select(Ticket)
        .options(selectinload(Ticket.messages))
        .where(Ticket.id == ticket_id)
    )
    result = await db.execute(stmt)
    ticket = result.scalars().unique().one_or_none()

    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тикет не найден")

    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        new_status = data["status"].value if isinstance(data["status"], TicketStatus) else data["status"]
        data["status"] = new_status
        if new_status == TicketStatus.CLOSED.value:
            ticket.closed_at = ticket.closed_at or datetime.utcnow()
        else:
            ticket.closed_at = None

    for field, value in data.items():
        setattr(ticket, field, value)

    ticket.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(ticket)

    return _ticket_to_schema(ticket)
