from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud import web_api_token as crud
from app.database.models import WebApiToken
from app.utils.security import generate_api_token, hash_api_token


class WebApiTokenService:
    """Сервис для управления токенами административного веб-API."""

    def __init__(self):
        self.algorithm = settings.WEB_API_TOKEN_HASH_ALGORITHM or "sha256"

    def hash_token(self, token: str) -> str:
        return hash_api_token(token, self.algorithm)  # type: ignore[arg-type]

    async def authenticate(
        self,
        db: AsyncSession,
        token_value: str,
        *,
        remote_ip: Optional[str] = None,
    ) -> Optional[WebApiToken]:
        token_hash = self.hash_token(token_value)
        token = await crud.get_token_by_hash(db, token_hash)

        if not token or not token.is_active:
            return None

        if token.expires_at and token.expires_at < datetime.utcnow():
            return None

        token.last_used_at = datetime.utcnow()
        if remote_ip:
            token.last_used_ip = remote_ip
        await db.flush()
        return token

    async def create_token(
        self,
        db: AsyncSession,
        *,
        name: str,
        description: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        created_by: Optional[str] = None,
        token_value: Optional[str] = None,
    ) -> Tuple[str, WebApiToken]:
        plain_token = token_value or generate_api_token()
        token_hash = self.hash_token(plain_token)

        token = await crud.create_token(
            db,
            name=name,
            token_hash=token_hash,
            token_prefix=plain_token[:12],
            description=description,
            expires_at=expires_at,
            created_by=created_by,
        )

        return plain_token, token

    async def revoke_token(self, db: AsyncSession, token: WebApiToken) -> WebApiToken:
        token.is_active = False
        token.updated_at = datetime.utcnow()
        await db.flush()
        await db.refresh(token)
        return token

    async def activate_token(self, db: AsyncSession, token: WebApiToken) -> WebApiToken:
        token.is_active = True
        token.updated_at = datetime.utcnow()
        await db.flush()
        await db.refresh(token)
        return token


web_api_token_service = WebApiTokenService()
