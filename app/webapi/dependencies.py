from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal
from app.database.models import WebApiToken
from app.services.web_api_token_service import web_api_token_service


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def require_api_token(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> WebApiToken:
    api_key = request.headers.get("X-API-Key")

    if not api_key:
        authorization = request.headers.get("Authorization")
        scheme, param = get_authorization_scheme_param(authorization)
        if scheme.lower() == "bearer" and param:
            api_key = param

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    token = await web_api_token_service.authenticate(
        db,
        api_key,
        remote_ip=request.client.host if request.client else None,
    )

    if not token:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key",
        )

    await db.commit()
    return token
