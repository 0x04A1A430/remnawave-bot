from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.subscription import (
    create_trial_subscription,
    decrement_subscription_server_counts,
)
from app.database.crud.user import add_user_balance, subtract_user_balance
from app.database.models import Subscription, TransactionType, User
from app.keyboards.inline import get_subscription_keyboard
from app.localization.texts import get_texts
from app.services.remnawave_service import RemnaWaveConfigurationError
from app.services.subscription_service import SubscriptionService
from app.utils.subscription_utils import get_display_subscription_link

if TYPE_CHECKING:  # pragma: no cover - only for type checking
    from aiogram import Bot


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


@dataclass(slots=True)
class TrialActivationReversionResult:
    refunded: bool = True
    subscription_rolled_back: bool = True


@dataclass(slots=True)
class TrialAutoActivationResult:
    success: bool
    subscription: Optional[Subscription] = None
    charged_amount: int = 0
    reason: Optional[str] = None


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


async def refund_trial_activation_charge(
    db: AsyncSession,
    user: User,
    amount_kopeks: int,
    *,
    description: Optional[str] = None,
) -> bool:
    """Refunds a previously charged trial activation amount back to the user."""

    if amount_kopeks <= 0:
        return True

    refund_description = description or "Возврат оплаты за активацию триальной подписки"

    success = await add_user_balance(
        db,
        user,
        amount_kopeks,
        refund_description,
        transaction_type=TransactionType.REFUND,
    )

    if not success:
        logger.error(
            "Failed to refund %s kopeks for user %s during trial activation rollback",
            amount_kopeks,
            getattr(user, "id", "<unknown>"),
        )

    return success


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


async def revert_trial_activation(
    db: AsyncSession,
    user: User,
    subscription: Optional[Subscription],
    charged_amount: int,
    *,
    refund_description: Optional[str] = None,
) -> TrialActivationReversionResult:
    """Rolls back a trial subscription and refunds any charged amount."""

    rollback_success = await rollback_trial_subscription_activation(db, subscription)
    refund_success = await refund_trial_activation_charge(
        db,
        user,
        charged_amount,
        description=refund_description,
    )

    try:
        await db.refresh(user)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.warning(
            "Failed to refresh user %s after reverting trial activation: %s",
            getattr(user, "id", "<unknown>"),
            error,
        )

    return TrialActivationReversionResult(
        refunded=refund_success,
        subscription_rolled_back=rollback_success,
    )


async def mark_trial_activation_pending(db: AsyncSession, user: User) -> None:
    """Marks that the user attempted to activate a trial without sufficient funds."""

    if getattr(user, "trial_activation_pending", False):
        return

    user.trial_activation_pending = True
    user.updated_at = datetime.utcnow()

    try:
        await db.commit()
        await db.refresh(user)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to mark trial activation pending for user %s: %s",
            getattr(user, "id", "<unknown>"),
            error,
        )
        await db.rollback()
        raise


async def clear_trial_activation_pending(db: AsyncSession, user: User) -> None:
    """Clears the pending trial activation flag for the user."""

    if not getattr(user, "trial_activation_pending", False):
        return

    user.trial_activation_pending = False
    user.updated_at = datetime.utcnow()

    try:
        await db.commit()
        await db.refresh(user)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to clear trial activation pending flag for user %s: %s",
            getattr(user, "id", "<unknown>"),
            error,
        )
        await db.rollback()
        raise


