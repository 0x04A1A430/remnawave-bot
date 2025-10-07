from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import UserApiToken


async def get_token_by_user_id(
    db: AsyncSession,
    user_id: int,
) -> Optional[UserApiToken]:
    query = select(UserApiToken).where(UserApiToken.user_id == user_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_token_by_hash(
    db: AsyncSession,
    token_hash: str,
) -> Optional[UserApiToken]:
    query = select(UserApiToken).where(UserApiToken.token_hash == token_hash)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def create_token(
    db: AsyncSession,
    *,
    user_id: int,
    token_hash: str,
    token_prefix: str,
    token_last_digits: str,
) -> UserApiToken:
    token = UserApiToken(
        user_id=user_id,
        token_hash=token_hash,
        token_prefix=token_prefix,
        token_last_digits=token_last_digits,
        is_active=True,
    )
    db.add(token)
    await db.flush()
    await db.refresh(token)
    return token


async def update_token(
    db: AsyncSession,
    token: UserApiToken,
    *,
    token_hash: str,
    token_prefix: str,
    token_last_digits: str,
) -> UserApiToken:
    token.token_hash = token_hash
    token.token_prefix = token_prefix
    token.token_last_digits = token_last_digits
    token.is_active = True
    token.updated_at = datetime.utcnow()
    token.last_used_at = None
    token.last_used_ip = None
    await db.flush()
    await db.refresh(token)
    return token


async def deactivate_token(
    db: AsyncSession,
    token: UserApiToken,
) -> UserApiToken:
    token.is_active = False
    token.updated_at = datetime.utcnow()
    await db.flush()
    await db.refresh(token)
    return token


__all__ = [
    "get_token_by_user_id",
    "get_token_by_hash",
    "create_token",
    "update_token",
    "deactivate_token",
]
