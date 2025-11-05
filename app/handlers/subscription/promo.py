import base64
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional
from urllib.parse import quote
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings, PERIOD_PRICES, get_traffic_prices
from app.database.crud.discount_offer import (
    get_offer_by_id,
    mark_offer_claimed,
)
from app.database.crud.promo_offer_template import get_promo_offer_template_by_id
from app.database.crud.subscription import (
    create_trial_subscription,
    create_paid_subscription, add_subscription_traffic, add_subscription_devices,
    update_subscription_autopay
)
from app.database.crud.transaction import create_transaction
from app.database.crud.user import subtract_user_balance
from app.database.models import (
    User, TransactionType, SubscriptionStatus,
    Subscription
)
from app.keyboards.inline import (
    get_subscription_keyboard, get_trial_keyboard,
    get_subscription_period_keyboard, get_traffic_packages_keyboard,
    get_countries_keyboard, get_devices_keyboard,
    get_subscription_confirm_keyboard, get_autopay_keyboard,
    get_autopay_days_keyboard, get_back_keyboard,
    get_add_traffic_keyboard,
    get_change_devices_keyboard, get_reset_traffic_confirm_keyboard,
    get_manage_countries_keyboard,
    get_device_selection_keyboard, get_connection_guide_keyboard,
    get_app_selection_keyboard, get_specific_app_keyboard,
    get_updated_subscription_settings_keyboard, get_insufficient_balance_keyboard,
    get_extend_subscription_keyboard_with_prices, get_confirm_change_devices_keyboard,
    get_devices_management_keyboard, get_device_management_help_keyboard,
    get_happ_cryptolink_keyboard,
    get_happ_download_platform_keyboard, get_happ_download_link_keyboard,
    get_happ_download_button_row,
    get_payment_methods_keyboard_with_cart,
    get_subscription_confirm_keyboard_with_cart,
    get_insufficient_balance_keyboard_with_cart
)
from app.localization.texts import get_texts
from app.services.admin_notification_service import AdminNotificationService
from app.services.remnawave_service import RemnaWaveService
from app.services.subscription_checkout_service import (
    clear_subscription_checkout_draft,
    get_subscription_checkout_draft,
    save_subscription_checkout_draft,
    should_offer_checkout_resume,
)
from app.services.subscription_service import SubscriptionService
from app.utils.miniapp_buttons import build_miniapp_or_callback_button
from app.services.promo_offer_service import promo_offer_service
from app.states import SubscriptionStates
from app.utils.pagination import paginate_list
from app.utils.pricing_utils import (
    calculate_months_from_days,
    get_remaining_months,
    calculate_prorated_price,
    validate_pricing_calculation,
    format_period_description,
    apply_percentage_discount,
)
from app.utils.subscription_utils import (
    get_display_subscription_link,
    get_happ_cryptolink_redirect_link,
    convert_subscription_link_to_happ_scheme,
)
from app.utils.promo_offer import (
    build_promo_offer_hint,
    get_user_active_promo_discount_percent,
)

from .common import _format_text_with_placeholders

async def _get_promo_offer_hint(
        db: AsyncSession,
        db_user: User,
        texts,
        percent: Optional[int] = None,
) -> Optional[str]:
    return await build_promo_offer_hint(db, db_user, texts, percent)

async def _build_promo_group_discount_text(
        db_user: User,
        periods: Optional[List[int]] = None,
        texts=None,
) -> str:
    promo_group = db_user.get_primary_promo_group()

    if not promo_group:
        return ""

    if texts is None:
        texts = get_texts(db_user.language)

    service_lines: List[str] = []

    if promo_group.server_discount_percent > 0:
        service_lines.append(
            texts.PROMO_GROUP_DISCOUNT_SERVERS.format(
                percent=promo_group.server_discount_percent
            )
        )

    if promo_group.traffic_discount_percent > 0:
        service_lines.append(
            texts.PROMO_GROUP_DISCOUNT_TRAFFIC.format(
                percent=promo_group.traffic_discount_percent
            )
        )

    if promo_group.device_discount_percent > 0:
        service_lines.append(
            texts.PROMO_GROUP_DISCOUNT_DEVICES.format(
                percent=promo_group.device_discount_percent
            )
        )

    period_lines: List[str] = []

    period_candidates: set[int] = set(periods or [])

    raw_period_discounts = getattr(promo_group, "period_discounts", None)
    if isinstance(raw_period_discounts, dict):
        for key in raw_period_discounts.keys():
            try:
                period_candidates.add(int(key))
            except (TypeError, ValueError):
                continue

    for period_days in sorted(period_candidates):
        percent = promo_group.get_discount_percent("period", period_days)

        if percent <= 0:
            continue

        period_display = format_period_description(period_days, db_user.language)
        period_lines.append(
            texts.PROMO_GROUP_PERIOD_DISCOUNT_ITEM.format(
                period=period_display,
                percent=percent,
            )
        )

    if not service_lines and not period_lines:
        return ""

    lines: List[str] = [texts.PROMO_GROUP_DISCOUNTS_HEADER]

    if service_lines:
        lines.extend(service_lines)

    if period_lines:
        if service_lines:
            lines.append("")

        lines.append(texts.PROMO_GROUP_PERIOD_DISCOUNTS_HEADER)
        lines.extend(period_lines)

    return "\n".join(lines)

