"""Pydantic schemas for cabinet broadcasts."""

from datetime import datetime

from pydantic import BaseModel, Field


# ============ Filters ============


class BroadcastFilter(BaseModel):
    """Single broadcast filter."""

    key: str
    label: str
    count: int | None = None
    group: str | None = None  # basic, subscription, traffic, registration, source, activity


class TariffFilter(BaseModel):
    """Tariff-based filter."""

    key: str  # tariff_1, tariff_2, ...
    label: str  # tariff name
    tariff_id: int
    count: int


class BroadcastFiltersResponse(BaseModel):
    """Response with all available filters."""

    filters: list[BroadcastFilter]  # basic filters
    tariff_filters: list[TariffFilter]  # tariff filters
    custom_filters: list[BroadcastFilter]  # custom filters


# ============ Tariffs ============


class TariffForBroadcast(BaseModel):
    """Tariff info for broadcast filtering."""

    id: int
    name: str
    filter_key: str  # tariff_{id}
    active_users_count: int


class BroadcastTariffsResponse(BaseModel):
    """Response with tariffs for filtering."""

    tariffs: list[TariffForBroadcast]


# ============ Buttons ============


class BroadcastButton(BaseModel):
    """Single broadcast button."""

    key: str
    label: str
    default: bool = False


class BroadcastButtonsResponse(BaseModel):
    """Response with available buttons."""

    buttons: list[BroadcastButton]


# ============ Media ============


class BroadcastMediaRequest(BaseModel):
    """Media attachment for broadcast."""

    type: str = Field(..., pattern=r'^(photo|video|document)$')
    file_id: str
    caption: str | None = None


# ============ Create ============


class BroadcastCreateRequest(BaseModel):
    """Request to create a broadcast."""

    target: str
    message_text: str = Field(..., min_length=1, max_length=4000)
    selected_buttons: list[str] = Field(default_factory=lambda: ['home'])
    media: BroadcastMediaRequest | None = None


# ============ Response ============


class BroadcastResponse(BaseModel):
    """Broadcast response."""

    id: int
    target_type: str
    message_text: str
    has_media: bool
    media_type: str | None = None
    media_file_id: str | None = None
    media_caption: str | None = None
    total_count: int
    sent_count: int
    failed_count: int
    status: str  # queued|in_progress|completed|partial|failed|cancelled|cancelling
    admin_id: int | None = None
    admin_name: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    progress_percent: float = 0.0

    class Config:
        from_attributes = True


class BroadcastListResponse(BaseModel):
    """Paginated list of broadcasts."""

    items: list[BroadcastResponse]
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
