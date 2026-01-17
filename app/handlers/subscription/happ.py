import base64
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional
from urllib.parse import quote
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InaccessibleMessage
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

async def handle_happ_download_request(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)
    prompt_text = texts.t(
        "HAPP_DOWNLOAD_PROMPT",
        "📥 <b>Скачать Happ</b>\nВыберите ваше устройство:",
    )

    keyboard = get_happ_download_platform_keyboard(db_user.language)

    await callback.message.answer(prompt_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

async def handle_happ_download_platform_choice(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    # Проверяем, доступно ли сообщение для редактирования
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return

    platform = callback.data.split('_')[-1]
    if platform == "pc":
        platform = "windows"
    texts = get_texts(db_user.language)
    link = settings.get_happ_download_link(platform)

    if not link:
        await callback.answer(
            texts.t("HAPP_DOWNLOAD_LINK_NOT_SET", "❌ Ссылка для этого устройства не настроена"),
            show_alert=True,
        )
        return

    platform_names = {
        "ios": texts.t("HAPP_PLATFORM_IOS", "🍎 iOS"),
        "android": texts.t("HAPP_PLATFORM_ANDROID", "🤖 Android"),
        "macos": texts.t("HAPP_PLATFORM_MACOS", "🖥️ Mac OS"),
        "windows": texts.t("HAPP_PLATFORM_WINDOWS", "💻 Windows"),
    }

    link_text = texts.t(
        "HAPP_DOWNLOAD_LINK_MESSAGE",
        "⬇️ Скачайте Happ для {platform}:",
    ).format(platform=platform_names.get(platform, platform.upper()))

    keyboard = get_happ_download_link_keyboard(db_user.language, link)

    await callback.message.edit_text(link_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

async def handle_happ_download_close(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.answer()

async def handle_happ_download_back(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    # Проверяем, доступно ли сообщение для редактирования
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return

    texts = get_texts(db_user.language)
    prompt_text = texts.t(
        "HAPP_DOWNLOAD_PROMPT",
        "📥 <b>Скачать Happ</b>\nВыберите ваше устройство:",
    )

    keyboard = get_happ_download_platform_keyboard(db_user.language)

    await callback.message.edit_text(prompt_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()
