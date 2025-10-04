from __future__ import annotations

from datetime import datetime
from typing import Optional, Set

from app.database.models import PromoOfferTarget, SubscriptionStatus, User


def determine_user_segments(user: User, now: Optional[datetime] = None) -> Set[str]:
    """Determine promo offer segments for a user."""

    now = now or datetime.utcnow()
    segments: Set[str] = set()
    subscription = getattr(user, "subscription", None)

    if subscription:
        is_trial = bool(subscription.is_trial)
        is_active = (
            subscription.status == SubscriptionStatus.ACTIVE.value
            and subscription.end_date > now
        )
        is_expired = (
            subscription.end_date <= now
            or subscription.status in {
                SubscriptionStatus.EXPIRED.value,
                SubscriptionStatus.DISABLED.value,
            }
        )

        if is_trial:
            if is_active:
                segments.add(PromoOfferTarget.TRIAL_ACTIVE.value)
            if is_expired:
                segments.add(PromoOfferTarget.TRIAL_EXPIRED.value)
        else:
            if is_active:
                segments.add(PromoOfferTarget.PAID_ACTIVE.value)
            if is_expired:
                segments.add(PromoOfferTarget.PAID_EXPIRED.value)
    else:
        segments.add(PromoOfferTarget.NO_SUBSCRIPTION.value)

    if not subscription and getattr(user, "has_had_paid_subscription", False):
        segments.add(PromoOfferTarget.PAID_EXPIRED.value)

    return segments
