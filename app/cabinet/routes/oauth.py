"""OAuth 2.0 authentication routes for cabinet."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.user import (
    create_user_by_oauth,
    get_user_by_email,
    get_user_by_oauth_provider,
    set_user_oauth_provider_id,
)
from app.database.models import User

from ..auth import create_access_token, create_refresh_token
from ..auth.jwt_handler import get_refresh_token_expires_at
from ..auth.oauth_providers import (
    OAuthUserInfo,
    generate_oauth_state,
    get_provider,
    validate_oauth_state,
)
from ..dependencies import get_cabinet_db
from ..schemas.auth import AuthResponse, UserResponse


logger = logging.getLogger(__name__)

router = APIRouter(prefix='/auth/oauth', tags=['Cabinet OAuth'])


# --- Schemas ---


class OAuthProviderInfo(BaseModel):
    name: str
    display_name: str


class OAuthProvidersResponse(BaseModel):
    providers: list[OAuthProviderInfo]


class OAuthAuthorizeResponse(BaseModel):
    authorize_url: str
    state: str


class OAuthCallbackRequest(BaseModel):
    code: str = Field(..., description='Authorization code from provider')
    state: str = Field(..., description='CSRF state token')


# --- Helpers ---


def _user_to_response(user: User) -> UserResponse:
    """Convert User model to UserResponse."""
    return UserResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        email_verified=user.email_verified,
        balance_kopeks=user.balance_kopeks,
        balance_rubles=user.balance_rubles,
        referral_code=user.referral_code,
        language=user.language,
        created_at=user.created_at,
        auth_type=getattr(user, 'auth_type', 'telegram'),
    )


def _create_auth_response(user: User) -> AuthResponse:
    """Create full auth response with tokens."""
    access_token = create_access_token(user.id, user.telegram_id)
    refresh_token = create_refresh_token(user.id)
    expires_in = settings.get_cabinet_access_token_expire_minutes() * 60

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type='bearer',
        expires_in=expires_in,
        user=_user_to_response(user),
    )


async def _store_refresh_token(
    db: AsyncSession,
    user_id: int,
    refresh_token: str,
    device_info: str | None = None,
) -> None:
    """Store refresh token hash in database."""
    import hashlib

    from app.database.models import CabinetRefreshToken

    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    expires_at = get_refresh_token_expires_at()

    from sqlalchemy import select

    existing = await db.execute(select(CabinetRefreshToken).where(CabinetRefreshToken.token_hash == token_hash))
    if existing.scalar_one_or_none():
        return

    token_record = CabinetRefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        device_info=device_info,
        expires_at=expires_at,
    )
    db.add(token_record)
    try:
        await db.commit()
    except Exception:
        await db.rollback()


# --- Endpoints ---


@router.get('/providers', response_model=OAuthProvidersResponse)
async def get_oauth_providers():
    """Get list of enabled OAuth providers."""
    providers_config = settings.get_oauth_providers_config()
    providers = [
        OAuthProviderInfo(name=name, display_name=cfg['display_name'])
        for name, cfg in providers_config.items()
        if cfg['enabled']
    ]
    return OAuthProvidersResponse(providers=providers)


@router.get('/{provider}/authorize', response_model=OAuthAuthorizeResponse)
async def get_oauth_authorize_url(provider: str):
    """Get authorization URL for an OAuth provider."""
    oauth_provider = get_provider(provider)
    if not oauth_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'OAuth provider "{provider}" is not enabled',
        )

    state = await generate_oauth_state(provider)
    authorize_url = oauth_provider.get_authorization_url(state)

    return OAuthAuthorizeResponse(authorize_url=authorize_url, state=state)


@router.post('/{provider}/callback', response_model=AuthResponse)
async def oauth_callback(
    provider: str,
    request: OAuthCallbackRequest,
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Handle OAuth callback: exchange code, find/create user, return JWT."""
    # 1. Validate CSRF state
    if not await validate_oauth_state(request.state, provider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid or expired OAuth state',
        )

    # 2. Get provider instance
    oauth_provider = get_provider(provider)
    if not oauth_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'OAuth provider "{provider}" is not enabled',
        )

    # 3. Exchange code for tokens
    try:
        token_data = await oauth_provider.exchange_code(request.code)
    except Exception as exc:
        logger.error('OAuth code exchange failed for %s: %s', provider, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Failed to exchange authorization code',
        ) from exc

    # 4. Fetch user info from provider
    try:
        user_info: OAuthUserInfo = await oauth_provider.get_user_info(token_data)
    except Exception as exc:
        logger.error('OAuth user info fetch failed for %s: %s', provider, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Failed to fetch user information from provider',
        ) from exc

    # 5. Find user by provider ID
    user = await get_user_by_oauth_provider(db, provider, user_info.provider_id)
    if user:
        user.cabinet_last_login = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()
        auth_response = _create_auth_response(user)
        await _store_refresh_token(db, user.id, auth_response.refresh_token, device_info=f'oauth:{provider}')
        logger.info('OAuth login via %s for existing user %s', provider, user.id)
        return auth_response

    # 6. Find user by email (if verified) and link provider
    if user_info.email and user_info.email_verified:
        user = await get_user_by_email(db, user_info.email)
        if user:
            await set_user_oauth_provider_id(db, user, provider, user_info.provider_id)
            user.cabinet_last_login = datetime.now(UTC).replace(tzinfo=None)
            await db.commit()
            auth_response = _create_auth_response(user)
            await _store_refresh_token(db, user.id, auth_response.refresh_token, device_info=f'oauth:{provider}')
            logger.info('OAuth login via %s linked to existing email user %s', provider, user.id)
            return auth_response

    # 7. Create new user
    user = await create_user_by_oauth(
        db=db,
        provider=provider,
        provider_id=user_info.provider_id,
        email=user_info.email if user_info.email_verified else None,
        email_verified=user_info.email_verified,
        first_name=user_info.first_name,
        last_name=user_info.last_name,
        username=user_info.username,
    )
    user.cabinet_last_login = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()

    auth_response = _create_auth_response(user)
    await _store_refresh_token(db, user.id, auth_response.refresh_token, device_info=f'oauth:{provider}')
    logger.info('OAuth new user created via %s with id=%s', provider, user.id)
    return auth_response
