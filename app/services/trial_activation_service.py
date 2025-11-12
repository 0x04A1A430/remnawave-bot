from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.subscription import (
    create_trial_subscription,
    decrement_subscription_server_counts,
)
from app.database.crud.user import add_user_balance, subtract_user_balance
from app.database.models import Subscription, TransactionType, User
from app.localization.texts import get_texts
from app.services.admin_notification_service import AdminNotificationService
from app.services.remnawave_service import RemnaWaveConfigurationError
from app.services.subscription_service import SubscriptionService
from app.services.user_cart_service import user_cart_service
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


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
class TrialActivationResult:
    subscription: Subscription
    charged_amount: int
    remnawave_user: Optional[object]


class TrialActivationProvisioningError(Exception):
    """Raised when trial provisioning fails after initial subscription creation."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


INTENT_KEY_TEMPLATE = "trial_activation_intent:{user_id}"
INTENT_TTL_SECONDS = 24 * 60 * 60  # 24 hours


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


def _build_intent_key(user_id: int) -> str:
    return INTENT_KEY_TEMPLATE.format(user_id=user_id)


async def save_trial_activation_intent(
    user_id: int,
    *,
    required_amount: Optional[int] = None,
    balance_amount: Optional[int] = None,
    missing_amount: Optional[int] = None,
    ttl: Optional[int] = None,
) -> bool:
    """Persist the user's intention to activate a trial after balance top-up."""

    client = getattr(user_cart_service, "redis_client", None)
    if client is None:
        logger.warning(
            "Redis client is not available when saving trial activation intent for user %s",
            user_id,
        )
        return False

    payload: Dict[str, Any] = {
        "user_id": user_id,
        "required_amount": required_amount,
        "balance_amount": balance_amount,
        "missing_amount": missing_amount,
        "timestamp": datetime.utcnow().isoformat(),
    }

    payload = {key: value for key, value in payload.items() if value is not None}

    key = _build_intent_key(user_id)
    try:
        await client.setex(
            key,
            ttl or INTENT_TTL_SECONDS,
            json.dumps(payload, ensure_ascii=False),
        )
        logger.debug("Saved trial activation intent for user %s", user_id)
        return True
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to store trial activation intent for user %s: %s",
            user_id,
            error,
        )
        return False


async def get_trial_activation_intent(user_id: int) -> Optional[Dict[str, Any]]:
    client = getattr(user_cart_service, "redis_client", None)
    if client is None:
        return None

    key = _build_intent_key(user_id)

    try:
        raw_value = await client.get(key)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to load trial activation intent for user %s: %s",
            user_id,
            error,
        )
        return None

    if not raw_value:
        return None

    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8")

    try:
        data = json.loads(raw_value)
    except (TypeError, ValueError) as error:
        logger.warning(
            "Corrupted trial activation intent for user %s: %s", user_id, error
        )
        await clear_trial_activation_intent(user_id)
        return None

    if not isinstance(data, dict):
        await clear_trial_activation_intent(user_id)
        return None

    return data


async def clear_trial_activation_intent(user_id: int) -> bool:
    client = getattr(user_cart_service, "redis_client", None)
    if client is None:
        return False

    key = _build_intent_key(user_id)

    try:
        await client.delete(key)
        logger.debug("Cleared trial activation intent for user %s", user_id)
        return True
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to clear trial activation intent for user %s: %s",
            user_id,
            error,
        )
        return False


def _determine_revert_failure_reason(
    revert_result: TrialActivationReversionResult,
    charged_amount: int,
) -> str:
    if not revert_result.subscription_rolled_back:
        return "rollback_failed"
    if charged_amount > 0 and not revert_result.refunded:
        return "refund_failed"
    return "provisioning_failed"


def _build_default_keyboard(texts: Any) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.t("MY_SUBSCRIPTION_BUTTON", "📱 Моя подписка"),
                    callback_data="menu_subscription",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                    callback_data="back_to_menu",
                )
            ],
        ]
    )


def _build_insufficient_balance_keyboard(texts: Any) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.t("BALANCE_TOPUP_BUTTON", "💳 Пополнить баланс"),
                    callback_data="balance_topup",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                    callback_data="back_to_menu",
                )
            ],
        ]
    )


