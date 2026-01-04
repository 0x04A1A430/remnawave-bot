"""Admin tickets routes for cabinet."""

import logging
import math
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field

from app.database.models import User, Ticket, TicketMessage
from app.database.crud.ticket import TicketCRUD, TicketMessageCRUD
from app.config import settings

from ..dependencies import get_cabinet_db, get_current_admin_user
from ..schemas.tickets import TicketMessageResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/tickets", tags=["Cabinet Admin Tickets"])


# Admin-specific schemas
class AdminTicketUserInfo(BaseModel):
    """User info for admin view."""
    id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    class Config:
        from_attributes = True


class AdminTicketResponse(BaseModel):
    """Ticket data for admin."""
    id: int
    title: str
    status: str
    priority: str
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    messages_count: int = 0
    user: Optional[AdminTicketUserInfo] = None
    last_message: Optional[TicketMessageResponse] = None

    class Config:
        from_attributes = True


class AdminTicketDetailResponse(BaseModel):
    """Ticket with all messages for admin."""
    id: int
    title: str
    status: str
    priority: str
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    is_reply_blocked: bool = False
    user: Optional[AdminTicketUserInfo] = None
    messages: List[TicketMessageResponse] = []

    class Config:
        from_attributes = True


class AdminTicketListResponse(BaseModel):
    """Paginated ticket list for admin."""
    items: List[AdminTicketResponse]
    total: int
    page: int
    per_page: int
    pages: int


class AdminReplyRequest(BaseModel):
    """Admin reply to ticket."""
    message: str = Field(..., min_length=1, max_length=4000, description="Reply message")


class AdminStatusUpdateRequest(BaseModel):
    """Update ticket status."""
    status: str = Field(..., description="New status: open, answered, pending, closed")


class AdminPriorityUpdateRequest(BaseModel):
    """Update ticket priority."""
    priority: str = Field(..., description="New priority: low, normal, high, urgent")


class AdminStatsResponse(BaseModel):
    """Ticket statistics for admin."""
    total: int
    open: int
    pending: int
    answered: int
    closed: int


def _message_to_response(message: TicketMessage) -> TicketMessageResponse:
    """Convert TicketMessage to response."""
    return TicketMessageResponse(
        id=message.id,
        message_text=message.message_text or "",
        is_from_admin=message.is_from_admin,
        has_media=bool(message.media_file_id),
        media_type=message.media_type,
        media_caption=message.media_caption,
        created_at=message.created_at,
    )


def _user_to_info(user: User) -> AdminTicketUserInfo:
    """Convert User to admin info."""
    return AdminTicketUserInfo(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )


def _ticket_to_admin_response(ticket: Ticket, include_messages: bool = False) -> AdminTicketResponse:
    """Convert Ticket to admin response."""
    last_message = None
    messages_count = len(ticket.messages) if ticket.messages else 0

    if ticket.messages:
        last_msg = max(ticket.messages, key=lambda m: m.created_at)
        last_message = _message_to_response(last_msg)

    user_info = None
    if hasattr(ticket, 'user') and ticket.user:
        user_info = _user_to_info(ticket.user)

    return AdminTicketResponse(
        id=ticket.id,
        title=ticket.title or f"Ticket #{ticket.id}",
        status=ticket.status,
        priority=ticket.priority or "normal",
        created_at=ticket.created_at,
        updated_at=ticket.updated_at or ticket.created_at,
        closed_at=ticket.closed_at,
        messages_count=messages_count,
        user=user_info,
        last_message=last_message,
    )


