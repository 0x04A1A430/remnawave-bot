from __future__ import annotations

from pydantic import BaseModel, Field


class MediaUploadResponse(BaseModel):
    media_type: str = Field(description='Тип загруженного файла (photo, video, document)')
    file_id: str = Field(description='Telegram file_id загруженного файла')
    file_unique_id: str | None = Field(default=None, description='Уникальный идентификатор файла')
    media_url: str | None = Field(default=None, description='Прямая ссылка на файл для предпросмотра')
