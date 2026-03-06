"""Admin routes for landing page management in cabinet."""

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.cabinet.utils.locale import (
    ensure_locale_dict,
    validate_locale_dict,
)
from app.database.crud.landing import (
    create_landing,
    delete_landing,
    get_all_landing_purchase_stats,
    get_all_landings,
    get_landing_by_id,
    get_landing_by_slug,
    update_landing,
    update_landing_order,
)
from app.database.models import User

from ..dependencies import get_cabinet_db, require_permission


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/landings', tags=['Cabinet Admin Landings'])

# Slugs that conflict with public landing router path segments
_RESERVED_SLUGS = frozenset(
    {
        'purchase',
        'admin',
        'api',
        'health',
        'static',
        'assets',
        'favicon',
        'robots',
        'sitemap',
        'well-known',
    }
)


# ============ Schemas ============


class LandingFeatureInput(BaseModel):
    icon: str = Field(default='', max_length=100)
    title: dict[str, str] = Field(default_factory=dict)
    description: dict[str, str] = Field(default_factory=dict)

    @field_validator('title', 'description', mode='before')
    @classmethod
    def coerce_to_dict(cls, v: dict[str, str] | str | None) -> dict[str, str]:
        return ensure_locale_dict(v)

    @field_validator('title')
    @classmethod
    def validate_title_length(cls, v: dict[str, str]) -> dict[str, str]:
        return validate_locale_dict(v, max_length=200, field_name='feature.title')

    @field_validator('description')
    @classmethod
    def validate_description_length(cls, v: dict[str, str]) -> dict[str, str]:
        return validate_locale_dict(v, max_length=500, field_name='feature.description')


class LandingPaymentMethodInput(BaseModel):
    method_id: str
    display_name: str
    description: str | None = None
    icon_url: str | None = None
    sort_order: int = 0

    @field_validator('icon_url')
    @classmethod
    def validate_icon_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.startswith(('https://', '/')):
            raise ValueError('icon_url must use HTTPS or be a relative path')
        return v


