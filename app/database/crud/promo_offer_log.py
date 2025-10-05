from __future__ import annotations

from math import ceil
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import PromoOfferLog


async def log_promo_offer_event(
    db: AsyncSession,
    *,
    user_id: int,
    action: str,
    offer_id: Optional[int] = None,
    source: Optional[str] = None,
    percent: Optional[int] = None,
    discount_value_kopeks: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> PromoOfferLog:
    payload = dict(metadata or {})
    log_entry = PromoOfferLog(
        user_id=user_id,
        offer_id=offer_id,
        action=action,
        source=source,
        percent=percent,
        discount_value_kopeks=discount_value_kopeks,
        metadata=payload or None,
    )
    db.add(log_entry)
    await db.flush()
    return log_entry


async def paginate_promo_offer_logs(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[PromoOfferLog], int, int, int]:
    safe_page = max(1, page)
    total_result = await db.execute(select(func.count()).select_from(PromoOfferLog))
    total_count = total_result.scalar_one()

    if total_count == 0:
        return [], 0, 0, safe_page

    total_pages = max(1, ceil(total_count / page_size))
    if safe_page > total_pages:
        safe_page = total_pages

    offset = (safe_page - 1) * page_size
    query = (
        select(PromoOfferLog)
        .options(
            selectinload(PromoOfferLog.user),
            selectinload(PromoOfferLog.offer),
        )
        .order_by(PromoOfferLog.created_at.desc(), PromoOfferLog.id.desc())
        .limit(page_size)
        .offset(offset)
    )

    result = await db.execute(query)
    logs = result.scalars().all()
    return logs, total_count, total_pages, safe_page
