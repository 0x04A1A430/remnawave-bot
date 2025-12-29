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

logger = logging.getLogger(__name__)

TRAFFIC_PRICES = get_traffic_prices()

class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:  # pragma: no cover - defensive fallback
        return "{" + key + "}"

def _format_text_with_placeholders(template: str, values: Dict[str, Any]) -> str:
    if not isinstance(template, str):
        return template

    safe_values = _SafeFormatDict()
    safe_values.update(values)

    try:
        return template.format_map(safe_values)
    except Exception:  # pragma: no cover - defensive logging
        logger.warning("Failed to format template '%s' with values %s", template, values)
        return template

def _get_addon_discount_percent_for_user(
        user: Optional[User],
        category: str,
        period_days_hint: Optional[int] = None,
) -> int:
    if user is None:
        return 0

    promo_group = user.get_primary_promo_group()
    if promo_group is None:
        return 0

    if not getattr(promo_group, "apply_discounts_to_addons", True):
        return 0

    try:
        return user.get_promo_discount(category, period_days_hint)
    except AttributeError:
        return 0

def _apply_addon_discount(
        user: Optional[User],
        category: str,
        amount: int,
        period_days_hint: Optional[int] = None,
) -> Dict[str, int]:
    percent = _get_addon_discount_percent_for_user(user, category, period_days_hint)
    discounted_amount, discount_value = apply_percentage_discount(amount, percent)

    return {
        "discounted": discounted_amount,
        "discount": discount_value,
        "percent": percent,
    }

def _get_promo_offer_discount_percent(user: Optional[User]) -> int:
    return get_user_active_promo_discount_percent(user)

def _apply_promo_offer_discount(user: Optional[User], amount: int) -> Dict[str, int]:
    percent = _get_promo_offer_discount_percent(user)

    if amount <= 0 or percent <= 0:
        return {"discounted": amount, "discount": 0, "percent": 0}

    discounted, discount_value = apply_percentage_discount(amount, percent)
    return {"discounted": discounted, "discount": discount_value, "percent": percent}

def _get_period_hint_from_subscription(subscription: Optional[Subscription]) -> Optional[int]:
    if not subscription:
        return None

    months_remaining = get_remaining_months(subscription.end_date)
    if months_remaining <= 0:
        return None

    return months_remaining * 30

def _apply_discount_to_monthly_component(
        amount_per_month: int,
        percent: int,
        months: int,
) -> Dict[str, int]:
    discounted_per_month, discount_per_month = apply_percentage_discount(amount_per_month, percent)

    return {
        "original_per_month": amount_per_month,
        "discounted_per_month": discounted_per_month,
        "discount_percent": max(0, min(100, percent)),
        "discount_per_month": discount_per_month,
        "total": discounted_per_month * months,
        "discount_total": discount_per_month * months,
    }

def update_traffic_prices():
    from app.config import refresh_traffic_prices
    refresh_traffic_prices()
    logger.info("🔄 TRAFFIC_PRICES обновлены из конфигурации")

def format_traffic_display(traffic_gb: int, is_fixed_mode: bool = None) -> str:
    if is_fixed_mode is None:
        is_fixed_mode = settings.is_traffic_fixed()

    if traffic_gb == 0:
        if is_fixed_mode:
            return "Безлимитный"
        else:
            return "Безлимитный"
    else:
        if is_fixed_mode:
            return f"{traffic_gb} ГБ"
        else:
            return f"{traffic_gb} ГБ"

def validate_traffic_price(gb: int) -> bool:
    from app.config import settings

    price = settings.get_traffic_price(gb)
    if gb == 0:
        return True

    return price > 0

def load_app_config() -> Dict[str, Any]:
    try:
        from app.config import settings
        config_path = settings.get_app_config_path()

        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            logger.error("Некорректный формат app-config.json: ожидается объект")
    except Exception as e:
        logger.error(f"Ошибка загрузки конфига приложений: {e}")

    return {}

def get_localized_value(values: Any, language: str, default_language: str = "en") -> str:
    if not isinstance(values, dict):
        return ""

    candidates: List[str] = []
    normalized_language = (language or "").strip().lower()

    if normalized_language:
        candidates.append(normalized_language)
        if "-" in normalized_language:
            candidates.append(normalized_language.split("-")[0])

    default_language = (default_language or "").strip().lower()
    if default_language and default_language not in candidates:
        candidates.append(default_language)

    for candidate in candidates:
        if not candidate:
            continue
        value = values.get(candidate)
        if isinstance(value, str) and value.strip():
            return value

    for value in values.values():
        if isinstance(value, str) and value.strip():
            return value

    return ""

def get_step_description(app: Dict[str, Any], step_key: str, language: str) -> str:
    if not isinstance(app, dict):
        return ""

    step = app.get(step_key)
    if not isinstance(step, dict):
        return ""

    description = step.get("description")
    return get_localized_value(description, language)

def format_additional_section(additional: Any, texts, language: str) -> str:
    if not isinstance(additional, dict):
        return ""

    title = get_localized_value(additional.get("title"), language)
    description = get_localized_value(additional.get("description"), language)

    parts: List[str] = []

    if title:
        parts.append(
            texts.t(
                "SUBSCRIPTION_ADDITIONAL_STEP_TITLE",
                "<b>{title}:</b>",
            ).format(title=title)
        )

    if description:
        parts.append(description)

    return "\n".join(parts)

