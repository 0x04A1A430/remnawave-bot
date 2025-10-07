import hashlib
import logging
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.models import SystemSetting


logger = logging.getLogger(__name__)


class ExternalAdminTokenService:
    """Управляет токеном внешней административной панели."""

    SETTING_KEY = "EXTERNAL_ADMIN_TOKEN"
    SETTING_DESCRIPTION = "Токен для проверки внешней административной панели"
    _SALT = "remnawave-external-admin-token"

    @staticmethod
    def _normalize_username(username: Optional[str]) -> str:
        if not username:
            return ""
        return username.strip().lstrip("@").lower()

    @staticmethod
    def _sanitize_username(username: Optional[str]) -> Optional[str]:
        if not username:
            return None
        sanitized = username.strip().lstrip("@")
        return sanitized or None

    def generate_token(self, username: str) -> str:
        normalized = self._normalize_username(username)
        if not normalized:
            raise ValueError("Bot username is required to generate external admin token")
        payload = f"{self._SALT}:{normalized}".encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        return digest[:48]

    async def _ensure_in_session(self, db: AsyncSession, token: str) -> bool:
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == self.SETTING_KEY)
        )
        setting = result.scalar_one_or_none()

        if setting is None:
            setting = SystemSetting(
                key=self.SETTING_KEY,
                value=token,
                description=self.SETTING_DESCRIPTION,
            )
            db.add(setting)
            return True

        if setting.value != token:
            setting.value = token
            setting.description = setting.description or self.SETTING_DESCRIPTION
            setting.updated_at = datetime.utcnow()
            return True

        return False

    async def ensure_token(
        self,
        username: Optional[str],
        *,
        session: Optional[AsyncSession] = None,
    ) -> Tuple[Optional[str], bool]:
        """Гарантирует наличие токена для указанного username."""

        normalized = self._normalize_username(username)
        if not normalized:
            logger.warning(
                "Не удалось сгенерировать токен внешней админки: username не задан",
            )
            return None, False

        token = self.generate_token(normalized)
        sanitized_username = self._sanitize_username(username)

        if session is None:
            async with AsyncSessionLocal() as db:
                updated = await self._ensure_in_session(db, token)
                await db.commit()
        else:
            updated = await self._ensure_in_session(session, token)
            if updated:
                await session.flush()

        settings.BOT_USERNAME = sanitized_username
        settings.EXTERNAL_ADMIN_TOKEN = token

        return token, updated

    async def ensure_from_config(self) -> Tuple[Optional[str], bool]:
        """Генерирует токен на основе username из конфигурации."""

        return await self.ensure_token(settings.BOT_USERNAME)


external_admin_token_service = ExternalAdminTokenService()