def _format_insufficient_funds_message(
    texts: Any, error: TrialPaymentInsufficientFunds
) -> str:
    required_label = settings.format_price(error.required_amount)
    balance_label = settings.format_price(error.balance_amount)
    missing_label = settings.format_price(error.missing_amount)

    return texts.t(
        "TRIAL_PAYMENT_INSUFFICIENT_FUNDS",
        "⚠️ Недостаточно средств для активации триала.\n"
        "Необходимо: {required}\nНа балансе: {balance}\n"
        "Не хватает: {missing}\n\nПополните баланс и попробуйте снова.",
    ).format(
        required=required_label,
        balance=balance_label,
        missing=missing_label,
    )


def _get_failure_message(texts: Any, reason: str) -> str:
    if reason == "rollback_failed":
        return texts.t(
            "TRIAL_ROLLBACK_FAILED",
            "Не удалось отменить активацию триала. Попробуйте позже.",
        )
    if reason == "refund_failed":
        return texts.t(
            "TRIAL_REFUND_FAILED",
            "Не удалось вернуть оплату за активацию триала. Свяжитесь с поддержкой.",
        )
    return texts.t(
        "TRIAL_PROVISIONING_FAILED",
        "Не удалось завершить активацию триала. Попробуйте позже.",
    )


async def _notify_insufficient_funds(
    bot: Optional[Any],
    user: User,
    texts: Any,
    error: TrialPaymentInsufficientFunds,
) -> None:
    if not bot:
        return

    message = _format_insufficient_funds_message(texts, error)
    keyboard = _build_insufficient_balance_keyboard(texts)

    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=message,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as send_error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to send insufficient funds notification to user %s: %s",
            getattr(user, "telegram_id", "<unknown>"),
            send_error,
        )


async def _notify_failure(
    bot: Optional[Any],
    user: User,
    texts: Any,
    reason: str,
) -> None:
    if not bot:
        return

    message = _get_failure_message(texts, reason)
    keyboard = _build_default_keyboard(texts)

    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=message,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as send_error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to send trial failure notification to user %s: %s",
            getattr(user, "telegram_id", "<unknown>"),
            send_error,
        )


async def _notify_payment_failure(
    bot: Optional[Any],
    user: User,
    texts: Any,
) -> None:
    if not bot:
        return

    message = texts.t(
        "TRIAL_PAYMENT_FAILED",
        "Не удалось списать средства для активации триала. Попробуйте позже.",
    )
    keyboard = _build_default_keyboard(texts)

    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=message,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as send_error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to send trial payment failure notification to user %s: %s",
            getattr(user, "telegram_id", "<unknown>"),
            send_error,
        )


async def _notify_success(
    bot: Optional[Any],
    user: User,
    texts: Any,
    charged_amount: int,
) -> None:
    if not bot:
        return

    message_parts = [
        texts.t(
            "TRIAL_AUTO_ACTIVATED_SUCCESS",
            "🎉 Триальная подписка активирована автоматически после пополнения баланса!",
        ),
        texts.t(
            "TRIAL_AUTO_ACTIVATED_HINT",
            "Откройте раздел \"Моя подписка\", чтобы получить ссылку и инструкции по подключению.",
        ),
    ]

    if charged_amount > 0:
        message_parts.append(
            texts.t(
                "TRIAL_PAYMENT_CHARGED_NOTE",
                "💳 С вашего баланса списано {amount}.",
            ).format(amount=settings.format_price(charged_amount))
        )

    keyboard = _build_default_keyboard(texts)

    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text="\n\n".join(message_parts),
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as send_error:  # pragma: no cover - defensive logging
        logger.error(
            "Failed to send trial success notification to user %s: %s",
            getattr(user, "telegram_id", "<unknown>"),
            send_error,
        )


