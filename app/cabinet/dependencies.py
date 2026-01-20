"""FastAPI dependencies for cabinet module."""

import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from aiogram import Bot

from app.database.database import AsyncSessionLocal
from app.database.models import User
from app.database.crud.user import get_user_by_id
from app.config import settings
from app.services.maintenance_service import maintenance_service
from .auth.jwt_handler import get_token_payload

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# Кешированный Bot для проверки подписки на канал
_channel_check_bot: Optional[Bot] = None


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
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_cabinet_db),
) -> User:
    """
    Get current authenticated cabinet user from JWT token.

    Args:
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
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = get_token_payload(token, expected_type="access")

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_user_by_id(db, user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is not active",
        )

    # Check maintenance mode (allow admins to pass)
    if maintenance_service.is_maintenance_active():
        if not settings.is_admin(user.telegram_id):
            status_info = maintenance_service.get_status_info()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "maintenance",
                    "message": maintenance_service.get_maintenance_message() or "Service is under maintenance",
                    "reason": status_info.get("reason"),
                },
            )

    # Check required channel subscription
    if settings.CHANNEL_IS_REQUIRED_SUB and settings.CHANNEL_SUB_ID:
        # Skip check for admins
        if not settings.is_admin(user.telegram_id):
            try:
                bot = _get_channel_check_bot()
                chat_member = await bot.get_chat_member(
                    chat_id=settings.CHANNEL_SUB_ID,
                    user_id=user.telegram_id
                )
                # Не закрываем сессию - бот переиспользуется

                if chat_member.status not in ["member", "administrator", "creator"]:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "code": "channel_subscription_required",
                            "message": "Please subscribe to our channel to continue",
                            "channel_link": settings.CHANNEL_LINK,
                        },
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"Failed to check channel subscription for user {user.telegram_id}: {e}")
                # Don't block user if check fails

    return user


async def get_optional_cabinet_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_cabinet_db),
) -> Optional[User]:
    """
    Optionally get current authenticated cabinet user.

    Returns None if no valid token is provided instead of raising an exception.
    """
    if not credentials:
        return None

    token = credentials.credentials
    payload = get_token_payload(token, expected_type="access")

    if not payload:
        return None

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None

    user = await get_user_by_id(db, user_id)

    if not user or user.status != "active":
        return None

    return user


async def get_current_admin_user(
    user: User = Depends(get_current_cabinet_user),
) -> User:
    """
    Get current authenticated admin user.

    Checks if the user's telegram_id is in ADMIN_IDS from settings.

    Args:
        user: Authenticated User object

    Returns:
        Authenticated admin User object

    Raises:
        HTTPException: If user is not an admin
    """
    if not settings.is_admin(user.telegram_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return user
