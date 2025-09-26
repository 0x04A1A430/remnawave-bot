import hashlib
import secrets
from datetime import datetime
from typing import Iterable, List, Optional, Union

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ApiToken


UNSET = object()


def _normalize_iterable(values: Optional[Iterable[str]]) -> Optional[List[str]]:
    if not values:
        return None

    normalized: List[str] = []
    for raw in values:
        if raw is None:
            continue
        value = str(raw).strip()
        if not value:
            continue
        if value not in normalized:
            normalized.append(value)
    return normalized or None


TOKEN_LENGTH = 64
TOKEN_PREFIX_LENGTH = 12


def generate_token(length: int = TOKEN_LENGTH) -> str:
    """Генерирует случайный токен."""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_token_prefix(token: str, length: int = TOKEN_PREFIX_LENGTH) -> str:
    return token[:length]


async def create_api_token(
    db: AsyncSession,
    *,
    name: str,
    token: str,
    description: Optional[str] = None,
    permissions: Optional[Iterable[str]] = None,
    allowed_ips: Optional[Iterable[str]] = None,
    expires_at: Optional[datetime] = None,
) -> ApiToken:
    token_hash = hash_token(token)
    token_prefix = get_token_prefix(token)

    api_token = ApiToken(
        name=name,
        token_hash=token_hash,
        token_prefix=token_prefix,
        description=description,
        permissions=_normalize_iterable(permissions),
        allowed_ips=_normalize_iterable(allowed_ips),
        expires_at=expires_at,
    )

    db.add(api_token)
    await db.commit()
    await db.refresh(api_token)
    return api_token


async def list_api_tokens(db: AsyncSession, *, active_only: bool = False) -> List[ApiToken]:
    stmt = select(ApiToken)
    if active_only:
        stmt = stmt.where(ApiToken.is_active.is_(True))
    stmt = stmt.order_by(ApiToken.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_api_token_by_hash(db: AsyncSession, token_hash: str) -> Optional[ApiToken]:
    result = await db.execute(
        select(ApiToken).where(ApiToken.token_hash == token_hash)
    )
    return result.scalar_one_or_none()


async def get_api_token_by_id(db: AsyncSession, token_id: int) -> Optional[ApiToken]:
    result = await db.execute(
        select(ApiToken).where(ApiToken.id == token_id)
    )
    return result.scalar_one_or_none()


async def update_last_used(db: AsyncSession, token: ApiToken) -> None:
    token.last_used_at = datetime.utcnow()
    await db.commit()


async def deactivate_api_token(db: AsyncSession, token: ApiToken) -> ApiToken:
    token.is_active = False
    token.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(token)
    return token


async def delete_api_token(db: AsyncSession, token_id: int) -> None:
    await db.execute(delete(ApiToken).where(ApiToken.id == token_id))
    await db.commit()


async def update_api_token(
    db: AsyncSession,
    token: ApiToken,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    permissions: Union[Iterable[str], object] = UNSET,
    allowed_ips: Union[Iterable[str], object] = UNSET,
    expires_at: Union[Optional[datetime], object] = UNSET,
    is_active: Optional[bool] = None,
) -> ApiToken:
    if name is not None:
        token.name = name
    if description is not None:
        token.description = description
    if permissions is not UNSET:
        token.permissions = (
            _normalize_iterable(permissions) if permissions is not None else None
        )
    if allowed_ips is not UNSET:
        token.allowed_ips = (
            _normalize_iterable(allowed_ips) if allowed_ips is not None else None
        )
    if expires_at is not UNSET:
        token.expires_at = expires_at
    if is_active is not None:
        token.is_active = is_active

    token.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(token)
    return token


async def purge_expired_tokens(db: AsyncSession) -> int:
    now = datetime.utcnow()
    result = await db.execute(
        delete(ApiToken)
        .where(ApiToken.expires_at.is_not(None))
        .where(ApiToken.expires_at < now)
        .returning(ApiToken.id)
    )
    deleted = result.scalars().all()
    if deleted:
        await db.commit()
        return len(deleted)
    await db.rollback()
    return 0
