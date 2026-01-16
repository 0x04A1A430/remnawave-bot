"""Pydantic schemas for cabinet broadcasts."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ============ Filters ============

class BroadcastFilter(BaseModel):
    """Single broadcast filter."""
    key: str
    label: str
    count: Optional[int] = None
    group: Optional[str] = None  # basic, subscription, traffic, registration, source, activity


class TariffFilter(BaseModel):
    """Tariff-based filter."""
    key: str  # tariff_1, tariff_2, ...
    label: str  # tariff name
    tariff_id: int
    count: int


class BroadcastFiltersResponse(BaseModel):
    """Response with all available filters."""
    filters: List[BroadcastFilter]  # basic filters
    tariff_filters: List[TariffFilter]  # tariff filters
    custom_filters: List[BroadcastFilter]  # custom filters


# ============ Tariffs ============

class TariffForBroadcast(BaseModel):
    """Tariff info for broadcast filtering."""
    id: int
    name: str
    filter_key: str  # tariff_{id}
    active_users_count: int


class BroadcastTariffsResponse(BaseModel):
    """Response with tariffs for filtering."""
    tariffs: List[TariffForBroadcast]


# ============ Buttons ============

class BroadcastButton(BaseModel):
    """Single broadcast button."""
    key: str
    label: str
    default: bool = False


class BroadcastButtonsResponse(BaseModel):
    """Response with available buttons."""
    buttons: List[BroadcastButton]


# ============ Media ============

class BroadcastMediaRequest(BaseModel):
    """Media attachment for broadcast."""
    type: str = Field(..., pattern=r"^(photo|video|document)$")
    file_id: str
    caption: Optional[str] = None


# ============ Create ============

class BroadcastCreateRequest(BaseModel):
    """Request to create a broadcast."""
    target: str
    message_text: str = Field(..., min_length=1, max_length=4000)
    selected_buttons: List[str] = Field(default_factory=lambda: ["home"])
    media: Optional[BroadcastMediaRequest] = None


# ============ Response ============

class BroadcastResponse(BaseModel):
    """Broadcast response."""
    id: int
    target_type: str
    message_text: str
    has_media: bool
    media_type: Optional[str] = None
    media_file_id: Optional[str] = None
    media_caption: Optional[str] = None
    total_count: int
    sent_count: int
    failed_count: int
    status: str  # queued|in_progress|completed|partial|failed|cancelled|cancelling
    admin_id: Optional[int] = None
    admin_name: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    progress_percent: float = 0.0

    class Config:
        from_attributes = True


class BroadcastListResponse(BaseModel):
    """Paginated list of broadcasts."""
    items: List[BroadcastResponse]
    total: int
    limit: int
    offset: int


# ============ Preview ============

class BroadcastPreviewRequest(BaseModel):
    """Request to preview broadcast recipients count."""
    target: str


class BroadcastPreviewResponse(BaseModel):
    """Preview response with recipients count."""
    target: str
    count: int