async def claim_discount_offer(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession,
):
    texts = get_texts(db_user.language)

    try:
        offer_id = int(callback.data.split("_")[-1])
    except (ValueError, AttributeError):
        await callback.answer(
            texts.get("DISCOUNT_CLAIM_NOT_FOUND", "❌ Предложение не найдено"),
            show_alert=True,
        )
        return

    offer = await get_offer_by_id(db, offer_id)
    if not offer or offer.user_id != db_user.id:
        await callback.answer(
            texts.get("DISCOUNT_CLAIM_NOT_FOUND", "❌ Предложение не найдено"),
            show_alert=True,
        )
        return

    now = datetime.utcnow()
    if offer.claimed_at is not None:
        await callback.answer(
            texts.get("DISCOUNT_CLAIM_ALREADY", "ℹ️ Скидка уже была активирована"),
            show_alert=True,
        )
        return

    if not offer.is_active or offer.expires_at <= now:
        offer.is_active = False
        await db.commit()
        await callback.answer(
            texts.get("DISCOUNT_CLAIM_EXPIRED", "⚠️ Время действия предложения истекло"),
            show_alert=True,
        )
        return

    effect_type = (offer.effect_type or "percent_discount").lower()
    if effect_type == "balance_bonus":
        effect_type = "percent_discount"

    if effect_type == "test_access":
        success, newly_added, expires_at, error_code = await promo_offer_service.grant_test_access(
            db,
            db_user,
            offer,
        )

        if not success:
            if error_code == "subscription_missing":
                error_message = texts.get(
                    "TEST_ACCESS_NO_SUBSCRIPTION",
                    "❌ Для активации предложения необходима действующая подписка.",
                )
            elif error_code == "squads_missing":
                error_message = texts.get(
                    "TEST_ACCESS_NO_SQUADS",
                    "❌ Не удалось определить список серверов для теста. Обратитесь к администратору.",
                )
            elif error_code == "already_connected":
                error_message = texts.get(
                    "TEST_ACCESS_ALREADY_CONNECTED",
                    "ℹ️ Этот сервер уже подключен к вашей подписке.",
                )
            elif error_code == "remnawave_sync_failed":
                error_message = texts.get(
                    "TEST_ACCESS_REMNAWAVE_ERROR",
                    "❌ Не удалось подключить серверы. Попробуйте позже или обратитесь в поддержку.",
                )
            else:
                error_message = texts.get(
                    "TEST_ACCESS_UNKNOWN_ERROR",
                    "❌ Не удалось активировать предложение. Попробуйте позже.",
                )
            await callback.answer(error_message, show_alert=True)
            return

        await mark_offer_claimed(
            db,
            offer,
            details={
                "context": "test_access_claim",
                "new_squads": newly_added,
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
        )

        expires_text = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else ""
        success_message = texts.get(
            "TEST_ACCESS_ACTIVATED_MESSAGE",
            "🎉 Тестовые сервера подключены! Доступ активен до {expires_at}.",
        ).format(expires_at=expires_text)

        popup_text = texts.get("TEST_ACCESS_ACTIVATED_POPUP", "✅ Доступ выдан!")
        await callback.answer(popup_text, show_alert=True)
        back_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.get("BACK_TO_MENU", "🏠 В главное меню"),
                        callback_data="back_to_menu",
                    )
                ]
            ]
        )
        await callback.message.answer(success_message, reply_markup=back_keyboard)
        return

    discount_percent = int(offer.discount_percent or 0)
    if discount_percent <= 0:
        await callback.answer(
            texts.get("DISCOUNT_CLAIM_ERROR", "❌ Не удалось активировать скидку. Попробуйте позже."),
            show_alert=True,
        )
        return

    db_user.promo_offer_discount_percent = discount_percent
    db_user.promo_offer_discount_source = offer.notification_type
    db_user.updated_at = now

    extra_data = offer.extra_data or {}
    raw_duration = extra_data.get("active_discount_hours")
    template_id = extra_data.get("template_id")

    if raw_duration in (None, "") and template_id:
        try:
            template = await get_promo_offer_template_by_id(db, int(template_id))
        except (ValueError, TypeError):
            template = None
        if template and template.active_discount_hours:
            raw_duration = template.active_discount_hours

    try:
        duration_hours = int(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError):
        duration_hours = None

    if duration_hours and duration_hours > 0:
        discount_expires_at = now + timedelta(hours=duration_hours)
    else:
        discount_expires_at = None

    db_user.promo_offer_discount_expires_at = discount_expires_at

    await mark_offer_claimed(
        db,
        offer,
        details={
            "context": "discount_claim",
            "discount_percent": discount_percent,
            "discount_expires_at": discount_expires_at.isoformat() if discount_expires_at else None,
        },
    )
    await db.refresh(db_user)

    success_template = texts.get(
        "DISCOUNT_CLAIM_SUCCESS",
        "🎉 Скидка {percent}% активирована! Она автоматически применится при следующей оплате.",
    )

    expires_text = (
        discount_expires_at.strftime("%d.%m.%Y %H:%M") if discount_expires_at else ""
    )

    format_values: Dict[str, Any] = {"percent": discount_percent}

    if duration_hours and duration_hours > 0:
        format_values.setdefault("hours", duration_hours)
        format_values.setdefault("duration_hours", duration_hours)

    if discount_expires_at:
        format_values.setdefault("expires_at", expires_text)
        format_values.setdefault("expires_at_iso", discount_expires_at.isoformat())
        try:
            expires_timestamp = int(discount_expires_at.timestamp())
        except (OverflowError, OSError, ValueError):
            expires_timestamp = None
        if expires_timestamp:
            format_values.setdefault("expires_at_ts", expires_timestamp)
        remaining_hours = int((discount_expires_at - now).total_seconds() // 3600)
        if remaining_hours > 0:
            format_values.setdefault("expires_in_hours", remaining_hours)

    amount_text = ""
    if isinstance(extra_data, dict):
        raw_amount_text = (
            extra_data.get("amount_text")
            or extra_data.get("discount_amount_text")
            or extra_data.get("formatted_amount")
        )
        if isinstance(raw_amount_text, str) and raw_amount_text.strip():
            amount_text = raw_amount_text.strip()
        else:
            raw_amount = extra_data.get("amount") or extra_data.get("discount_amount")
            if isinstance(raw_amount, (int, float)):
                amount_text = settings.format_price(int(raw_amount))
            elif isinstance(raw_amount, str) and raw_amount.strip():
                amount_text = raw_amount.strip()

        if not amount_text:
            for key in ("discount_amount_kopeks", "amount_kopeks", "bonus_amount_kopeks"):
                maybe_amount = extra_data.get(key)
                try:
                    amount_value = int(maybe_amount)
                except (TypeError, ValueError):
                    continue
                if amount_value > 0:
                    amount_text = settings.format_price(amount_value)
                    break

        for key, value in extra_data.items():
            if (
                isinstance(key, str)
                and key.isidentifier()
                and key not in format_values
                and isinstance(value, (str, int, float))
            ):
                format_values[key] = value

    if not amount_text:
        try:
            bonus_amount = int(getattr(offer, "bonus_amount_kopeks", 0))
        except (TypeError, ValueError):
            bonus_amount = 0
        if bonus_amount > 0:
            amount_text = settings.format_price(bonus_amount)

    if amount_text:
        format_values.setdefault("amount", amount_text)

    success_message = _format_text_with_placeholders(success_template, format_values)

    await callback.answer("✅ Скидка активирована!", show_alert=True)

    offer_type = None
    if isinstance(extra_data, dict):
        offer_type = extra_data.get("offer_type")

    subscription = getattr(db_user, "subscription", None)

    if offer_type == "purchase_discount":
        button_text = texts.get("MENU_BUY_SUBSCRIPTION", "💎 Купить подписку")
        button_callback = "subscription_upgrade"
    elif offer_type == "extend_discount":
        button_text = texts.get("SUBSCRIPTION_EXTEND", "💎 Продлить подписку")
        button_callback = "subscription_extend"
    else:
        has_active_paid_subscription = bool(
            subscription
            and getattr(subscription, "is_active", False)
            and not getattr(subscription, "is_trial", False)
        )

        if has_active_paid_subscription:
            button_text = texts.get("SUBSCRIPTION_EXTEND", "💎 Продлить подписку")
            button_callback = "subscription_extend"
        else:
            button_text = texts.get("MENU_BUY_SUBSCRIPTION", "💎 Купить подписку")
            button_callback = "subscription_upgrade"

    buy_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                build_miniapp_or_callback_button(
                    text=button_text,
                    callback_data=button_callback,
                )
            ]
        ]
    )
    await callback.message.answer(success_message, reply_markup=buy_keyboard)

async def handle_promo_offer_close(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession,
):
    try:
        await callback.message.delete()
    except Exception:
        try:
            await callback.message.edit_reply_markup()
        except Exception:
            pass

    await callback.answer()
