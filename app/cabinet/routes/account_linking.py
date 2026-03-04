"""Account linking and merge routes for cabinet.

Router 1 (`router`): JWT-protected endpoints for linking/unlinking OAuth providers.
Router 2 (`merge_router`): Public endpoints for merge preview and execution.
"""

from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.user import (
    get_user_by_id,
    get_user_by_oauth_provider,
    get_user_by_telegram_id,
    set_user_oauth_provider_id,
)
from app.database.models import User
from app.services.account_merge_service import compute_auth_methods, execute_merge, get_merge_preview

from ..auth.merge_service import (
    MERGE_TOKEN_TTL_SECONDS,
    consume_merge_token,
    create_merge_token,
    get_merge_token_data,
    restore_merge_token,
)
from ..auth.oauth_providers import (
    generate_oauth_state,
    get_provider,
    validate_oauth_state,
)
from ..auth.telegram_auth import validate_telegram_init_data, validate_telegram_login_widget
from ..dependencies import get_cabinet_db, get_current_cabinet_user
from ..schemas.auth import UserResponse
from .auth import _create_auth_response, _store_refresh_token, _user_to_response


logger = structlog.get_logger(__name__)

# OAuth provider -> User model column name
_OAUTH_PROVIDER_COLUMNS: dict[str, str] = {
    'google': 'google_id',
    'yandex': 'yandex_id',
    'discord': 'discord_id',
    'vk': 'vk_id',
}

# All known auth providers (including non-OAuth)
_ALL_PROVIDERS: tuple[str, ...] = ('telegram', 'email', 'google', 'yandex', 'discord', 'vk')


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LinkedProvider(BaseModel):
    provider: str
    linked: bool
    identifier: str | None = None


class LinkedProvidersResponse(BaseModel):
    providers: list[LinkedProvider]


class LinkInitResponse(BaseModel):
    authorize_url: str
    state: str


class LinkCallbackRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=2048, description='Authorization code from provider')
    state: str = Field(..., min_length=1, max_length=128, description='CSRF state token')
    device_id: str | None = Field(None, max_length=256, description='Device ID from VK ID callback')


class LinkCallbackResponse(BaseModel):
    success: bool
    message: str | None = None
    merge_required: bool = False
    merge_token: str | None = None


class UnlinkResponse(BaseModel):
    success: bool


class LinkTelegramRequest(BaseModel):
    """Request for linking Telegram account. Supply EITHER init_data OR widget fields."""

    # Mini App: Telegram WebApp initData
    init_data: str | None = Field(None, max_length=4096, description='Telegram WebApp initData string')
    # Login Widget fields
    id: int | None = Field(None, description='Telegram user ID from Login Widget')
    first_name: str | None = Field(None, max_length=256, description="User's first name")
    last_name: str | None = Field(None, max_length=256, description="User's last name")
    username: str | None = Field(None, max_length=256, description="User's username")
    photo_url: str | None = Field(None, max_length=2048, description="User's photo URL")
    auth_date: int | None = Field(None, description='Unix timestamp of authentication')
    hash: str | None = Field(None, min_length=64, max_length=64, description='Authentication hash (SHA-256 hex)')

    @model_validator(mode='after')
    def check_exclusive(self) -> 'LinkTelegramRequest':
        has_init = self.init_data is not None
        has_widget = self.id is not None or self.hash is not None or self.auth_date is not None
        if has_init and has_widget:
            raise ValueError('Provide either init_data or Login Widget fields, not both')
        if not has_init and not has_widget:
            raise ValueError('Provide either init_data or Login Widget fields (id, auth_date, hash)')
        return self


class MergePreviewSubscription(BaseModel):
    status: str
    is_trial: bool
    end_date: datetime | None = None
    traffic_limit_gb: float
    traffic_used_gb: float
    device_limit: int
    tariff_name: str | None = None
    autopay_enabled: bool


class MergePreviewUser(BaseModel):
    id: int
    username: str | None = None
    first_name: str | None = None
    email: str | None = None
    auth_methods: list[str]
    balance_kopeks: int = 0
    subscription: MergePreviewSubscription | None = None
    created_at: datetime | None = None


class MergePreviewResponse(BaseModel):
    primary: MergePreviewUser
    secondary: MergePreviewUser
    expires_in_seconds: int


class MergeRequest(BaseModel):
    keep_subscription_from: int = Field(..., description='User ID whose subscription to keep')


