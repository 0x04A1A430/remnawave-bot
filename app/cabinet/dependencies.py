"""FastAPI dependencies for cabinet module."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database.database import AsyncSessionLocal
from app.database.models import User
from app.database.crud.user import get_user_by_id
from app.config import settings
from .auth.jwt_handler import get_token_payload

security = HTTPBearer(auto_error=False)


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
