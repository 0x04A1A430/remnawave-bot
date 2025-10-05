from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.crud.discount_offer import get_latest_claimed_offer_for_user
from app.database.models import (
    ServerSquad,
    Subscription,
    SubscriptionTemporaryAccess,
    User,
)

logger = logging.getLogger(__name__)

BAR_LENGTH = 10


def get_promo_offer_discount_percent(user: Optional[User]) -> int:
    """Return the currently active promo discount percent for the user."""

    if not user:
        return 0

    try:
        percent = int(getattr(user, "promo_offer_discount_percent", 0) or 0)
    except (TypeError, ValueError):
        return 0

    expires_at = getattr(user, "promo_offer_discount_expires_at", None)
    if expires_at and expires_at <= datetime.utcnow():
        return 0

    return max(0, min(100, percent))


def _format_time_left(seconds_left: int, language: str) -> str:
    total_minutes = max(1, math.ceil(seconds_left / 60))
    days, remainder_minutes = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(remainder_minutes, 60)

    language_code = (language or "ru").split("-")[0].lower()
    if language_code == "en":
        day_label, hour_label, minute_label = "d", "h", "m"
    else:
        day_label, hour_label, minute_label = "д", "ч", "м"

    parts: List[str] = []
    if days:
        parts.append(f"{days}{day_label}")
    if hours or days:
        parts.append(f"{hours}{hour_label}")
    parts.append(f"{minutes}{minute_label}")
    return " ".join(parts)


def _build_progress_bar(seconds_left: int, total_seconds: int) -> str:
    ratio = 0.0 if total_seconds <= 0 else max(0.0, min(1.0, seconds_left / total_seconds))
    filled_segments = int(round(ratio * BAR_LENGTH))
    filled_segments = max(0, min(BAR_LENGTH, filled_segments))
    if filled_segments == 0 and seconds_left > 0:
        filled_segments = 1
    empty_segments = max(0, BAR_LENGTH - filled_segments)
    return "█" * filled_segments + "░" * empty_segments


def _render_timer_line(
    texts,
    *,
    seconds_left: int,
    total_seconds: int,
    translation_key: str,
    default_template: str,
) -> str:
    bar = _build_progress_bar(seconds_left, total_seconds)
    language = getattr(texts, "language", "ru")
    time_left_text = _format_time_left(seconds_left, language)
    template = texts.t(translation_key, default_template)
    return template.format(bar=bar, time_left=time_left_text)


async def _resolve_discount_total_seconds(
    db: AsyncSession,
    user: User,
    *,
    expires_at: datetime,
    seconds_left: int,
) -> int:
    total_seconds: Optional[int] = None
    source = getattr(user, "promo_offer_discount_source", None)

    try:
        offer = await get_latest_claimed_offer_for_user(db, user.id, source)
    except Exception as lookup_error:  # pragma: no cover - defensive logging
        logger.debug(
            "Failed to resolve latest claimed promo offer for user %s: %s",
            user.id,
            lookup_error,
        )
        offer = None

    if offer and getattr(offer, "claimed_at", None):
        total_seconds = int((expires_at - offer.claimed_at).total_seconds())
        if total_seconds <= 0:
            total_seconds = None

    if total_seconds is None and offer and isinstance(offer.extra_data, dict):
        raw_duration = (
            offer.extra_data.get("active_discount_hours")
            or offer.extra_data.get("duration_hours")
        )
        try:
            if raw_duration:
                total_seconds = int(float(raw_duration) * 3600)
        except (TypeError, ValueError):
            total_seconds = None

    if not total_seconds or total_seconds <= 0:
        total_seconds = seconds_left

    return total_seconds