class MergeResponse(BaseModel):
    success: bool
    access_token: str | None = None
    refresh_token: str | None = None
    user: UserResponse | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_provider_identifier(user: User, provider: str) -> str | None:
    """Return the identifier (provider_id or email) for a given provider, or None."""
    match provider:
        case 'telegram':
            return str(user.telegram_id) if user.telegram_id else None
        case 'email':
            return user.email if user.email and user.password_hash else None
        case _:
            column = _OAUTH_PROVIDER_COLUMNS.get(provider)
            if not column:
                return None
            value = getattr(user, column, None)
            return str(value) if value else None


def _count_auth_methods(user: User) -> int:
    """Count how many auth methods the user has linked."""
    return len(compute_auth_methods(user))


# ---------------------------------------------------------------------------
# Router 1: Account linking (JWT required)
# ---------------------------------------------------------------------------

router = APIRouter(prefix='/auth/account', tags=['Cabinet Account Linking'])


@router.get('/linked-providers', response_model=LinkedProvidersResponse)
async def get_linked_providers(
    user: User = Depends(get_current_cabinet_user),
) -> LinkedProvidersResponse:
    """Return all auth methods with their link status for the current user."""
    providers: list[LinkedProvider] = []
    for provider in _ALL_PROVIDERS:
        identifier = _get_provider_identifier(user, provider)
        providers.append(
            LinkedProvider(
                provider=provider,
                linked=identifier is not None,
                identifier=identifier,
            )
        )
    return LinkedProvidersResponse(providers=providers)


@router.get('/link/{provider}/init', response_model=LinkInitResponse)
async def link_provider_init(
    provider: str,
    user: User = Depends(get_current_cabinet_user),
) -> LinkInitResponse:
    """Start OAuth flow for linking a new provider to the current account."""
    if provider not in _OAUTH_PROVIDER_COLUMNS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Only OAuth providers can be linked via this endpoint',
        )

    # Check if already linked
    column = _OAUTH_PROVIDER_COLUMNS[provider]
    if getattr(user, column, None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Provider is already linked to your account',
        )

    oauth_provider = get_provider(provider)
    if not oauth_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Requested OAuth provider is not available',
        )

    # Generate PKCE data for VK (and potentially future providers)
    auth_extra = oauth_provider.prepare_auth_state()
    extra_data: dict[str, str] = {
        'linking': 'true',
        'user_id': str(user.id),
    }
    if auth_extra:
        extra_data.update(auth_extra)

    state = await generate_oauth_state(provider, extra_data=extra_data)
    authorize_url = oauth_provider.get_authorization_url(state, **auth_extra)

    return LinkInitResponse(authorize_url=authorize_url, state=state)


@router.post('/link/{provider}/callback', response_model=LinkCallbackResponse)
async def link_provider_callback(
    provider: str,
    request: LinkCallbackRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> LinkCallbackResponse:
    """Handle OAuth callback for linking a provider to the current account."""
    if provider not in _OAUTH_PROVIDER_COLUMNS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Only OAuth providers can be linked via this endpoint',
        )

    # 1. Validate CSRF state
    state_data = await validate_oauth_state(request.state, provider)
    if not state_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid or expired OAuth state',
        )

    # 1b. Validate that this state was created for account linking (not login)
    if state_data.get('linking') != 'true' or not state_data.get('user_id'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='OAuth state was not initiated for account linking',
        )

    # 1c. Validate that the user who initiated the link flow is the same user completing it
    state_user_id = state_data['user_id']
    if str(user.id) != state_user_id:
        logger.warning(
            'OAuth state user_id mismatch in link callback',
            state_user_id=state_user_id,
            current_user_id=user.id,
            provider=provider,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='OAuth state was initiated by a different user',
        )

    # 2. Get provider instance
    oauth_provider = get_provider(provider)
    if not oauth_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Requested OAuth provider is not available',
        )

    # 3. Exchange code for tokens
    exchange_kwargs: dict[str, str] = {'state': request.state}
    code_verifier = state_data.get('code_verifier')
    if code_verifier:
        exchange_kwargs['code_verifier'] = code_verifier
    if request.device_id:
        exchange_kwargs['device_id'] = request.device_id

    try:
        token_data = await oauth_provider.exchange_code(request.code, **exchange_kwargs)
    except Exception as exc:
        logger.error('OAuth code exchange failed during linking', provider=provider, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Failed to exchange authorization code',
        ) from exc

    # 4. Fetch user info from provider
    try:
        user_info = await oauth_provider.get_user_info(token_data)
    except Exception as exc:
        logger.error('OAuth user info fetch failed during linking', provider=provider, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Failed to fetch user information from provider',
        ) from exc

    # 5. Check if provider_id is already linked to THIS user
    column = _OAUTH_PROVIDER_COLUMNS[provider]
    current_value = getattr(user, column, None)
    if current_value and str(current_value) == user_info.provider_id:
        return LinkCallbackResponse(success=True, message='already_linked')

    # 6. Check if provider_id is linked to ANOTHER user
    existing_user = await get_user_by_oauth_provider(db, provider, user_info.provider_id)
    if existing_user and existing_user.id != user.id:
        # Account conflict -> create merge token
        logger.info(
            'Account linking conflict: provider already linked to another user',
            provider=provider,
            provider_id=user_info.provider_id,
            current_user_id=user.id,
            existing_user_id=existing_user.id,
        )
        merge_token = await create_merge_token(
            primary_user_id=user.id,
            secondary_user_id=existing_user.id,
            provider=provider,
            provider_id=user_info.provider_id,
        )
        return LinkCallbackResponse(
            success=False,
            merge_required=True,
            merge_token=merge_token,
        )

    # 7. Link the provider to current user
    await set_user_oauth_provider_id(db, user, provider, user_info.provider_id)
    await db.commit()

    logger.info(
        'OAuth provider linked to account',
        provider=provider,
        provider_id=user_info.provider_id,
        user_id=user.id,
    )
    return LinkCallbackResponse(success=True, message='linked')


