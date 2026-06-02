"""One-shot cleanup of duplicate multi-tariff subscriptions.

Re-buying a tariff after it expired used to create a NEW subscription instead of
reviving the old one, so users piled up stacks of expired same-tariff
duplicates. The purchase path now revives in place (``create_paid_subscription``);
this collapses the duplicates that already accumulated.

Per (user, tariff) it keeps one survivor — most "alive" first
(active > limited > trial > expired), then the latest ``end_date`` — and removes
the redundant EXPIRED/DISABLED ones from BOTH the DB and the Remnawave panel,
exactly like a normal subscription deletion (``delete_remnawave_user`` + row
delete), so no orphaned panel users are left behind. Live subscriptions
(active / limited / trial), lone rows and pending are never touched.

Runs once in the background on startup. Idempotent — a no-op once there are no
duplicates. If the panel can't confirm a user's deletion, that duplicate's DB
row is kept and retried on the next start, so the DB and panel never drift apart.
"""

import structlog
from sqlalchemy import select

from app.database.database import AsyncSessionLocal
from app.database.models import Subscription, SubscriptionStatus
from app.services.subscription_service import SubscriptionService


logger = structlog.get_logger(__name__)

# Lower rank = better survivor. Statuses not listed (e.g. disabled/pending) sort last.
_SURVIVOR_PRIORITY = {
    SubscriptionStatus.ACTIVE.value: 0,
    SubscriptionStatus.LIMITED.value: 1,
    SubscriptionStatus.TRIAL.value: 2,
    SubscriptionStatus.EXPIRED.value: 3,
}
_REMOVABLE_STATUSES = frozenset({SubscriptionStatus.EXPIRED.value, SubscriptionStatus.DISABLED.value})


def _survivor_key(sub: Subscription) -> tuple[int, float]:
    end_ts = sub.end_date.timestamp() if sub.end_date else 0.0
    return (_SURVIVOR_PRIORITY.get(sub.status, 4), -end_ts)


async def _run_dedupe() -> dict[str, int]:
    removed_db = 0
    removed_panel = 0
    service = SubscriptionService()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Subscription).where(
                Subscription.tariff_id.isnot(None),
                Subscription.is_trial.is_(False),
            )
        )
        groups: dict[tuple[int, int], list[Subscription]] = {}
        for sub in result.scalars().all():
            groups.setdefault((sub.user_id, sub.tariff_id), []).append(sub)

        for subs in groups.values():
            if len(subs) < 2:
                continue
            subs.sort(key=_survivor_key)
            survivor, *rest = subs
            for dup in rest:
                if dup.status not in _REMOVABLE_STATUSES:
                    continue  # never remove a live subscription
                if dup.remnawave_uuid and dup.remnawave_uuid != survivor.remnawave_uuid:
                    try:
                        deleted = await service.delete_remnawave_user(dup.remnawave_uuid)
                    except Exception as error:
                        logger.warning(
                            'dedup: panel delete failed, keeping duplicate for retry',
                            subscription_id=dup.id,
                            uuid=dup.remnawave_uuid,
                            error=error,
                        )
                        continue
                    if not deleted:
                        # Panel still has the user — keep the DB row so they stay in
                        # sync; retried on the next start.
                        continue
                    removed_panel += 1
                await db.delete(dup)
                removed_db += 1

        if removed_db:
            await db.commit()

    if removed_db:
        logger.info(
            '🧹 Схлопнуты дубли тарифных подписок',
            removed_db=removed_db,
            removed_panel=removed_panel,
        )
    return {'removed_db': removed_db, 'removed_panel': removed_panel}


async def dedupe_expired_tariff_subscriptions() -> dict[str, int]:
    """Background-safe entrypoint: never raises, returns the counts removed."""
    try:
        return await _run_dedupe()
    except Exception as error:
        logger.error('dedup: cleanup pass failed', error=error)
        return {'removed_db': 0, 'removed_panel': 0}
