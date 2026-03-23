"""Schemas for news articles in cabinet."""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Cyrillic-to-Latin transliteration map for slug generation
_TRANSLIT_MAP: dict[str, str] = {
    'а': 'a',
    'б': 'b',
    'в': 'v',
    'г': 'g',
    'д': 'd',
    'е': 'e',
    'ё': 'yo',
    'ж': 'zh',
    'з': 'z',
    'и': 'i',
    'й': 'y',
    'к': 'k',
    'л': 'l',
    'м': 'm',
    'н': 'n',
    'о': 'o',
    'п': 'p',
    'р': 'r',
    'с': 's',
    'т': 't',
    'у': 'u',
    'ф': 'f',
    'х': 'kh',
    'ц': 'ts',
    'ч': 'ch',
    'ш': 'sh',
    'щ': 'shch',
    'ъ': '',
    'ы': 'y',
    'ь': '',
    'э': 'e',
    'ю': 'yu',
    'я': 'ya',
}


def _slugify(title: str) -> str:
    """Generate a URL-safe slug from a title, transliterating Cyrillic."""
    slug = title.lower()
    result: list[str] = []
    for ch in slug:
        if ch in _TRANSLIT_MAP:
            result.append(_TRANSLIT_MAP[ch])
        elif ch.isascii() and (ch.isalnum() or ch in '-_'):
            result.append(ch)
        elif ch == ' ':
            result.append('-')
    slug = ''.join(result)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug or 'untitled'


class NewsArticleResponse(BaseModel):
    """Full news article response (detail view)."""

    id: int
    title: str
    slug: str
    content: str
    excerpt: str | None
    category: str
    category_color: str
    tag: str | None
    featured_image_url: str | None
    is_published: bool
    is_featured: bool
    published_at: datetime | None
    read_time_minutes: int
    views_count: int
    author_name: str | None = None
    created_at: datetime
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class NewsArticleListItem(BaseModel):
    """Compact news article for list views."""

    id: int
    title: str
    slug: str
    excerpt: str | None
    category: str
    category_color: str
    tag: str | None
    featured_image_url: str | None
    is_published: bool
    is_featured: bool
    published_at: datetime | None
    read_time_minutes: int
    views_count: int

    model_config = ConfigDict(from_attributes=True)


class NewsListResponse(BaseModel):
    """Paginated list of news articles."""

    items: list[NewsArticleListItem]
    total: int
    categories: list[str] = Field(default_factory=list)


class NewsCreateRequest(BaseModel):
    """Request to create a news article."""

    title: str = Field(..., min_length=1, max_length=500)
    slug: str | None = Field(None, max_length=500)
    content: str = Field(default='', max_length=500_000)
    excerpt: str | None = Field(None, max_length=1000)
    category: str = Field(..., min_length=1, max_length=100)
    category_color: str = Field(default='#00e5a0', max_length=20)
    tag: str | None = Field(None, max_length=50)
    featured_image_url: str | None = Field(None, max_length=2000)
    is_published: bool = False
    is_featured: bool = False
    read_time_minutes: int = Field(default=1, ge=1, le=60)

    @field_validator('category_color')
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        if not re.match(r'^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$', v):
            raise ValueError('category_color must be a valid hex color (e.g. #00e5a0)')
        return v

    @field_validator('slug', mode='before')
    @classmethod
    def generate_slug(cls, v: str | None, info) -> str:
        if v:
            return v
        title = info.data.get('title', '')
        return _slugify(title)


class NewsUpdateRequest(BaseModel):
    """Request to update a news article."""

    title: str | None = Field(None, min_length=1, max_length=500)
    slug: str | None = Field(None, max_length=500)
    content: str | None = Field(None, max_length=500_000)
    excerpt: str | None = None
    category: str | None = Field(None, min_length=1, max_length=100)
    category_color: str | None = Field(None, max_length=20)
    tag: str | None = None
    featured_image_url: str | None = Field(None, max_length=2000)
    is_published: bool | None = None
    is_featured: bool | None = None
    read_time_minutes: int | None = Field(None, ge=1, le=60)

    @field_validator('category_color')
    @classmethod
    def validate_hex_color(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r'^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$', v):
            raise ValueError('category_color must be a valid hex color (e.g. #00e5a0)')
        return v


class NewsToggleResponse(BaseModel):
    """Response after toggling publish/featured status."""

    id: int
    is_published: bool
    is_featured: bool
    published_at: datetime | None
