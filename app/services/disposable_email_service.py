"""Service for blocking disposable/temporary email domains."""

import asyncio
import logging
from datetime import UTC, datetime

import aiohttp

from app.config import settings


logger = logging.getLogger(__name__)


class DisposableEmailService:
    """
    Downloads and caches a list of disposable email domains from GitHub.

    Domains are stored in a frozenset for O(1) thread-safe lookups.
    The list is refreshed every 24 hours via an asyncio background task.
    If the download fails, the service falls back to an empty set (no blocking).
    """

    DOMAINS_URL = 'https://raw.githubusercontent.com/disposable/disposable-email-domains/master/domains.txt'
    UPDATE_INTERVAL_HOURS = 24

    def __init__(self) -> None:
        self._domains: frozenset[str] = frozenset()
        self._task: asyncio.Task[None] | None = None
        self._last_updated: datetime | None = None
        self._domain_count: int = 0

    async def start(self) -> None:
        """Load domains and start periodic refresh task."""
        await self._update_domains()
        self._task = asyncio.create_task(self._periodic_loop())
        logger.info('DisposableEmailService started (%d domains loaded)', self._domain_count)

    async def stop(self) -> None:
        """Cancel periodic refresh task."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info('DisposableEmailService stopped')

    async def _update_domains(self) -> None:
        """Fetch domains.txt from GitHub and swap the in-memory set."""
        try:
            async with aiohttp.ClientSession() as session, session.get(self.DOMAINS_URL) as resp:
                if resp.status != 200:
                    logger.error(
                        'Failed to fetch disposable domains: HTTP %d',
                        resp.status,
                    )
                    return

                text = await resp.text()

            domains = frozenset(
                line.strip().lower() for line in text.splitlines() if line.strip() and not line.startswith('#')
            )

            self._domains = domains
            self._domain_count = len(domains)
            self._last_updated = datetime.now(UTC)
            logger.info('Disposable email domains updated: %d domains', self._domain_count)

        except Exception:
            logger.exception('Error updating disposable email domains')

    async def _periodic_loop(self) -> None:
        """Sleep then refresh, repeating forever until cancelled."""
        while True:
            await asyncio.sleep(self.UPDATE_INTERVAL_HOURS * 3600)
            await self._update_domains()

    def is_disposable(self, email: str) -> bool:
        """Check if the email uses a disposable domain.

        Returns False when the feature is disabled via settings.
        """
        if not getattr(settings, 'DISPOSABLE_EMAIL_CHECK_ENABLED', True):
            return False

        if not self._domains:
            return False

        try:
            domain = email.rsplit('@', 1)[1].lower()
        except IndexError:
            return False

        return domain in self._domains

    def get_status(self) -> dict:
        """Return service status for monitoring / health checks."""
        return {
            'enabled': getattr(settings, 'DISPOSABLE_EMAIL_CHECK_ENABLED', True),
            'domain_count': self._domain_count,
            'last_updated': self._last_updated.isoformat() if self._last_updated else None,
            'running': self._task is not None and not self._task.done(),
        }


disposable_email_service = DisposableEmailService()
