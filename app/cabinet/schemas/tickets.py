"""Support tickets schemas for cabinet."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class TicketMessageResponse(BaseModel):
    """Ticket message data."""
    id: int
    message_text: str
    is_from_admin: bool
    has_media: bool = False
    media_type: Optional[str] = None
    media_caption: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TicketResponse(BaseModel):
    """Ticket data."""
    id: int
    title: str
    status: str
    priority: str
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    messages_count: int = 0
    last_message: Optional[TicketMessageResponse] = None

    class Config:
        from_attributes = True


class TicketDetailResponse(BaseModel):
    """Ticket with all messages."""
    id: int
    title: str
    status: str
    priority: str
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    is_reply_blocked: bool = False
    messages: List[TicketMessageResponse] = []

    class Config:
        from_attributes = True


class TicketListResponse(BaseModel):
    """Paginated ticket list."""
    items: List[TicketResponse]
    total: int
    page: int
    per_page: int
    pages: int


class TicketCreateRequest(BaseModel):
    """Request to create a new ticket."""
    title: str = Field(..., min_length=3, max_length=255, description="Ticket title")
    message: str = Field(..., min_length=10, max_length=4000, description="Initial message")


class TicketMessageCreateRequest(BaseModel):
    """Request to add message to ticket."""
    message: str = Field(..., min_length=1, max_length=4000, description="Message text")
