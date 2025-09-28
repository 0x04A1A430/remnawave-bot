from __future__ import annotations

from datetime import datetime
from secrets import compare_digest
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
        normalized_value = token_value.strip()
        token_hash = self.hash_token(normalized_value)
        token = await crud.get_token_by_hash(db, token_hash)

        token = await self._ensure_bootstrap_token_if_needed(
            db,
            token,
            normalized_value,
            token_hash,
        )

        if not token or not token.is_active:
            return None

        now = datetime.utcnow()
        if token.expires_at and token.expires_at < now:
            return None

        token.last_used_at = now
        if remote_ip:
            token.last_used_ip = remote_ip
        await db.flush()
        return token

    async def _ensure_bootstrap_token_if_needed(
        self,
        db: AsyncSession,
        token: Optional[WebApiToken],
        provided_value: str,
        token_hash: str,
    ) -> Optional[WebApiToken]:
        """Гарантирует работу бутстрап-токена из настроек даже без миграции."""

        if token and token.is_active:
            return token

        default_token = (settings.WEB_API_DEFAULT_TOKEN or "").strip()
        if not default_token:
            return token

        if not compare_digest(default_token, provided_value):
            return token

        token_name = (settings.WEB_API_DEFAULT_TOKEN_NAME or "Bootstrap Token").strip() or "Bootstrap Token"
        now = datetime.utcnow()

        if token:
            updated = False

            if not token.is_active:
                token.is_active = True
                updated = True

            if token.name != token_name:
                token.name = token_name
                updated = True

            if updated:
                token.updated_at = now
                await db.flush()

            return token

        token = await crud.create_token(
            db,
            name=token_name,
            token_hash=token_hash,
            token_prefix=default_token[:12],
            description="Автоматически создан при авторизации бутстрап-токеном",
            created_by="bootstrap",
        )

        token.created_at = token.created_at or now
        token.updated_at = now
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