@router.get("/stats", response_model=AdminStatsResponse)
async def get_ticket_stats(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get ticket statistics."""
    # Total count
    total_result = await db.execute(select(func.count()).select_from(Ticket))
    total = total_result.scalar() or 0

    # Count by status
    statuses = {}
    for status_name in ["open", "pending", "answered", "closed"]:
        result = await db.execute(
            select(func.count()).select_from(Ticket).where(Ticket.status == status_name)
        )
        statuses[status_name] = result.scalar() or 0

    return AdminStatsResponse(
        total=total,
        open=statuses.get("open", 0),
        pending=statuses.get("pending", 0),
        answered=statuses.get("answered", 0),
        closed=statuses.get("closed", 0),
    )


@router.get("", response_model=AdminTicketListResponse)
async def get_all_tickets(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    priority_filter: Optional[str] = Query(None, alias="priority", description="Filter by priority"),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get all tickets for admin."""
    # Base query with user relationship
    query = (
        select(Ticket)
        .options(selectinload(Ticket.messages), selectinload(Ticket.user))
    )

    # Build count query
    count_query = select(func.count()).select_from(Ticket)

    # Apply filters
    if status_filter:
        query = query.where(Ticket.status == status_filter)
        count_query = count_query.where(Ticket.status == status_filter)

    if priority_filter:
        query = query.where(Ticket.priority == priority_filter)
        count_query = count_query.where(Ticket.priority == priority_filter)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate - order by updated_at desc (newest first)
    offset = (page - 1) * per_page
    query = query.order_by(desc(Ticket.updated_at)).offset(offset).limit(per_page)

    result = await db.execute(query)
    tickets = result.scalars().all()

    items = [_ticket_to_admin_response(t) for t in tickets]
    pages = math.ceil(total / per_page) if total > 0 else 1

    return AdminTicketListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/{ticket_id}", response_model=AdminTicketDetailResponse)
async def get_ticket_detail(
    ticket_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get ticket with all messages for admin."""
    query = (
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .options(selectinload(Ticket.messages), selectinload(Ticket.user))
    )

    result = await db.execute(query)
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    messages = sorted(ticket.messages or [], key=lambda m: m.created_at)
    messages_response = [_message_to_response(m) for m in messages]

    user_info = None
    if ticket.user:
        user_info = _user_to_info(ticket.user)

    return AdminTicketDetailResponse(
        id=ticket.id,
        title=ticket.title or f"Ticket #{ticket.id}",
        status=ticket.status,
        priority=ticket.priority or "normal",
        created_at=ticket.created_at,
        updated_at=ticket.updated_at or ticket.created_at,
        closed_at=ticket.closed_at,
        is_reply_blocked=ticket.is_reply_blocked if hasattr(ticket, "is_reply_blocked") else False,
        user=user_info,
        messages=messages_response,
    )


@router.post("/{ticket_id}/reply", response_model=TicketMessageResponse)
async def reply_to_ticket(
    ticket_id: int,
    request: AdminReplyRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Reply to a ticket as admin."""
    # Get ticket
    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=False, load_user=True)

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    # Create admin message
    message = TicketMessage(
        ticket_id=ticket.id,
        user_id=ticket.user_id,
        message_text=request.message,
        is_from_admin=True,
        created_at=datetime.utcnow(),
    )
    db.add(message)

    # Update ticket status to answered
    ticket.status = "answered"
    ticket.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(message)

    # Try to notify user via Telegram
    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            from app.handlers.admin.tickets import notify_user_about_ticket_reply
            await notify_user_about_ticket_reply(bot, ticket, request.message, db)
        except Exception as e:
            logger.warning(f"Failed to notify user about ticket reply: {e}")
        finally:
            await bot.session.close()
    except Exception as e:
        logger.warning(f"Failed to send Telegram notification: {e}")

    return _message_to_response(message)


@router.post("/{ticket_id}/status", response_model=AdminTicketDetailResponse)
async def update_ticket_status(
    ticket_id: int,
    request: AdminStatusUpdateRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update ticket status."""
    allowed_statuses = {"open", "pending", "answered", "closed"}
    if request.status not in allowed_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Allowed: {', '.join(allowed_statuses)}",
        )

    query = (
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .options(selectinload(Ticket.messages), selectinload(Ticket.user))
    )

    result = await db.execute(query)
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    ticket.status = request.status
    ticket.updated_at = datetime.utcnow()
    if request.status == "closed":
        ticket.closed_at = datetime.utcnow()
    else:
        ticket.closed_at = None

    await db.commit()
    await db.refresh(ticket)

    messages = sorted(ticket.messages or [], key=lambda m: m.created_at)
    messages_response = [_message_to_response(m) for m in messages]

    user_info = None
    if ticket.user:
        user_info = _user_to_info(ticket.user)

    return AdminTicketDetailResponse(
        id=ticket.id,
        title=ticket.title or f"Ticket #{ticket.id}",
        status=ticket.status,
        priority=ticket.priority or "normal",
        created_at=ticket.created_at,
        updated_at=ticket.updated_at or ticket.created_at,
        closed_at=ticket.closed_at,
        is_reply_blocked=ticket.is_reply_blocked if hasattr(ticket, "is_reply_blocked") else False,
        user=user_info,
        messages=messages_response,
    )


@router.post("/{ticket_id}/priority", response_model=AdminTicketDetailResponse)
async def update_ticket_priority(
    ticket_id: int,
    request: AdminPriorityUpdateRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update ticket priority."""
    allowed_priorities = {"low", "normal", "high", "urgent"}
    if request.priority not in allowed_priorities:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid priority. Allowed: {', '.join(allowed_priorities)}",
        )

    query = (
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .options(selectinload(Ticket.messages), selectinload(Ticket.user))
    )

    result = await db.execute(query)
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    ticket.priority = request.priority
    ticket.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(ticket)

    messages = sorted(ticket.messages or [], key=lambda m: m.created_at)
    messages_response = [_message_to_response(m) for m in messages]

    user_info = None
    if ticket.user:
        user_info = _user_to_info(ticket.user)

    return AdminTicketDetailResponse(
        id=ticket.id,
        title=ticket.title or f"Ticket #{ticket.id}",
        status=ticket.status,
        priority=ticket.priority or "normal",
        created_at=ticket.created_at,
        updated_at=ticket.updated_at or ticket.created_at,
        closed_at=ticket.closed_at,
        is_reply_blocked=ticket.is_reply_blocked if hasattr(ticket, "is_reply_blocked") else False,
        user=user_info,
        messages=messages_response,
    )
