from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud import user_api_token as crud
from app.database.models import User, UserApiToken
from app.utils.security import generate_api_token, hash_api_token


class UserApiTokenService:
    """Service for issuing and validating user-facing API tokens."""

    def __init__(self) -> None:
        self.algorithm = settings.USER_API_TOKEN_HASH_ALGORITHM or "sha256"

    def hash_token(self, token: str) -> str:
        return hash_api_token(token, self.algorithm)  # type: ignore[arg-type]

    async def get_token_for_user(
        self,
        db: AsyncSession,
        user: User,
    ) -> Optional[UserApiToken]:
        if user.api_token:
            return user.api_token
        return await crud.get_token_by_user_id(db, user.id)

    async def generate_token(
        self,
        db: AsyncSession,
        user: User,
    ) -> Tuple[str, UserApiToken]:
        plain_token = generate_api_token()
        token_hash = self.hash_token(plain_token)
        token_prefix = plain_token[:12]
        token_last_digits = plain_token[-6:]

        existing = await crud.get_token_by_user_id(db, user.id)
        if existing:
            token = await crud.update_token(
                db,
                existing,
                token_hash=token_hash,
                token_prefix=token_prefix,
                token_last_digits=token_last_digits,
            )
        else:
            token = await crud.create_token(
                db,
                user_id=user.id,
                token_hash=token_hash,
                token_prefix=token_prefix,
                token_last_digits=token_last_digits,
            )

        if user.api_token is None:
            user.api_token = token

        return plain_token, token

    async def deactivate_token(
        self,
        db: AsyncSession,
        token: UserApiToken,
    ) -> UserApiToken:
        return await crud.deactivate_token(db, token)

    async def authenticate(
        self,
        db: AsyncSession,
        token_value: str,
        *,
        remote_ip: Optional[str] = None,
    ) -> Optional[UserApiToken]:
        normalized_value = (token_value or "").strip()
        if not normalized_value:
            return None

        token_hash = self.hash_token(normalized_value)
        token = await crud.get_token_by_hash(db, token_hash)

        if not token or not token.is_active:
            return None

        if token.user is None:
            token.user = await db.get(User, token.user_id)

        token.last_used_at = datetime.utcnow()
        if remote_ip:
            token.last_used_ip = remote_ip
        await db.flush()
        return token


user_api_token_service = UserApiTokenService()


__all__ = ["user_api_token_service", "UserApiTokenService"]
