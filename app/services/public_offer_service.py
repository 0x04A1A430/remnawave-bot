import logging
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.public_offer import (
    get_public_offer,
    set_public_offer_enabled,
    upsert_public_offer,
)
from app.database.models import PublicOffer

logger = logging.getLogger(__name__)


class PublicOfferService:
    """Helpers for managing the public offer text and visibility."""

    MAX_PAGE_LENGTH = 3500

    @staticmethod
    def _normalize_language(language: str) -> str:
        base_language = language or settings.DEFAULT_LANGUAGE or "ru"
        return base_language.split("-")[0].lower()

    @staticmethod
    def normalize_language(language: str) -> str:
        return PublicOfferService._normalize_language(language)

    @classmethod
    async def get_offer(
        cls,
        db: AsyncSession,
        language: str,
        *,
        fallback: bool = False,
    ) -> Optional[PublicOffer]:
        lang = cls._normalize_language(language)
        offer = await get_public_offer(db, lang)

        if offer or not fallback:
            return offer

        default_lang = cls._normalize_language(settings.DEFAULT_LANGUAGE)
        if lang != default_lang:
            return await get_public_offer(db, default_lang)

        return offer

    @classmethod
    async def get_active_offer(
        cls,
        db: AsyncSession,
        language: str,
    ) -> Optional[PublicOffer]:
        lang = cls._normalize_language(language)
        offer = await get_public_offer(db, lang)

        if offer and offer.is_enabled and offer.content.strip():
            return offer

        default_lang = cls._normalize_language(settings.DEFAULT_LANGUAGE)
        if lang != default_lang:
            fallback_offer = await get_public_offer(db, default_lang)
            if fallback_offer and fallback_offer.is_enabled and fallback_offer.content.strip():
                return fallback_offer

        return None

    @classmethod
    async def is_offer_enabled(cls, db: AsyncSession, language: str) -> bool:
        offer = await cls.get_active_offer(db, language)
        return offer is not None

    @classmethod
    async def save_offer(
        cls,
        db: AsyncSession,
        language: str,
        content: str,
    ) -> PublicOffer:
        lang = cls._normalize_language(language)
        enable_if_new = True
        offer = await upsert_public_offer(
            db,
            lang,
            content,
            enable_if_new=enable_if_new,
        )
        logger.info("✅ Публичная оферта обновлена для языка %s", lang)
        return offer

    @classmethod
    async def set_enabled(
        cls,
        db: AsyncSession,
        language: str,
        enabled: bool,
    ) -> PublicOffer:
        lang = cls._normalize_language(language)
        return await set_public_offer_enabled(db, lang, enabled)

    @classmethod
    async def toggle_enabled(
        cls,
        db: AsyncSession,
        language: str,
    ) -> PublicOffer:
        lang = cls._normalize_language(language)
        offer = await get_public_offer(db, lang)

        if offer:
            new_status = not offer.is_enabled
        else:
            new_status = True

        return await set_public_offer_enabled(db, lang, new_status)

    @staticmethod
    def split_content_into_pages(
        content: str,
        *,
        max_length: int = None,
    ) -> List[str]:
        if not content:
            return []

        normalized = content.replace("\r\n", "\n").strip()
        if not normalized:
            return []

        max_len = max_length or PublicOfferService.MAX_PAGE_LENGTH

        if len(normalized) <= max_len:
            return [normalized]

        paragraphs = [
            paragraph.strip()
            for paragraph in normalized.split("\n\n")
            if paragraph.strip()
        ]

        pages: List[str] = []
        current = ""

        def flush_current() -> None:
            nonlocal current
            if current:
                pages.append(current.strip())
                current = ""

        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= max_len:
                current = candidate
                continue

            flush_current()

            if len(paragraph) <= max_len:
                current = paragraph
                continue

            start_index = 0
            while start_index < len(paragraph):
                chunk = paragraph[start_index:start_index + max_len]
                pages.append(chunk.strip())
                start_index += max_len

            current = ""

        flush_current()

        if not pages:
            return [normalized[:max_len]]

        return pages