async def get_promo_offer_hint(
    db: AsyncSession,
    user: Optional[User],
    texts,
    *,
    percent: Optional[int] = None,
) -> Optional[str]:
    """Return a textual hint describing the active promo discount for the user."""

    if user is None:
        return None

    if percent is None:
        percent = get_promo_offer_discount_percent(user)

    if percent <= 0:
        return None

    base_hint = texts.t(
        "SUBSCRIPTION_PROMO_DISCOUNT_HINT",
        "⚡ Extra {percent}% discount is active and will apply automatically. It stacks with other discounts.",
    ).format(percent=percent)

    expires_at = getattr(user, "promo_offer_discount_expires_at", None)
    if not expires_at:
        return base_hint

    now = datetime.utcnow()
    if expires_at <= now:
        return base_hint

    seconds_left = int((expires_at - now).total_seconds())
    if seconds_left <= 0:
        return base_hint

    total_seconds = await _resolve_discount_total_seconds(
        db,
        user,
        expires_at=expires_at,
        seconds_left=seconds_left,
    )

    timer_line = _render_timer_line(
        texts,
        seconds_left=seconds_left,
        total_seconds=total_seconds,
        translation_key="SUBSCRIPTION_PROMO_DISCOUNT_TIMER",
        default_template="⏳ Discount active for {time_left}\n<code>{bar}</code>",
    )

    return f"{base_hint}\n{timer_line}"


async def _fetch_server_names(
    db: AsyncSession,
    squad_uuids: Sequence[str],
) -> Dict[str, str]:
    if not squad_uuids:
        return {}

    result = await db.execute(
        select(ServerSquad.squad_uuid, ServerSquad.display_name)
        .where(ServerSquad.squad_uuid.in_(list({uuid for uuid in squad_uuids if uuid})))
    )
    return {row[0]: row[1] for row in result.all()}


async def get_test_access_hint(
    db: AsyncSession,
    subscription: Optional[Subscription],
    texts,
) -> Optional[str]:
    """Return a textual hint about active temporary server access for a subscription."""

    if subscription is None:
        return None

    now = datetime.utcnow()
    result = await db.execute(
        select(SubscriptionTemporaryAccess)
        .options(selectinload(SubscriptionTemporaryAccess.offer))
        .where(
            SubscriptionTemporaryAccess.subscription_id == subscription.id,
            SubscriptionTemporaryAccess.is_active == True,  # noqa: E712
            SubscriptionTemporaryAccess.expires_at > now,
        )
        .order_by(SubscriptionTemporaryAccess.expires_at.desc())
    )
    entries = result.scalars().all()
    if not entries:
        return None

    unique_squads: List[str] = []
    max_expires_at: Optional[datetime] = None
    earliest_created: Optional[datetime] = None

    for entry in entries:
        squad_uuid = getattr(entry, "squad_uuid", None)
        if squad_uuid and squad_uuid not in unique_squads:
            unique_squads.append(squad_uuid)

        expires_at = getattr(entry, "expires_at", None)
        if expires_at:
            if max_expires_at is None or expires_at > max_expires_at:
                max_expires_at = expires_at

        created_at = getattr(entry, "created_at", None)
        if created_at:
            if earliest_created is None or created_at < earliest_created:
                earliest_created = created_at

    if not unique_squads or max_expires_at is None:
        return None

    seconds_left = int((max_expires_at - now).total_seconds())
    if seconds_left <= 0:
        return None

    names_map = await _fetch_server_names(db, unique_squads)
    names: List[str] = [names_map.get(uuid, uuid) for uuid in unique_squads]
    count = len(unique_squads)

    count_text = texts.t("MAIN_MENU_TEST_ACCESS_COUNT", "Servers: {count}").format(count=count)
    if names:
        displayed_names = names[:3]
        summary = ", ".join(displayed_names)
        remaining = len(names) - len(displayed_names)
        if remaining > 0:
            more_text = texts.t("MAIN_MENU_TEST_ACCESS_MORE", "and {count} more").format(count=remaining)
            summary = f"{summary}, {more_text}"
        summary_text = f"{count_text}. {summary}"
    else:
        summary_text = count_text

    base_hint = texts.t(
        "MAIN_MENU_TEST_ACCESS_HINT",
        "🧪 Test servers are active. {summary}",
    ).format(summary=summary_text)

    if earliest_created and max_expires_at > earliest_created:
        total_seconds = int((max_expires_at - earliest_created).total_seconds())
    else:
        total_seconds = seconds_left

    timer_line = _render_timer_line(
        texts,
        seconds_left=seconds_left,
        total_seconds=total_seconds,
        translation_key="MAIN_MENU_TEST_ACCESS_TIMER",
        default_template="⏳ Access remains for {time_left}\n<code>{bar}</code>",
    )

    return f"{base_hint}\n{timer_line}"
