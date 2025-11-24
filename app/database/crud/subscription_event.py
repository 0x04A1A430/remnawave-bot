from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SubscriptionEvent


async def create_subscription_event(
    db: AsyncSession,
    *,
    user_id: int,
    event_type: str,
    subscription_id: Optional[int] = None,
    transaction_id: Optional[int] = None,
    amount_kopeks: Optional[int] = None,
    currency: Optional[str] = None,
    message: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> SubscriptionEvent:
    event = SubscriptionEvent(
        user_id=user_id,
        event_type=event_type,
        subscription_id=subscription_id,
        transaction_id=transaction_id,
        amount_kopeks=amount_kopeks,
        currency=currency,
        message=message,
        occurred_at=occurred_at or datetime.utcnow(),
        extra=extra or None,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def list_subscription_events(
    db: AsyncSession,
    *,
    limit: int,
    offset: int,
    event_types: Optional[Iterable[str]] = None,
    user_id: Optional[int] = None,
) -> Tuple[list[SubscriptionEvent], int]:
    base_query = select(SubscriptionEvent)
    filters = []

    if event_types:
        filters.append(SubscriptionEvent.event_type.in_(set(event_types)))
    if user_id:
        filters.append(SubscriptionEvent.user_id == user_id)

    if filters:
        base_query = base_query.where(and_(*filters))

    total_query = base_query.with_only_columns(func.count()).order_by(None)
    total = await db.scalar(total_query) or 0

    result = await db.execute(
        base_query.options(selectinload(SubscriptionEvent.user))
        .order_by(SubscriptionEvent.occurred_at.desc())
        .offset(offset)
        .limit(limit)
    )

    return result.scalars().all(), int(total)