async def maybe_activate_trial_after_topup(
    db: AsyncSession,
    user: User,
    *,
    bot: Optional["Bot"] = None,
) -> Optional[TrialAutoActivationResult]:
    """Automatically activates a trial after a successful top-up if pending."""

    if not getattr(user, "trial_activation_pending", False):
        return None

    logger.info(
        "Detected pending trial activation for user %s after top-up. Attempting activation.",
        getattr(user, "id", "<unknown>"),
    )

    if getattr(user, "subscription", None) or getattr(user, "has_had_paid_subscription", False):
        try:
            await clear_trial_activation_pending(db, user)
        except Exception:  # pragma: no cover - defensive logging
            logger.warning(
                "Failed to clear trial activation pending flag for user %s during eligibility check",
                getattr(user, "id", "<unknown>"),
            )
        return TrialAutoActivationResult(success=False, reason="not_eligible")

    try:
        preview_trial_activation_charge(user)
    except TrialPaymentInsufficientFunds:
        logger.info(
            "User %s still lacks funds for paid trial activation after top-up",
            getattr(user, "id", "<unknown>"),
        )
        return TrialAutoActivationResult(success=False, reason="insufficient_funds")

    forced_devices = None
    if not settings.is_devices_selection_enabled():
        forced_devices = settings.get_disabled_mode_device_limit()

    try:
        subscription = await create_trial_subscription(
            db,
            user.id,
            device_limit=forced_devices,
        )
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to auto-create trial subscription for user %s: %s",
            getattr(user, "id", "<unknown>"),
            error,
        )
        return TrialAutoActivationResult(success=False, reason="subscription_creation_failed")

    charged_amount = 0
    try:
        charged_amount = await charge_trial_activation_if_required(
            db,
            user,
            description="Активация триала после пополнения баланса",
        )
    except TrialPaymentInsufficientFunds as error:
        await rollback_trial_subscription_activation(db, subscription)
        await db.refresh(user)
        logger.error(
            "Balance check failed after auto trial creation for user %s: %s",
            getattr(user, "id", "<unknown>"),
            error,
        )
        return TrialAutoActivationResult(success=False, reason="insufficient_funds")
    except TrialPaymentChargeFailed as error:
        await rollback_trial_subscription_activation(db, subscription)
        await db.refresh(user)
        logger.error(
            "Failed to charge balance for auto trial activation %s: %s",
            getattr(subscription, "id", "<unknown>"),
            error,
        )
        return TrialAutoActivationResult(success=False, reason="charge_failed")

    subscription_service = SubscriptionService()
    try:
        await subscription_service.create_remnawave_user(db, subscription)
    except RemnaWaveConfigurationError as error:  # pragma: no cover - configuration issues
        logger.error(
            "RemnaWave configuration error during auto trial activation for user %s: %s",
            getattr(user, "id", "<unknown>"),
            error,
        )
        await revert_trial_activation(
            db,
            user,
            subscription,
            charged_amount,
            refund_description="Возврат оплаты за автоматическую активацию триала",
        )
        return TrialAutoActivationResult(success=False, reason="remnawave_configuration_error")
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to create RemnaWave user for auto trial activation %s: %s",
            getattr(subscription, "id", "<unknown>"),
            error,
        )
        await revert_trial_activation(
            db,
            user,
            subscription,
            charged_amount,
            refund_description="Возврат оплаты за автоматическую активацию триала",
        )
        return TrialAutoActivationResult(success=False, reason="remnawave_provisioning_failed")

    await db.refresh(subscription)
    await db.refresh(user)

    try:
        await clear_trial_activation_pending(db, user)
    except Exception:  # pragma: no cover - defensive logging
        logger.warning(
            "Failed to clear trial activation pending flag for user %s after auto activation",
            getattr(user, "id", "<unknown>"),
        )

    result = TrialAutoActivationResult(
        success=True,
        subscription=subscription,
        charged_amount=charged_amount,
    )

    if bot is not None:
        await _notify_user_about_auto_trial(bot, user, subscription, charged_amount)
        await _notify_admin_about_auto_trial(bot, db, user, subscription, charged_amount)

    return result


async def _notify_user_about_auto_trial(
    bot: "Bot",
    user: User,
    subscription: Subscription,
    charged_amount: int,
) -> None:
    texts = get_texts(getattr(user, "language", "ru"))

    lines = [
        texts.TRIAL_ACTIVATED,
        texts.t(
            "TRIAL_ACTIVATED_AUTOMATICALLY",
            "🎁 Триал активирован автоматически после пополнения баланса!",
        ),
    ]

    subscription_link = get_display_subscription_link(subscription)
    if subscription_link and not settings.should_hide_subscription_link():
        lines.append(
            texts.t(
                "SUBSCRIPTION_IMPORT_LINK_SECTION",
                "🔗 <b>Ваша ссылка для импорта:</b>\n<code>{subscription_url}</code>",
            ).format(subscription_url=subscription_link)
        )

    lines.append(
        texts.t(
            "TRIAL_AUTOMATIC_MANAGE_NOTE",
            "ℹ️ Управляйте подпиской через кнопки ниже или в разделе \"Моя подписка\".",
        )
    )

    instruction_prompt = texts.t(
        "SUBSCRIPTION_IMPORT_INSTRUCTION_PROMPT",
        "📱 Нажмите кнопку ниже, чтобы получить инструкцию по настройке VPN на вашем устройстве",
    )
    if instruction_prompt:
        lines.append(instruction_prompt)

    if charged_amount > 0:
        lines.append(
            texts.t(
                "TRIAL_PAYMENT_CHARGED_NOTE",
                "💳 С вашего баланса списано {amount}.",
            ).format(amount=settings.format_price(charged_amount))
        )

    message = "\n\n".join(lines)

    keyboard = get_subscription_keyboard(
        getattr(user, "language", "ru"),
        has_subscription=True,
        is_trial=True,
        subscription=subscription,
    )

    try:
        await bot.send_message(
            getattr(user, "telegram_id"),
            message,
            reply_markup=keyboard,
        )
    except Exception as error:  # pragma: no cover - best-effort notification
        logger.warning(
            "Failed to notify user %s about automatic trial activation: %s",
            getattr(user, "id", "<unknown>"),
            error,
        )


async def _notify_admin_about_auto_trial(
    bot: "Bot",
    db: AsyncSession,
    user: User,
    subscription: Subscription,
    charged_amount: int,
) -> None:
    try:
        from app.services.admin_notification_service import AdminNotificationService
    except Exception as import_error:  # pragma: no cover - defensive logging
        logger.warning(
            "Failed to import AdminNotificationService for auto trial notification: %s",
            import_error,
        )
        return

    try:
        notification_service = AdminNotificationService(bot)
        await notification_service.send_trial_activation_notification(
            db,
            user,
            subscription,
            charged_amount_kopeks=charged_amount,
        )
    except Exception as error:  # pragma: no cover - best-effort notification
        logger.error(
            "Failed to send admin notification about auto trial activation for user %s: %s",
            getattr(user, "id", "<unknown>"),
            error,
        )