async def auto_activate_trial_after_topup(
    db: AsyncSession,
    user: User,
    *,
    bot: Optional[Any] = None,
) -> bool:
    """Automatically activates a trial for users who attempted activation before top-up."""

    user_id = getattr(user, "id", None)
    if not user_id:
        return False

    intent = await get_trial_activation_intent(user_id)
    if not intent:
        return False

    if getattr(user, "subscription", None) or getattr(user, "has_had_paid_subscription", False):
        await clear_trial_activation_intent(user_id)
        return False

    texts = get_texts(getattr(user, "language", "ru"))

    try:
        preview_trial_activation_charge(user)
    except TrialPaymentInsufficientFunds as error:
        await save_trial_activation_intent(
            user_id,
            required_amount=error.required_amount,
            balance_amount=error.balance_amount,
            missing_amount=error.missing_amount,
        )
        await _notify_insufficient_funds(bot, user, texts, error)
        return False

    forced_devices = None
    if not settings.is_devices_selection_enabled():
        forced_devices = settings.get_disabled_mode_device_limit()

    subscription: Optional[Subscription] = None
    charged_amount = 0

    try:
        subscription = await create_trial_subscription(
            db,
            user_id,
            device_limit=forced_devices,
        )
        await db.refresh(user)

        charged_amount = await charge_trial_activation_if_required(
            db,
            user,
            description="Активация триала после пополнения баланса",
        )
    except TrialPaymentInsufficientFunds as error:
        rollback_success = await rollback_trial_subscription_activation(db, subscription)
        await db.refresh(user)

        if not rollback_success:
            await clear_trial_activation_intent(user_id)
            await _notify_failure(bot, user, texts, "rollback_failed")
            return False

        await save_trial_activation_intent(
            user_id,
            required_amount=error.required_amount,
            balance_amount=error.balance_amount,
            missing_amount=error.missing_amount,
        )
        await _notify_insufficient_funds(bot, user, texts, error)
        return False
    except TrialPaymentChargeFailed:
        rollback_success = await rollback_trial_subscription_activation(db, subscription)
        await db.refresh(user)
        await clear_trial_activation_intent(user_id)

        if rollback_success:
            await _notify_payment_failure(bot, user, texts)
        else:
            await _notify_failure(bot, user, texts, "rollback_failed")
        return False
    except Exception as error:
        logger.error(
            "Failed to create trial subscription automatically for user %s: %s",
            user_id,
            error,
            exc_info=True,
        )
        if subscription is not None:
            revert_result = await revert_trial_activation(
                db,
                user,
                subscription,
                charged_amount,
                refund_description="Возврат оплаты за автоматическую активацию триала",
            )
            await clear_trial_activation_intent(user_id)
            reason = _determine_revert_failure_reason(revert_result, charged_amount)
            await _notify_failure(bot, user, texts, reason)
        else:
            await clear_trial_activation_intent(user_id)
            await _notify_failure(bot, user, texts, "provisioning_failed")
        return False

    subscription_service = SubscriptionService()

    try:
        remnawave_user = await subscription_service.create_remnawave_user(
            db,
            subscription,
        )
    except RemnaWaveConfigurationError as error:
        logger.error(
            "RemnaWave configuration error during auto trial activation for user %s: %s",
            user_id,
            error,
        )
        revert_result = await revert_trial_activation(
            db,
            user,
            subscription,
            charged_amount,
            refund_description="Возврат оплаты за автоматическую активацию триала",
        )
        await clear_trial_activation_intent(user_id)
        reason = _determine_revert_failure_reason(revert_result, charged_amount)
        await _notify_failure(bot, user, texts, reason)
        return False
    except Exception as error:
        logger.error(
            "Failed to provision RemnaWave user during auto trial activation for user %s: %s",
            user_id,
            error,
            exc_info=True,
        )
        revert_result = await revert_trial_activation(
            db,
            user,
            subscription,
            charged_amount,
            refund_description="Возврат оплаты за автоматическую активацию триала",
        )
        await clear_trial_activation_intent(user_id)
        reason = _determine_revert_failure_reason(revert_result, charged_amount)
        await _notify_failure(bot, user, texts, reason)
        return False

    await db.refresh(user)
    await db.refresh(subscription)

    try:
        user.subscription = subscription
    except Exception:  # pragma: no cover - relationship safety
        pass

    await clear_trial_activation_intent(user_id)

    if bot:
        try:
            notification_service = AdminNotificationService(bot)
            await notification_service.send_trial_activation_notification(
                db,
                user,
                subscription,
                charged_amount_kopeks=charged_amount,
            )
        except Exception as notify_error:  # pragma: no cover - defensive logging
            logger.error(
                "Failed to send admin notification for auto trial activation (user %s): %s",
                user_id,
                notify_error,
            )

    await _notify_success(bot, user, texts, charged_amount)

    logger.info(
        "✅ Trial subscription activated automatically after top-up for user %s",
        user_id,
    )
    return True
