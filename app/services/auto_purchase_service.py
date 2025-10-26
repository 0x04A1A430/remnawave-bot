import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.server_squad import add_user_to_servers, get_server_ids_by_uuids
from app.database.crud.subscription import add_subscription_servers, create_paid_subscription
from app.database.crud.transaction import create_transaction
from app.database.crud.user import subtract_user_balance
from app.database.models import SubscriptionStatus, TransactionType, User
from app.keyboards.inline import get_insufficient_balance_keyboard_with_cart
from app.localization.texts import get_texts
from app.services.admin_notification_service import AdminNotificationService
from app.services.subscription_checkout_service import clear_subscription_checkout_draft
from app.services.subscription_service import SubscriptionService
from app.services.user_cart_service import user_cart_service
from app.utils.pricing_utils import format_period_description
from app.utils.subscription_utils import get_display_subscription_link
from app.utils.user_utils import mark_user_as_had_paid_subscription

logger = logging.getLogger(__name__)


@dataclass
class AutoPurchaseResult:
    triggered: bool
    success: bool


def _get_auto_purchase_status_lines(texts, enabled: bool) -> tuple[str, str]:
    status_text = texts.AUTO_PURCHASE_AFTER_TOPUP_STATUS.format(
        status=(
            texts.AUTO_PURCHASE_AFTER_TOPUP_STATUS_ENABLED
            if enabled
            else texts.AUTO_PURCHASE_AFTER_TOPUP_STATUS_DISABLED
        )
    )
    status_hint = (
        texts.AUTO_PURCHASE_AFTER_TOPUP_TOGGLED_ON
        if enabled
        else texts.AUTO_PURCHASE_AFTER_TOPUP_TOGGLED_OFF
    )
    return status_text, status_hint


def _build_autopurchase_failure_text(texts, total_price: int, balance: int, missing: int, enabled: bool) -> str:
    status_text, status_hint = _get_auto_purchase_status_lines(texts, enabled)
    return (
        f"{texts.AUTO_PURCHASE_AFTER_TOPUP_INSUFFICIENT}\n\n"
        f"Стоимость: {texts.format_price(total_price)}\n"
        f"На балансе: {texts.format_price(balance)}\n"
        f"Не хватает: {texts.format_price(missing)}\n\n"
        f"{status_text}\n{status_hint}"
    )


def _build_autopurchase_success_prefix(texts, language: str, period_days: int, final_price: int, discount_value: int) -> str:
    period_display = format_period_description(period_days, language)
    if discount_value > 0:
        return texts.AUTO_PURCHASE_AFTER_TOPUP_SUCCESS_WITH_DISCOUNT.format(
            period=period_display,
            amount=texts.format_price(final_price),
            discount=texts.format_price(discount_value),
        )
    return texts.AUTO_PURCHASE_AFTER_TOPUP_SUCCESS.format(
        period=period_display,
        amount=texts.format_price(final_price),
    )


