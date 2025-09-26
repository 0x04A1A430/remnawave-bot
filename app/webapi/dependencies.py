from __future__ import annotations

from typing import Callable, List, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.api_token import (
    get_api_token_by_hash,
    hash_token,
    update_last_used,
)
from app.database.database import AsyncSessionLocal
from app.database.models import ApiToken


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


def _is_ip_allowed(client_ip: Optional[str], allowed_ips: List[str]) -> bool:
    if not allowed_ips:
        return True
    if not client_ip:
        return False
    return client_ip in allowed_ips


async def get_current_token(
    request: Request,
    api_key: Optional[str] = Depends(api_key_header),
    db: AsyncSession = Depends(get_db),
) -> ApiToken:
    if not settings.is_webapi_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Web API отключен",
        )

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется API ключ",
        )

    token_hash = hash_token(api_key)
    token = await get_api_token_by_hash(db, token_hash)

    if not token or not token.is_valid():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Неверный или неактивный API ключ",
        )

    client_ip = request.client.host if request.client else None
    global_allowed = settings.get_webapi_allowed_ips()
    if global_allowed and not _is_ip_allowed(client_ip, global_allowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IP адрес не разрешен глобальными настройками",
        )

    token_allowed_ips = token.allowed_ips or []
    if token_allowed_ips and not _is_ip_allowed(client_ip, token_allowed_ips):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IP адрес не разрешен для этого токена",
        )

    await update_last_used(db, token)
    return token


def require_permission(permission: str) -> Callable[[ApiToken], ApiToken]:
    async def _dependency(token: ApiToken = Depends(get_current_token)) -> ApiToken:
        permissions = token.permissions or []
        if "*" in permissions or permission in permissions:
            return token
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для выполнения операции",
        )

    return _dependency
