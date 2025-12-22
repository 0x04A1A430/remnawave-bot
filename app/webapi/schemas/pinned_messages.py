from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PinnedMessageMedia(BaseModel):
    type: str = Field(pattern=r"^(photo|video)$")
    file_id: str


class PinnedMessageBase(BaseModel):
    content: Optional[str] = Field(None, max_length=4000)
    send_before_menu: bool = True
    send_on_every_start: bool = True


class PinnedMessageCreateRequest(PinnedMessageBase):
    content: str = Field(..., min_length=1, max_length=4000)
    media: Optional[PinnedMessageMedia] = None


class PinnedMessageUpdateRequest(BaseModel):
    content: Optional[str] = Field(None, max_length=4000)
    send_before_menu: Optional[bool] = None
    send_on_every_start: Optional[bool] = None
    media: Optional[PinnedMessageMedia] = None


class PinnedMessageSettingsRequest(BaseModel):
    send_before_menu: Optional[bool] = None
    send_on_every_start: Optional[bool] = None


class PinnedMessageResponse(BaseModel):
    id: int
    content: Optional[str]
    media_type: Optional[str] = None
    media_file_id: Optional[str] = None
    send_before_menu: bool
    send_on_every_start: bool
    is_active: bool
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class PinnedMessageBroadcastResponse(BaseModel):
    message: PinnedMessageResponse
    sent_count: int
    failed_count: int


class PinnedMessageUnpinResponse(BaseModel):
    unpinned_count: int
    failed_count: int
    was_active: bool


class PinnedMessageListResponse(BaseModel):
    items: list[PinnedMessageResponse]
    total: int
    limit: int
    offset: int