@router.post('/unlink/{provider}', response_model=UnlinkResponse)
async def unlink_provider(
    provider: str,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> UnlinkResponse:
    """Unlink an OAuth provider from the current account."""
    if provider not in _OAUTH_PROVIDER_COLUMNS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Only OAuth providers can be unlinked via this endpoint',
        )

    column = _OAUTH_PROVIDER_COLUMNS[provider]
    if not getattr(user, column, None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Provider is not linked to your account',
        )

    # Ensure at least one auth method remains
    if _count_auth_methods(user) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Cannot unlink last authentication method',
        )

    setattr(user, column, None)
    user.updated_at = datetime.now(UTC)
    await db.commit()

    logger.info('OAuth provider unlinked from account', provider=provider, user_id=user.id)
    return UnlinkResponse(success=True)


@router.post('/link/telegram', response_model=LinkCallbackResponse)
async def link_telegram(
    request: LinkTelegramRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> LinkCallbackResponse:
    """Link Telegram account via WebApp initData or Login Widget."""
    # 1. Already has Telegram linked?
    if user.telegram_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Telegram is already linked to your account',
        )

    # 2. Validate and extract telegram_id
    telegram_id: int | None = None
    telegram_username: str | None = None
    telegram_first_name: str | None = None
    telegram_last_name: str | None = None

    if request.init_data:
        # Mini App flow: validate initData
        user_data = validate_telegram_init_data(request.init_data)
        if not user_data or not user_data.get('id'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Invalid or expired Telegram initData',
            )
        telegram_id = int(user_data['id'])
        telegram_username = user_data.get('username')
        telegram_first_name = user_data.get('first_name')
        telegram_last_name = user_data.get('last_name')
    elif request.id is not None and request.hash is not None and request.auth_date is not None:
        # Login Widget flow: validate widget hash
        widget_data = {
            'id': request.id,
            'auth_date': request.auth_date,
            'hash': request.hash,
        }
        if request.first_name is not None:
            widget_data['first_name'] = request.first_name
        if request.last_name is not None:
            widget_data['last_name'] = request.last_name
        if request.username is not None:
            widget_data['username'] = request.username
        if request.photo_url is not None:
            widget_data['photo_url'] = request.photo_url

        if not validate_telegram_login_widget(widget_data):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Invalid or expired Telegram Login Widget data',
            )
        telegram_id = request.id
        telegram_username = request.username
        telegram_first_name = request.first_name
        telegram_last_name = request.last_name
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Provide either init_data (Mini App) or Login Widget fields (id, auth_date, hash)',
        )

    # 3. Check if telegram_id is linked to ANOTHER user
    existing_user = await get_user_by_telegram_id(db, telegram_id)
    if existing_user and existing_user.id != user.id:
        logger.info(
            'Telegram linking conflict: telegram_id already linked to another user',
            telegram_id=telegram_id,
            current_user_id=user.id,
            existing_user_id=existing_user.id,
        )
        merge_token = await create_merge_token(
            primary_user_id=user.id,
            secondary_user_id=existing_user.id,
            provider='telegram',
            provider_id=str(telegram_id),
        )
        return LinkCallbackResponse(
            success=False,
            merge_required=True,
            merge_token=merge_token,
        )

    # 4. Link Telegram to current user
    user.telegram_id = telegram_id
    if telegram_username and not user.username:
        user.username = telegram_username
    if telegram_first_name and not user.first_name:
        user.first_name = telegram_first_name
    if telegram_last_name and not user.last_name:
        user.last_name = telegram_last_name
    user.updated_at = datetime.now(UTC)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='This Telegram account was just linked to another user',
        )

    logger.info(
        'Telegram linked to account',
        telegram_id=telegram_id,
        user_id=user.id,
    )
    return LinkCallbackResponse(success=True, message='linked')