async def try_auto_purchase_after_topup(
        db: AsyncSession,
        user: User,
        bot: Optional[Bot],
) -> AutoPurchaseResult:
    if not getattr(user, "auto_purchase_after_topup_enabled", False):
        return AutoPurchaseResult(triggered=False, success=False)

    cart_data = await user_cart_service.get_user_cart(user.id)
    if not cart_data:
        return AutoPurchaseResult(triggered=False, success=False)

    texts = get_texts(user.language)

    from app.handlers.subscription.pricing import _prepare_subscription_summary

    try:
        _, prepared_data = await _prepare_subscription_summary(user, cart_data, texts)
    except ValueError as error:
        logger.error("Не удалось восстановить корзину для автопокупки: %s", error)
        await user_cart_service.delete_user_cart(user.id)
        return AutoPurchaseResult(triggered=True, success=False)

    final_price = prepared_data.get('total_price', 0)
    promo_offer_discount_value = prepared_data.get('promo_offer_discount_value', 0)
    promo_offer_discount_percent = prepared_data.get('promo_offer_discount_percent', 0)

    await db.refresh(user)

    if user.balance_kopeks < final_price:
        missing = final_price - user.balance_kopeks
        failure_text = _build_autopurchase_failure_text(
            texts,
            final_price,
            user.balance_kopeks,
            missing,
            True,
        )
        if bot:
            try:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=failure_text,
                    parse_mode="HTML",
                    reply_markup=get_insufficient_balance_keyboard_with_cart(
                        user.language,
                        missing,
                        auto_purchase_enabled=True,
                    ),
                )
            except Exception as send_error:
                logger.error("Не удалось отправить уведомление об автопокупке: %s", send_error)
        return AutoPurchaseResult(triggered=True, success=False)

    success = await subtract_user_balance(
        db,
        user,
        final_price,
        f"Покупка подписки на {prepared_data['period_days']} дней (авто)",
        consume_promo_offer=promo_offer_discount_value > 0,
    )

    if not success:
        await db.refresh(user)
        missing = max(0, final_price - user.balance_kopeks)
        failure_text = _build_autopurchase_failure_text(
            texts,
            final_price,
            user.balance_kopeks,
            missing,
            True,
        )
        if bot:
            try:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=failure_text,
                    parse_mode="HTML",
                    reply_markup=get_insufficient_balance_keyboard_with_cart(
                        user.language,
                        missing,
                        auto_purchase_enabled=True,
                    ),
                )
            except Exception as send_error:
                logger.error("Не удалось отправить уведомление об автопокупке: %s", send_error)
        return AutoPurchaseResult(triggered=True, success=False)

    final_traffic_gb = prepared_data.get('final_traffic_gb', prepared_data.get('traffic_gb', 0))
    server_prices = prepared_data.get('server_prices_for_period', [])

    existing_subscription = user.subscription
    was_trial_conversion = False
    current_time = datetime.utcnow()

    if existing_subscription:
        bonus_period = timedelta()
        if existing_subscription.is_trial:
            was_trial_conversion = True
            trial_duration = (current_time - existing_subscription.start_date).days
            if settings.TRIAL_ADD_REMAINING_DAYS_TO_PAID and existing_subscription.end_date:
                remaining_trial_delta = existing_subscription.end_date - current_time
                if remaining_trial_delta.total_seconds() > 0:
                    bonus_period = remaining_trial_delta

        existing_subscription.is_trial = False
        existing_subscription.status = SubscriptionStatus.ACTIVE.value
        existing_subscription.traffic_limit_gb = final_traffic_gb
        existing_subscription.device_limit = prepared_data['devices']
        existing_subscription.connected_squads = prepared_data['countries']
        existing_subscription.start_date = current_time
        existing_subscription.end_date = current_time + timedelta(days=prepared_data['period_days']) + bonus_period
        existing_subscription.updated_at = current_time
        existing_subscription.traffic_used_gb = 0.0

        await db.commit()
        await db.refresh(existing_subscription)
        subscription = existing_subscription
    else:
        subscription = await create_paid_subscription(
            db=db,
            user_id=user.id,
            duration_days=prepared_data['period_days'],
            traffic_limit_gb=final_traffic_gb,
            device_limit=prepared_data['devices'],
            connected_squads=prepared_data['countries'],
        )

    await mark_user_as_had_paid_subscription(db, user)

    server_ids = await get_server_ids_by_uuids(db, prepared_data['countries'])
    if server_ids:
        await add_subscription_servers(db, subscription, server_ids, server_prices)
        await add_user_to_servers(db, server_ids)

    await db.refresh(user)

    subscription_service = SubscriptionService()
    if user.remnawave_uuid:
        remnawave_user = await subscription_service.update_remnawave_user(
            db,
            subscription,
            reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
            reset_reason="автопокупка подписки",
        )
    else:
        remnawave_user = await subscription_service.create_remnawave_user(
            db,
            subscription,
            reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
            reset_reason="автопокупка подписки",
        )
    if not remnawave_user:
        remnawave_user = await subscription_service.create_remnawave_user(
            db,
            subscription,
            reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
            reset_reason="автопокупка подписки (повтор)",
        )

    transaction = await create_transaction(
        db=db,
        user_id=user.id,
        type=TransactionType.SUBSCRIPTION_PAYMENT,
        amount_kopeks=final_price,
        description=f"Подписка на {prepared_data['period_days']} дней (авто)",
    )

    if bot:
        try:
            notification_service = AdminNotificationService(bot)
            await notification_service.send_subscription_purchase_notification(
                db,
                user,
                subscription,
                transaction,
                prepared_data['period_days'],
                was_trial_conversion,
            )
        except Exception as notify_error:
            logger.error("Ошибка отправки уведомления админам об автопокупке: %s", notify_error)

    await db.refresh(user)
    await db.refresh(subscription)

    subscription_link = get_display_subscription_link(subscription)
    hide_subscription_link = settings.should_hide_subscription_link()

    auto_prefix = _build_autopurchase_success_prefix(
        texts,
        user.language,
        prepared_data['period_days'],
        final_price,
        promo_offer_discount_value,
    )

    instruction_text = texts.t(
        "SUBSCRIPTION_IMPORT_INSTRUCTION_PROMPT",
        "📱 Нажмите кнопку ниже, чтобы получить инструкцию по настройке VPN на вашем устройстве",
    )
    success_text = f"{texts.SUBSCRIPTION_PURCHASED}\n\n{auto_prefix}\n\n{instruction_text}"

    if bot:
        rows: List[List[InlineKeyboardButton]] = []
        if subscription_link and not hide_subscription_link:
            rows.append([
                InlineKeyboardButton(
                    text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                    url=subscription_link,
                )
            ])
        rows.append([
            InlineKeyboardButton(text=texts.MENU_SUBSCRIPTION, callback_data="menu_subscription")
        ])
        rows.append([
            InlineKeyboardButton(text=texts.BACK_TO_MAIN_MENU_BUTTON, callback_data="back_to_menu")
        ])

        try:
            await bot.send_message(
                chat_id=user.telegram_id,
                text=success_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        except Exception as send_error:
            logger.error("Не удалось отправить сообщение об автопокупке: %s", send_error)

    await clear_subscription_checkout_draft(user.id)
    await user_cart_service.delete_user_cart(user.id)

    return AutoPurchaseResult(triggered=True, success=True)
