from __future__ import annotations

import secrets
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud import web_api_token as crud
from app.database.models import WebApiToken
from app.utils.security import generate_api_token, hash_api_token


logger = structlog.get_logger(__name__)


async def ensure_default_web_api_token() -> bool:
    """Ensure the bootstrap web API token from config exists in the DB."""
    from app.database.database import AsyncSessionLocal

    default_token = (settings.WEB_API_DEFAULT_TOKEN or '').strip()
    if not default_token:
        return True

    token_name = (settings.WEB_API_DEFAULT_TOKEN_NAME or 'Bootstrap Token').strip()

    try:
        async with AsyncSessionLocal() as session:
            algorithm = settings.WEB_API_TOKEN_HASH_ALGORITHM
            hmac_secret = settings.WEB_API_TOKEN_HMAC_SECRET
            token_hash = hash_api_token(default_token, algorithm, hmac_secret=hmac_secret)

            result = await session.execute(select(WebApiToken).where(WebApiToken.token_hash == token_hash))
            existing = result.scalar_one_or_none()

            # Fallback: if HMAC enabled, try plain hash (legacy token) and rehash
            if not existing and hmac_secret:
                plain_hash = hash_api_token(default_token, algorithm)
                result = await session.execute(select(WebApiToken).where(WebApiToken.token_hash == plain_hash))
                existing = result.scalar_one_or_none()
                if existing:
                    existing.token_hash = token_hash
                    existing.updated_at = datetime.now(UTC)
                    await session.commit()
                    logger.info('Дефолтный токен перехеширован на HMAC')
                    return True

            if existing:
                updated = False

                if not existing.is_active:
                    existing.is_active = True
                    updated = True

                if token_name and existing.name != token_name:
                    existing.name = token_name
                    updated = True

                if updated:
                    existing.updated_at = datetime.now(UTC)
                    await session.commit()
                return True

            token = WebApiToken(
                name=token_name or 'Bootstrap Token',
                token_hash=token_hash,
                token_prefix=default_token[:8],
                description='Автоматически создан при миграции',
                created_by='migration',
                is_active=True,
            )
            session.add(token)
            await session.commit()
            logger.info('Создан дефолтный токен веб-API из конфигурации')
            return True

    except Exception as error:
        logger.error('Ошибка создания дефолтного веб-API токена', error=error)
        return False


class WebApiTokenService:
    """Сервис для управления токенами административного веб-API."""

    def __init__(self):
        self.algorithm = settings.WEB_API_TOKEN_HASH_ALGORITHM or 'sha256'
        self.hmac_secret = settings.WEB_API_TOKEN_HMAC_SECRET

    def hash_token(self, token: str) -> str:
        return hash_api_token(token, self.algorithm, hmac_secret=self.hmac_secret)  # type: ignore[arg-type]

    def _hash_token_plain(self, token: str) -> str:
        """Hash without HMAC (for legacy fallback)."""
        return hash_api_token(token, self.algorithm)  # type: ignore[arg-type]

    async def _load_token_with_fallback(self, db: AsyncSession, value: str) -> WebApiToken | None:
        """Load token by hash, falling back to plain hash if HMAC is enabled.

        When HMAC is newly enabled, existing tokens are stored with plain
        hashes. This method tries HMAC first, then falls back to plain hash
        and auto-rehashes the token for future lookups.
        """
        token_hash = self.hash_token(value)
        token = await crud.get_token_by_hash(db, token_hash)

        if not token and self.hmac_secret:
            plain_hash = self._hash_token_plain(value)
            token = await crud.get_token_by_hash(db, plain_hash)
            if token:
                token.token_hash = token_hash
                token.updated_at = datetime.now(UTC)
                await db.flush()
                logger.info('Токен автоматически перехеширован на HMAC', token_id=token.id)

        return token

    async def authenticate(
        self,
        db: AsyncSession,
        token_value: str,
        *,
        remote_ip: str | None = None,
    ) -> WebApiToken | None:
        normalized_value = token_value.strip()
        if not normalized_value:
            return None

        token = await self._load_token_with_fallback(db, normalized_value)

        if not token:
            default_token = (settings.WEB_API_DEFAULT_TOKEN or '').strip()
            if default_token and secrets.compare_digest(default_token, normalized_value):
                await ensure_default_web_api_token()
                token = await self._load_token_with_fallback(db, default_token)

        if not token or not token.is_active:
            return None

        if token.expires_at and token.expires_at < datetime.now(UTC):
            return None

        token.last_used_at = datetime.now(UTC)
        if remote_ip:
            token.last_used_ip = remote_ip
        await db.flush()
        return token

    async def create_token(
        self,
        db: AsyncSession,
        *,
        name: str,
        description: str | None = None,
        expires_at: datetime | None = None,
        created_by: str | None = None,
        token_value: str | None = None,
    ) -> tuple[str, WebApiToken]:
        plain_token = token_value or generate_api_token()
        token_hash = self.hash_token(plain_token)

        token = await crud.create_token(
            db,
            name=name,
            token_hash=token_hash,
            token_prefix=plain_token[:8],
            description=description,
            expires_at=expires_at,
            created_by=created_by,
        )

        return plain_token, token

    async def revoke_token(self, db: AsyncSession, token: WebApiToken) -> WebApiToken:
        token.is_active = False
        token.updated_at = datetime.now(UTC)
        await db.flush()
        await db.refresh(token)
        return token

    async def activate_token(self, db: AsyncSession, token: WebApiToken) -> WebApiToken:
        token.is_active = True
        token.updated_at = datetime.now(UTC)
        await db.flush()
        await db.refresh(token)
        return token


web_api_token_service = WebApiTokenService()
