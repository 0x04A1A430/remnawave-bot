"""FastAPI dependencies for cabinet module."""

import asyncio

import structlog
from aiogram import Bot
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.user import get_user_by_id
from app.database.database import AsyncSessionLocal
from app.database.models import User
from app.services.blacklist_service import blacklist_service
from app.services.maintenance_service import maintenance_service

from .auth.jwt_handler import get_token_payload
from .auth.telegram_auth import validate_telegram_init_data


logger = structlog.get_logger(__name__)

security = HTTPBearer(auto_error=False)

# Кешированный Bot для проверки подписки на канал
_channel_check_bot: Bot | None = None


def _get_channel_check_bot() -> Bot:
    """Получить или создать Bot для проверки подписки на канал."""
    global _channel_check_bot
    if _channel_check_bot is None:
        _channel_check_bot = Bot(token=settings.BOT_TOKEN)
    return _channel_check_bot


async def get_cabinet_db() -> AsyncSession:
    """Get database session for cabinet operations."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_current_cabinet_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_cabinet_db),
) -> User:
    """
    Get current authenticated cabinet user from JWT token.

    Args:
        request: FastAPI request object (for reading X-Telegram-Init-Data header)
        credentials: HTTP Bearer credentials
        db: Database session

    Returns:
        Authenticated User object

    Raises:
        HTTPException: If token is invalid, expired, or user not found
    """
    # Check maintenance mode first (except for admins - checked later)
    if maintenance_service.is_maintenance_active():
        # We need to check token first to see if user is admin
        pass  # Will check after getting user

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Authentication required',
            headers={'WWW-Authenticate': 'Bearer'},
        )

    token = credentials.credentials
    payload = get_token_payload(token, expected_type='access')

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid or expired token',
            headers={'WWW-Authenticate': 'Bearer'},
        )

    try:
        user_id = int(payload.get('sub'))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid token payload',
            headers={'WWW-Authenticate': 'Bearer'},
        )

    user = await get_user_by_id(db, user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='User not found',
        )

    if user.status != 'active':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='User account is not active',
        )

    # Defense in depth: cross-validate Telegram identity.
    # The frontend sends X-Telegram-Init-Data on every request.
    # If the header is present and cryptographically valid, verify that
    # the Telegram user ID matches the JWT user's telegram_id.
    # This prevents cross-account token reuse when Telegram WebView
    # shares localStorage across accounts on the same device.
    init_data_raw = request.headers.get('X-Telegram-Init-Data')
    if init_data_raw and user.telegram_id is not None:
        # Use generous max_age: Telegram Desktop caches initData
        tg_user = validate_telegram_init_data(init_data_raw, max_age_seconds=86400 * 30)
        if tg_user is None:
            logger.warning(
                'Telegram initData validation failed but header was present',
                jwt_user_id=user.id,
            )
        elif tg_user.get('id') != user.telegram_id:
            logger.warning(
                'Telegram identity mismatch: JWT belongs to different user than current Telegram account',
                jwt_user_id=user.id,
                jwt_telegram_id=user.telegram_id,
                init_data_telegram_id=tg_user.get('id'),
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Session belongs to a different Telegram account. Please restart the app.',
                headers={'WWW-Authenticate': 'Bearer'},
            )

    # Check blacklist
    if user.telegram_id is not None:
        is_blacklisted, reason = await blacklist_service.is_user_blacklisted(user.telegram_id, user.username)
        if is_blacklisted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    'code': 'blacklisted',
                    'message': reason or 'Доступ запрещен',
                },
            )

    # Check maintenance mode (allow admins to pass)
    if maintenance_service.is_maintenance_active():
        # Проверяем админа по telegram_id ИЛИ email
        is_admin = settings.is_admin(telegram_id=user.telegram_id, email=user.email if user.email_verified else None)
        if not is_admin:
            status_info = maintenance_service.get_status_info()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    'code': 'maintenance',
                    'message': maintenance_service.get_maintenance_message() or 'Service is under maintenance',
                    'reason': status_info.get('reason'),
                },
            )

    # Check required channel subscription - ТОЛЬКО для Telegram юзеров
    if settings.CHANNEL_IS_REQUIRED_SUB and settings.CHANNEL_SUB_ID:
        # Пропускаем проверку для email-only юзеров (нет telegram_id)
        if user.telegram_id is not None:
            # Проверяем админа по telegram_id ИЛИ email
            is_admin = settings.is_admin(
                telegram_id=user.telegram_id, email=user.email if user.email_verified else None
            )
            if not is_admin:
                try:
                    bot = _get_channel_check_bot()
                    chat_member = await asyncio.wait_for(
                        bot.get_chat_member(chat_id=settings.CHANNEL_SUB_ID, user_id=user.telegram_id),
                        timeout=10.0,
                    )
                    # Не закрываем сессию - бот переиспользуется

                    if chat_member.status not in ['member', 'administrator', 'creator']:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail={
                                'code': 'channel_subscription_required',
                                'message': 'Please subscribe to our channel to continue',
                                'channel_link': settings.CHANNEL_LINK,
                            },
                        )
                except HTTPException:
                    raise
                except TimeoutError:
                    logger.warning('Timeout checking channel subscription for user', telegram_id=user.telegram_id)
                    # Don't block user if check times out
                except Exception as e:
                    logger.warning(
                        'Failed to check channel subscription for user', telegram_id=user.telegram_id, error=e
                    )
                    # Don't block user if check fails

    return user


async def get_optional_cabinet_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_cabinet_db),
) -> User | None:
    """
    Optionally get current authenticated cabinet user.

    Returns None if no valid token is provided instead of raising an exception.
    """
    if not credentials:
        return None

    token = credentials.credentials
    payload = get_token_payload(token, expected_type='access')

    if not payload:
        return None

    try:
        user_id = int(payload.get('sub'))
    except (TypeError, ValueError):
        return None

    user = await get_user_by_id(db, user_id)

    if not user or user.status != 'active':
        return None

    # Cross-validate Telegram identity (same as get_current_cabinet_user)
    init_data_raw = request.headers.get('X-Telegram-Init-Data')
    if init_data_raw and user.telegram_id is not None:
        tg_user = validate_telegram_init_data(init_data_raw, max_age_seconds=86400 * 30)
        if tg_user and tg_user.get('id') != user.telegram_id:
            logger.warning(
                'Telegram identity mismatch in optional auth',
                jwt_user_id=user.id,
                jwt_telegram_id=user.telegram_id,
                init_data_telegram_id=tg_user.get('id'),
            )
            return None

    return user


async def get_current_admin_user(
    user: User = Depends(get_current_cabinet_user),
) -> User:
    """
    Get current authenticated admin user.

    Checks if the user is admin by telegram_id or email.

    Args:
        user: Authenticated User object

    Returns:
        Authenticated admin User object

    Raises:
        HTTPException: If user is not an admin
    """
    is_admin = settings.is_admin(telegram_id=user.telegram_id, email=user.email if user.email_verified else None)
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Admin access required',
        )

    return user