class LandingCreateRequest(BaseModel):
    slug: str = Field(pattern=r'^[a-z0-9\-]+$', min_length=1, max_length=100)
    title: dict[str, str] = Field(default_factory=lambda: {'ru': ''})
    subtitle: dict[str, str] | None = None
    is_active: bool = True
    features: list[LandingFeatureInput] = Field(default_factory=list, max_length=20)
    footer_text: dict[str, str] | None = None
    allowed_tariff_ids: list[int] = Field(default_factory=list)
    allowed_periods: dict[str, list[int]] = Field(default_factory=dict)
    payment_methods: list[LandingPaymentMethodInput] = Field(default_factory=list, max_length=10)
    gift_enabled: bool = True
    custom_css: str | None = Field(default=None, max_length=10000)
    meta_title: dict[str, str] | None = None
    meta_description: dict[str, str] | None = None

    @field_validator('title', 'subtitle', 'footer_text', 'meta_title', 'meta_description', mode='before')
    @classmethod
    def coerce_text_to_dict(cls, v: dict[str, str] | str | None) -> dict[str, str] | None:
        if v is None:
            return None
        return ensure_locale_dict(v)

    @field_validator('title')
    @classmethod
    def validate_title(cls, v: dict[str, str]) -> dict[str, str]:
        return validate_locale_dict(v, max_length=500, field_name='title')

    @field_validator('subtitle')
    @classmethod
    def validate_subtitle(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        return validate_locale_dict(v, max_length=1000, field_name='subtitle')

    @field_validator('footer_text')
    @classmethod
    def validate_footer_text(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        return validate_locale_dict(v, max_length=5000, field_name='footer_text')

    @field_validator('meta_title')
    @classmethod
    def validate_meta_title(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        return validate_locale_dict(v, max_length=200, field_name='meta_title')

    @field_validator('meta_description')
    @classmethod
    def validate_meta_description(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        return validate_locale_dict(v, max_length=500, field_name='meta_description')


class LandingUpdateRequest(BaseModel):
    slug: str | None = Field(default=None, pattern=r'^[a-z0-9\-]+$', min_length=1, max_length=100)
    title: dict[str, str] | None = None
    subtitle: dict[str, str] | None = None
    is_active: bool | None = None
    features: list[LandingFeatureInput] | None = Field(default=None, max_length=20)
    footer_text: dict[str, str] | None = None
    allowed_tariff_ids: list[int] | None = None
    allowed_periods: dict[str, list[int]] | None = None
    payment_methods: list[LandingPaymentMethodInput] | None = Field(default=None, max_length=10)
    gift_enabled: bool | None = None
    custom_css: str | None = Field(default=None, max_length=10000)
    meta_title: dict[str, str] | None = None
    meta_description: dict[str, str] | None = None

    @field_validator('title', 'subtitle', 'footer_text', 'meta_title', 'meta_description', mode='before')
    @classmethod
    def coerce_text_to_dict(cls, v: dict[str, str] | str | None) -> dict[str, str] | None:
        if v is None:
            return None
        return ensure_locale_dict(v)

    @field_validator('title')
    @classmethod
    def validate_title(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        return validate_locale_dict(v, max_length=500, field_name='title')

    @field_validator('subtitle')
    @classmethod
    def validate_subtitle(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        return validate_locale_dict(v, max_length=1000, field_name='subtitle')

    @field_validator('footer_text')
    @classmethod
    def validate_footer_text(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        return validate_locale_dict(v, max_length=5000, field_name='footer_text')

    @field_validator('meta_title')
    @classmethod
    def validate_meta_title(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        return validate_locale_dict(v, max_length=200, field_name='meta_title')

    @field_validator('meta_description')
    @classmethod
    def validate_meta_description(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        return validate_locale_dict(v, max_length=500, field_name='meta_description')


class PurchaseStats(BaseModel):
    total: int = 0
    pending: int = 0
    paid: int = 0
    delivered: int = 0
    failed: int = 0
    expired: int = 0


class LandingListItem(BaseModel):
    id: int
    slug: str
    title: dict[str, str]
    is_active: bool
    display_order: int
    gift_enabled: bool
    tariff_count: int
    method_count: int
    purchase_stats: PurchaseStats
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator('title', mode='before')
    @classmethod
    def coerce_title(cls, v: dict[str, str] | str | None) -> dict[str, str]:
        return ensure_locale_dict(v)

    class Config:
        from_attributes = True


class LandingDetailResponse(BaseModel):
    id: int
    slug: str
    title: dict[str, str]
    subtitle: dict[str, str] | None = None
    is_active: bool
    display_order: int
    features: list[LandingFeatureInput]
    footer_text: dict[str, str] | None = None
    allowed_tariff_ids: list[int]
    allowed_periods: dict[str, list[int]]
    payment_methods: list[LandingPaymentMethodInput]
    gift_enabled: bool
    custom_css: str | None = None
    meta_title: dict[str, str] | None = None
    meta_description: dict[str, str] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator('title', 'subtitle', 'footer_text', 'meta_title', 'meta_description', mode='before')
    @classmethod
    def coerce_to_dict(cls, v: dict[str, str] | str | None) -> dict[str, str] | None:
        if v is None:
            return None
        return ensure_locale_dict(v)

    class Config:
        from_attributes = True


class OrderRequest(BaseModel):
    landing_ids: list[int]


# ============ Routes ============

# IMPORTANT: /order MUST come before /{landing_id} to avoid "order" being
# parsed as a landing_id path parameter.


@router.get('', response_model=list[LandingListItem])
async def list_landings(
    admin: User = Depends(require_permission('landings:read')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """List all landing pages with purchase stats."""
    landings = await get_all_landings(db)
    all_stats = await get_all_landing_purchase_stats(db)
    empty_stats = {'total': 0, 'pending': 0, 'paid': 0, 'delivered': 0, 'failed': 0, 'expired': 0}

    items = []
    for landing in landings:
        stats = all_stats.get(landing.id, empty_stats)
        items.append(
            LandingListItem(
                id=landing.id,
                slug=landing.slug,
                title=landing.title,
                is_active=landing.is_active,
                display_order=landing.display_order,
                gift_enabled=landing.gift_enabled,
                tariff_count=len(landing.allowed_tariff_ids or []),
                method_count=len(landing.payment_methods or []),
                purchase_stats=PurchaseStats(
                    total=stats.get('total', 0),
                    pending=stats.get('pending', 0),
                    paid=stats.get('paid', 0),
                    delivered=stats.get('delivered', 0),
                    failed=stats.get('failed', 0),
                    expired=stats.get('expired', 0),
                ),
                created_at=landing.created_at,
                updated_at=landing.updated_at,
            )
        )
    return items


@router.post('', response_model=LandingDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_landing_page(
    request: LandingCreateRequest,
    admin: User = Depends(require_permission('landings:create')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Create a new landing page."""
    if request.slug in _RESERVED_SLUGS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Slug "{request.slug}" is reserved and cannot be used',
        )

    existing = await get_landing_by_slug(db, request.slug)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'Landing page with slug "{request.slug}" already exists',
        )

    landing = await create_landing(
        db,
        slug=request.slug,
        title=request.title,
        subtitle=request.subtitle,
        is_active=request.is_active,
        features=[f.model_dump() for f in request.features],
        footer_text=request.footer_text,
        allowed_tariff_ids=request.allowed_tariff_ids,
        allowed_periods=request.allowed_periods,
        payment_methods=[m.model_dump() for m in request.payment_methods],
        gift_enabled=request.gift_enabled,
        custom_css=request.custom_css,
        meta_title=request.meta_title,
        meta_description=request.meta_description,
    )

    logger.info('Admin created landing page', admin_id=admin.id, slug=landing.slug, landing_id=landing.id)

    return _landing_to_detail(landing)


@router.put('/order')
async def update_landings_order(
    request: OrderRequest,
    admin: User = Depends(require_permission('landings:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Batch update display order for landing pages."""
    await update_landing_order(db, request.landing_ids)
    logger.info('Admin updated landing page order', admin_id=admin.id, landing_ids=request.landing_ids)
    return {'success': True}


@router.get('/{landing_id}', response_model=LandingDetailResponse)
async def get_landing_detail(
    landing_id: int,
    admin: User = Depends(require_permission('landings:read')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get a single landing page with full details."""
    landing = await get_landing_by_id(db, landing_id)
    if landing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Landing page not found',
        )
    return _landing_to_detail(landing)


@router.put('/{landing_id}', response_model=LandingDetailResponse)
async def update_landing_page(
    landing_id: int,
    request: LandingUpdateRequest,
    admin: User = Depends(require_permission('landings:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update a landing page."""
    data = request.model_dump(exclude_unset=True)

    # If slug is being changed, check reserved and uniqueness
    if 'slug' in data:
        if data['slug'] in _RESERVED_SLUGS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Slug "{data["slug"]}" is reserved and cannot be used',
            )
        existing = await get_landing_by_slug(db, data['slug'])
        if existing is not None and existing.id != landing_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'Landing page with slug "{data["slug"]}" already exists',
            )

    # Serialize nested Pydantic models to dicts for JSON storage
    if 'features' in data and data['features'] is not None:
        data['features'] = [f.model_dump() if hasattr(f, 'model_dump') else f for f in data['features']]
    if 'payment_methods' in data and data['payment_methods'] is not None:
        data['payment_methods'] = [m.model_dump() if hasattr(m, 'model_dump') else m for m in data['payment_methods']]

    landing = await update_landing(db, landing_id, data)
    if landing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Landing page not found',
        )

    logger.info('Admin updated landing page', admin_id=admin.id, slug=landing.slug, landing_id=landing.id)

    return _landing_to_detail(landing)


@router.delete('/{landing_id}')
async def delete_landing_page(
    landing_id: int,
    admin: User = Depends(require_permission('landings:delete')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Delete a landing page."""
    deleted = await delete_landing(db, landing_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Landing page not found',
        )
    logger.info('Admin deleted landing page', admin_id=admin.id, landing_id=landing_id)
    return {'success': True}


@router.post('/{landing_id}/toggle', response_model=LandingDetailResponse)
async def toggle_landing_active(
    landing_id: int,
    admin: User = Depends(require_permission('landings:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Toggle active/inactive state of a landing page."""
    landing = await get_landing_by_id(db, landing_id)
    if landing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Landing page not found',
        )

    new_active = not landing.is_active
    landing = await update_landing(db, landing_id, {'is_active': new_active})

    logger.info(
        'Admin toggled landing page',
        admin_id=admin.id,
        landing_id=landing_id,
        is_active=new_active,
    )

    return _landing_to_detail(landing)


# ============ Helpers ============


def _landing_to_detail(landing) -> LandingDetailResponse:
    """Convert a LandingPage model to LandingDetailResponse.

    Admin detail view returns full locale dicts for all text fields.
    """
    features = [
        LandingFeatureInput(
            icon=f.get('icon', ''),
            title=f.get('title', {}),
            description=f.get('description', {}),
        )
        for f in (landing.features or [])
    ]

    payment_methods = [
        LandingPaymentMethodInput(
            method_id=m.get('method_id', ''),
            display_name=m.get('display_name', ''),
            description=m.get('description'),
            icon_url=m.get('icon_url'),
            sort_order=m.get('sort_order', 0),
        )
        for m in (landing.payment_methods or [])
    ]

    return LandingDetailResponse(
        id=landing.id,
        slug=landing.slug,
        title=landing.title or {},
        subtitle=landing.subtitle,
        is_active=landing.is_active,
        display_order=landing.display_order,
        features=features,
        footer_text=landing.footer_text,
        allowed_tariff_ids=landing.allowed_tariff_ids or [],
        allowed_periods=landing.allowed_periods or {},
        payment_methods=payment_methods,
        gift_enabled=landing.gift_enabled,
        custom_css=landing.custom_css,
        meta_title=landing.meta_title,
        meta_description=landing.meta_description,
        created_at=landing.created_at,
        updated_at=landing.updated_at,
    )
