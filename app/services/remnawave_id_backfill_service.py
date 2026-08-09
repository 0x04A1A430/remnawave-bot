"""One-shot backfill of ``remnawave_id`` from existing ``remnawave_uuid`` (pre-v3).

Remnawave 3.0.0 removes ``uuid`` from the panel user object — the numeric ``id``
becomes the only identifier. Rows created while the panel was on v2.8.1 hold a
``remnawave_uuid`` but often no ``remnawave_id``, so upgrading without this step
would orphan those users.

This service runs on bot startup WHILE the panel is still on v2
(``REMNAWAVE_USE_USER_ID`` is off): for every ``User`` / ``Subscription`` row
that has a ``remnawave_uuid`` but no ``remnawave_id`` it resolves the numeric id
via ``GET /api/users/{uuid}`` and persists it. By the time the panel is upgraded,
``remnawave_id`` is already populated.

Properties:

* Gated on v2 mode — a no-op once ``REMNAWAVE_USE_USER_ID`` is enabled (in v3 the
  uuid lookup endpoint no longer exists and ids are populated by normal sync).
* Idempotent — only rows with NULL ``remnawave_id`` are touched; re-runs are
  no-ops once everything is backfilled.
* Best-effort — per-uuid failures are logged and skipped, never propagated, so a
  missing/404 panel user (or a temporarily down panel) can't crash startup.
* One API call per unique uuid — single-tariff rows duplicate the same uuid on
  the ``User`` and every ``Subscription`` row, so uuids are deduplicated first.
"""

import asyncio

import structlog
from sqlalchemy import select, update

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.models import Subscription, User
from app.external.remnawave_api import RemnaWaveAPI
from app.services.remnawave_service import RemnaWaveService


logger = structlog.get_logger(__name__)

_BACKFILL_CONCURRENCY = 5

# Row kind discriminator: 1 = User (single-tariff), 2 = Subscription (multi-tariff).
_USER_ROW = 1
_SUBSCRIPTION_ROW = 2


async def _collect_targets() -> list[tuple[int, str, int]]:
    """Return ``(row_id, remnawave_uuid, kind)`` for rows missing ``remnawave_id``."""
    targets: list[tuple[int, str, int]] = []

    async with AsyncSessionLocal() as db:
        users = await db.execute(
            select(User.id, User.remnawave_uuid).where(
                User.remnawave_uuid.isnot(None),
                User.remnawave_uuid != '',
                User.remnawave_id.is_(None),
            )
        )
        for row_id, uuid in users.all():
            targets.append((row_id, uuid, _USER_ROW))

        subs = await db.execute(
            select(Subscription.id, Subscription.remnawave_uuid).where(
                Subscription.remnawave_uuid.isnot(None),
                Subscription.remnawave_uuid != '',
                Subscription.remnawave_id.is_(None),
            )
        )
        for row_id, uuid in subs.all():
            targets.append((row_id, uuid, _SUBSCRIPTION_ROW))

    return targets


async def _resolve_remnawave_ids(uuids: list[str]) -> dict[str, int | None]:
    """Map uuid → numeric panel id (None if unresolvable). Bounded concurrency."""
    semaphore = asyncio.Semaphore(_BACKFILL_CONCURRENCY)
    service = RemnaWaveService()

    async def resolve(uuid: str) -> tuple[str, int | None]:
        async with semaphore:
            try:
                async with service.get_api_client() as api:
                    user = await api.get_user_by_uuid(uuid)
                    return uuid, (user.id if user else None)
            except Exception:
                logger.warning(
                    'Не удалось получить id панельного пользователя',
                    remnawave_uuid=uuid,
                    exc_info=True,
                )
                return uuid, None

    resolved: dict[str, int | None] = {}
    if uuids:
        for uuid, remnawave_id in await asyncio.gather(*[resolve(u) for u in uuids]):
            resolved[uuid] = remnawave_id
    return resolved


async def _run_backfill() -> dict[str, int]:
    if settings.REMNAWAVE_USE_USER_ID:
        logger.info('Бэкфилл remnawave_id пропущен: панель уже в режиме v3 (REMNAWAVE_USE_USER_ID)')
        return {'checked': 0, 'backfilled': 0, 'not_found': 0, 'errors': 0}

    targets = await _collect_targets()
    if not targets:
        return {'checked': 0, 'backfilled': 0, 'not_found': 0, 'errors': 0}

    uuids = sorted({uuid for _row_id, uuid, _kind in targets})
    resolved = await _resolve_remnawave_ids(uuids)

    backfilled = 0
    not_found = 0
    errors = 0

    async with AsyncSessionLocal() as db:
        for row_id, uuid, kind in targets:
            remnawave_id = resolved.get(uuid)
            if remnawave_id is None:
                not_found += 1
                continue
            # Guard against NaN/infinity parsed from JSON literals leaking into the DB
            remnawave_id = RemnaWaveAPI._sanitize_user_id(remnawave_id)
            if remnawave_id is None:
                not_found += 1
                continue
            model = User if kind == _USER_ROW else Subscription
            await db.execute(update(model).where(model.id == row_id).values(remnawave_id=remnawave_id))
            backfilled += 1
        await db.commit()

    logger.info(
        'Бэкфилл remnawave_id завершён',
        checked=len(targets),
        backfilled=backfilled,
        not_found=not_found,
    )
    return {
        'checked': len(targets),
        'backfilled': backfilled,
        'not_found': not_found,
        'errors': errors,
    }


async def backfill_remnawave_ids() -> dict[str, int]:
    """Background-safe entrypoint: never raises, returns a summary dict."""
    try:
        return await _run_backfill()
    except Exception as error:
        logger.error('Бэкфилл remnawave_id не выполнен', error=error)
        return {'checked': 0, 'backfilled': 0, 'not_found': 0, 'errors': 1}
