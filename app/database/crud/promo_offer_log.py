from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import PromoOfferLog

logger = logging.getLogger(__name__)


async def log_promo_offer_action(
    db: AsyncSession,
    *,
    user_id: Optional[int],
    offer_id: Optional[int],
    action: str,
    source: Optional[str] = None,
    percent: Optional[int] = None,
    effect_type: Optional[str] = None,
    details: Optional[Dict[str, object]] = None,
    commit: bool = True,
) -> PromoOfferLog:
    """Persist a promo offer log entry."""

    entry = PromoOfferLog(
        user_id=user_id,
        offer_id=offer_id,
        action=action,
        source=source,
        percent=percent,
        effect_type=effect_type,
        details=(details or {}).copy(),
    )
    db.add(entry)

    if commit:
        try:
            await db.commit()
            await db.refresh(entry)
        except Exception:
            logger.exception("Failed to commit promo offer log entry")
            raise

    return entry


async def list_promo_offer_logs(
    db: AsyncSession,
    offset: int = 0,
    limit: int = 20,
) -> Tuple[List[PromoOfferLog], int]:
    stmt = (
        select(PromoOfferLog)
        .options(
            selectinload(PromoOfferLog.user),
            selectinload(PromoOfferLog.offer),
        )
        .order_by(PromoOfferLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    count_stmt = select(func.count(PromoOfferLog.id))
    total = (await db.execute(count_stmt)).scalar() or 0

    return logs, total