# ---------------------------------------------------------------------------
# Router 2: Merge (NO JWT required)
# ---------------------------------------------------------------------------

merge_router = APIRouter(prefix='/auth/merge', tags=['Cabinet Account Merge'])


@merge_router.get('/{merge_token}', response_model=MergePreviewResponse)
async def get_merge_preview_endpoint(
    merge_token: str = Path(..., min_length=32, max_length=64),
    db: AsyncSession = Depends(get_cabinet_db),
) -> MergePreviewResponse:
    """Preview the result of merging two accounts before confirming."""
    token_data = await get_merge_token_data(merge_token)
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Merge token is invalid or expired',
        )

    primary_user_id: int = token_data['primary_user_id']
    secondary_user_id: int = token_data['secondary_user_id']

    try:
        preview = await get_merge_preview(db, primary_user_id, secondary_user_id)
    except ValueError as exc:
        logger.error('Merge preview failed', error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='One or both users not found',
        ) from exc

    # Calculate remaining TTL
    created_at_str: str = token_data.get('created_at', '')
    try:
        created_at = datetime.fromisoformat(created_at_str)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - created_at).total_seconds()
        expires_in_seconds = max(0, int(MERGE_TOKEN_TTL_SECONDS - elapsed))
    except (ValueError, TypeError):
        expires_in_seconds = 0

    return MergePreviewResponse(
        primary=MergePreviewUser(**preview['primary']),
        secondary=MergePreviewUser(**preview['secondary']),
        expires_in_seconds=expires_in_seconds,
    )


@merge_router.post('/{merge_token}', response_model=MergeResponse)
async def execute_merge_endpoint(
    request: MergeRequest,
    merge_token: str = Path(..., min_length=32, max_length=64),
    db: AsyncSession = Depends(get_cabinet_db),
) -> MergeResponse:
    """Execute account merge. Consumes the merge token (one-time use)."""
    # 1. Consume token atomically first (GETDEL — one-time use, no TOCTOU)
    consumed = await consume_merge_token(merge_token)
    if not consumed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Merge token is invalid, expired, or already consumed',
        )

    primary_user_id: int = consumed['primary_user_id']
    secondary_user_id: int = consumed['secondary_user_id']
    provider: str = consumed.get('provider', '')
    provider_id: str = consumed.get('provider_id', '')

    # 2. Validate keep_subscription_from — restore token if invalid
    if request.keep_subscription_from not in (primary_user_id, secondary_user_id):
        await restore_merge_token(merge_token, consumed)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='keep_subscription_from must be one of the two user IDs being merged',
        )

    # Convert user_id to 'primary'/'secondary' string for execute_merge()
    keep_from: Literal['primary', 'secondary'] = 'primary' if request.keep_subscription_from == primary_user_id else 'secondary'

    # 4. Execute merge
    try:
        merged_user = await execute_merge(
            db=db,
            primary_user_id=primary_user_id,
            secondary_user_id=secondary_user_id,
            keep_subscription_from=keep_from,
            provider=provider,
            provider_id=provider_id,
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        await restore_merge_token(merge_token, consumed)
        logger.error('Merge execution failed (ValueError)', error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Account merge cannot be completed. The accounts may have already been merged or deleted.',
        ) from exc
    except Exception as exc:
        await db.rollback()
        await restore_merge_token(merge_token, consumed)
        logger.error('Merge execution failed', exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Account merge failed due to an internal error',
        ) from exc

    # 5. Re-fetch merged user with full relationships for auth response
    merged_user = await get_user_by_id(db, primary_user_id)
    if not merged_user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to load merged user',
        )

    # 6. Create auth tokens for the merged user
    try:
        auth_response = await _create_auth_response(merged_user, db)
        await _store_refresh_token(db, merged_user.id, auth_response.refresh_token, device_info='merge')
    except Exception as exc:
        logger.error('Failed to create auth tokens after merge', exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Merge succeeded but failed to create new session',
        ) from exc

    logger.info(
        'Account merge completed successfully',
        primary_user_id=primary_user_id,
        secondary_user_id=secondary_user_id,
        provider=provider,
    )

    return MergeResponse(
        success=True,
        access_token=auth_response.access_token,
        refresh_token=auth_response.refresh_token,
        user=_user_to_response(merged_user),
    )