def build_redirect_link(target_link: Optional[str], template: Optional[str]) -> Optional[str]:
    if not target_link or not template:
        return None

    normalized_target = str(target_link).strip()
    normalized_template = str(template).strip()

    if not normalized_target or not normalized_template:
        return None

    encoded_target = quote(normalized_target, safe="")
    result = normalized_template
    replaced = False

    replacements = [
        ("{subscription_link}", encoded_target),
        ("{link}", encoded_target),
        ("{subscription_link_raw}", normalized_target),
        ("{link_raw}", normalized_target),
    ]

    for placeholder, replacement in replacements:
        if placeholder in result:
            result = result.replace(placeholder, replacement)
            replaced = True

    if not replaced:
        result = f"{result}{encoded_target}"

    return result

def get_apps_for_device(device_type: str, language: str = "ru") -> List[Dict[str, Any]]:
    config = load_app_config()
    platforms = config.get("platforms", {}) if isinstance(config, dict) else {}

    if not isinstance(platforms, dict):
        return []

    device_mapping = {
        'ios': 'ios',
        'android': 'android',
        'windows': 'windows',
        'mac': 'macos',
        'tv': 'androidTV',
        'appletv': 'appleTV',
        'apple_tv': 'appleTV',
    }

    config_key = device_mapping.get(device_type, device_type)
    apps = platforms.get(config_key, [])
    return apps if isinstance(apps, list) else []

def get_device_name(device_type: str, language: str = "ru") -> str:
    names = {
        'ios': 'iPhone/iPad',
        'android': 'Android',
        'windows': 'Windows',
        'mac': 'macOS',
        'tv': 'Android TV',
        'appletv': 'Apple TV',
        'apple_tv': 'Apple TV',
    }

    return names.get(device_type, device_type)

def create_deep_link(app: Dict[str, Any], subscription_url: str) -> Optional[str]:
    if not subscription_url:
        return None

    if not isinstance(app, dict):
        return subscription_url

    scheme = str(app.get("urlScheme", "")).strip()
    payload = subscription_url

    if app.get("isNeedBase64Encoding"):
        try:
            payload = base64.b64encode(subscription_url.encode("utf-8")).decode("utf-8")
        except Exception as exc:
            logger.warning(
                "Не удалось закодировать ссылку подписки в base64 для приложения %s: %s",
                app.get("id"),
                exc,
            )
            payload = subscription_url

    scheme_link = f"{scheme}{payload}" if scheme else None

    template = settings.get_happ_cryptolink_redirect_template()
    redirect_link = build_redirect_link(scheme_link, template) if scheme_link and template else None

    return redirect_link or scheme_link or subscription_url

def get_reset_devices_confirm_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, сбросить все устройства",
                callback_data="confirm_reset_devices"
            )
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="menu_subscription")
        ]
    ])

def get_traffic_switch_keyboard(
        current_traffic_gb: int,
        language: str = "ru",
        subscription_end_date: datetime = None,
        discount_percent: int = 0,
        base_traffic_gb: int = None,
) -> InlineKeyboardMarkup:
    from app.config import settings

    # Если базовый трафик не передан, используем текущий
    # (для обратной совместимости и случаев без докупленного трафика)
    if base_traffic_gb is None:
        base_traffic_gb = current_traffic_gb

    months_multiplier = 1
    period_text = ""
    if subscription_end_date:
        months_multiplier = get_remaining_months(subscription_end_date)
        if months_multiplier > 1:
            period_text = f" (за {months_multiplier} мес)"

    packages = settings.get_traffic_packages()
    enabled_packages = [pkg for pkg in packages if pkg['enabled']]

    # Используем базовый трафик для определения цены текущего пакета
    current_price_per_month = settings.get_traffic_price(base_traffic_gb)
    discounted_current_per_month, _ = apply_percentage_discount(
        current_price_per_month,
        discount_percent,
    )

    buttons = []

    for package in enabled_packages:
        gb = package['gb']
        price_per_month = package['price']
        discounted_price_per_month, _ = apply_percentage_discount(
            price_per_month,
            discount_percent,
        )

        price_diff_per_month = discounted_price_per_month - discounted_current_per_month
        total_price_diff = price_diff_per_month * months_multiplier

        # Сравниваем с базовым трафиком (без докупленного)
        if gb == base_traffic_gb:
            emoji = "✅"
            action_text = " (текущий)"
            price_text = ""
        elif total_price_diff > 0:
            emoji = "⬆️"
            action_text = ""
            price_text = f" (+{total_price_diff // 100}₽{period_text})"
            if discount_percent > 0:
                discount_total = (
                        (price_per_month - current_price_per_month) * months_multiplier
                        - total_price_diff
                )
                if discount_total > 0:
                    price_text += f" (скидка {discount_percent}%: -{discount_total // 100}₽)"
        elif total_price_diff < 0:
            emoji = "⬇️"
            action_text = ""
            price_text = " (без возврата)"
        else:
            emoji = "🔄"
            action_text = ""
            price_text = " (бесплатно)"

        if gb == 0:
            traffic_text = "Безлимит"
        else:
            traffic_text = f"{gb} ГБ"

        button_text = f"{emoji} {traffic_text}{action_text}{price_text}"

        buttons.append([
            InlineKeyboardButton(text=button_text, callback_data=f"switch_traffic_{gb}")
        ])

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад" if language == "ru" else "⬅️ Back",
            callback_data="subscription_settings"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_confirm_switch_traffic_keyboard(
        new_traffic_gb: int,
        price_difference: int,
        language: str = "ru"
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить переключение",
                callback_data=f"confirm_switch_traffic_{new_traffic_gb}_{price_difference}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="subscription_settings"
            )
        ]
    ])
