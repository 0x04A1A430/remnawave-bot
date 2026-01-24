"""CRUD операции для токенов административного веб-API."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import WebApiToken


async def list_tokens(
    db: AsyncSession,
    *,
    include_inactive: bool = False,
) -> list[WebApiToken]:
    query = select(WebApiToken)

    if not include_inactive:
        query = query.where(WebApiToken.is_active.is_(True))

    query = query.order_by(WebApiToken.created_at.desc())

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_token_by_id(db: AsyncSession, token_id: int) -> WebApiToken | None:
    return await db.get(WebApiToken, token_id)


async def get_token_by_hash(db: AsyncSession, token_hash: str) -> WebApiToken | None:
    query = select(WebApiToken).where(WebApiToken.token_hash == token_hash)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def create_token(
    db: AsyncSession,
    *,
    name: str,
    token_hash: str,
    token_prefix: str,
    description: str | None = None,
    expires_at: datetime | None = None,
    created_by: str | None = None,
) -> WebApiToken:
    token = WebApiToken(
        name=name,
        token_hash=token_hash,
        token_prefix=token_prefix,
        description=description,
        expires_at=expires_at,
        created_by=created_by,
        is_active=True,
    )

    db.add(token)
    await db.flush()
    await db.refresh(token)
    return token


async def update_token(
    db: AsyncSession,
    token: WebApiToken,
    **kwargs,
) -> WebApiToken:
    for key, value in kwargs.items():
        if hasattr(token, key):
            setattr(token, key, value)
    token.updated_at = datetime.utcnow()
    await db.flush()
    await db.refresh(token)
    return token


async def set_tokens_active_status(
    db: AsyncSession,
    token_ids: Iterable[int],
    *,
    is_active: bool,
) -> None:
    await db.execute(
        update(WebApiToken)
        .where(WebApiToken.id.in_(list(token_ids)))
        .values(is_active=is_active, updated_at=datetime.utcnow())
    )


async def delete_token(db: AsyncSession, token: WebApiToken) -> None:
    await db.delete(token)


__all__ = [
    'create_token',
    'delete_token',
    'get_token_by_hash',
    'get_token_by_id',
    'list_tokens',
    'set_tokens_active_status',
    'update_token',
]
