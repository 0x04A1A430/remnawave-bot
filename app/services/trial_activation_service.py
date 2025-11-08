from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.subscription import decrement_subscription_server_counts
from app.database.crud.user import subtract_user_balance
from app.database.models import Subscription, User


logger = logging.getLogger(__name__)


class TrialPaymentError(Exception):
    """Base exception for trial activation payment issues."""


@dataclass(slots=True)
class TrialPaymentInsufficientFunds(TrialPaymentError):
    required_amount: int
    balance_amount: int

    @property
    def missing_amount(self) -> int:
        return max(0, self.required_amount - self.balance_amount)


class TrialPaymentChargeFailed(TrialPaymentError):
    """Raised when balance charge could not be completed."""


def get_trial_activation_charge_amount() -> int:
    """Returns the configured activation charge in kopeks if payment is enabled."""

    if not settings.is_trial_paid_activation_enabled():
        return 0

    try:
        price_kopeks = int(settings.get_trial_activation_price() or 0)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        price_kopeks = 0

    return max(0, price_kopeks)


def preview_trial_activation_charge(user: User) -> int:
    """Validates that the user can afford the trial activation charge."""

    price_kopeks = get_trial_activation_charge_amount()
    if price_kopeks <= 0:
        return 0

    balance = int(getattr(user, "balance_kopeks", 0) or 0)
    if balance < price_kopeks:
        raise TrialPaymentInsufficientFunds(price_kopeks, balance)

    return price_kopeks


async def charge_trial_activation_if_required(
    db: AsyncSession,
    user: User,
    *,
    description: Optional[str] = None,
) -> int:
    """Charges the user's balance if paid trial activation is enabled.

    Returns the charged amount in kopeks. If payment is not required or the
    configured price is zero, the function returns ``0``.
    """

    price_kopeks = preview_trial_activation_charge(user)
    if price_kopeks <= 0:
        return 0

    charge_description = description or "Активация триальной подписки"

    success = await subtract_user_balance(
        db,
        user,
        price_kopeks,
        charge_description,
    )
    if not success:
        raise TrialPaymentChargeFailed()

    # subtract_user_balance обновляет пользователя, но на всякий случай приводим к int
    return int(price_kopeks)


async def rollback_trial_subscription_activation(
    db: AsyncSession,
    subscription: Optional[Subscription],
) -> bool:
    """Attempts to undo a previously created trial subscription.

    Returns ``True`` when the rollback succeeds or when ``subscription`` is
    falsy. In case of a database failure the function returns ``False`` after
    logging the error so callers can decide how to proceed.
    """

    if not subscription:
        return True

    try:
        await decrement_subscription_server_counts(db, subscription)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to decrement server counters during trial rollback for %s: %s",
            subscription.user_id,
            error,
        )

    try:
        await db.delete(subscription)
        await db.commit()
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to remove trial subscription %s after charge failure: %s",
            getattr(subscription, "id", "<unknown>"),
            error,
        )
        await db.rollback()
        return False

    return True
