"""Lightweight in-process cache for per-section cabinet button styles.

Avoids circular imports between ``cabinet.routes`` and ``app.utils.miniapp_buttons``
by keeping the cache and its helpers in a dedicated module.
"""

import json
import logging

from app.database.database import AsyncSessionLocal


logger = logging.getLogger(__name__)

# ---- Defaults per section ------------------------------------------------

DEFAULT_BUTTON_STYLES: dict[str, dict] = {
    'home': {'style': 'primary', 'icon_custom_emoji_id': ''},
    'subscription': {'style': 'success', 'icon_custom_emoji_id': ''},
    'balance': {'style': 'primary', 'icon_custom_emoji_id': ''},
    'referral': {'style': 'success', 'icon_custom_emoji_id': ''},
    'support': {'style': 'primary', 'icon_custom_emoji_id': ''},
    'info': {'style': 'primary', 'icon_custom_emoji_id': ''},
    'admin': {'style': 'danger', 'icon_custom_emoji_id': ''},
}

SECTIONS = list(DEFAULT_BUTTON_STYLES.keys())

# Map callback_data values to their logical section name.
CALLBACK_TO_SECTION: dict[str, str] = {
    'menu_profile_unavailable': 'home',
    'back_to_menu': 'home',
    'menu_subscription': 'subscription',
    'subscription': 'subscription',
    'subscription_extend': 'subscription',
    'subscription_upgrade': 'subscription',
    'subscription_connect': 'subscription',
    'subscription_resume_checkout': 'subscription',
    'return_to_saved_cart': 'subscription',
    'menu_buy': 'subscription',
    'buy_traffic': 'subscription',
    'menu_balance': 'balance',
    'balance_topup': 'balance',
    'menu_referrals': 'referral',
    'menu_referral': 'referral',
    'menu_support': 'support',
    'menu_info': 'info',
    'admin_panel': 'admin',
}

# DB key used for storage.
BUTTON_STYLES_KEY = 'CABINET_BUTTON_STYLES'

# Valid Telegram Bot API style values.
VALID_STYLES = frozenset({'primary', 'success', 'danger'})

# All style values accepted by the admin API ('default' = no color, Telegram default).
ALLOWED_STYLE_VALUES = VALID_STYLES | {'default'}

# ---- Module-level cache ---------------------------------------------------

_cached_styles: dict[str, dict] | None = None


def get_cached_button_styles() -> dict[str, dict]:
    """Return the current merged config (DB overrides + defaults).

    If the cache has not been loaded yet, returns defaults.
    """
    if _cached_styles is not None:
        return _cached_styles
    return {section: {**cfg} for section, cfg in DEFAULT_BUTTON_STYLES.items()}


async def load_button_styles_cache() -> dict[str, dict]:
    """Load button styles from DB and refresh the module cache.

    Called at bot startup and after admin updates via the cabinet API.
    """
    global _cached_styles

    merged = {section: {**cfg} for section, cfg in DEFAULT_BUTTON_STYLES.items()}

    try:
        from sqlalchemy import select

        from app.database.models import SystemSetting

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(SystemSetting).where(SystemSetting.key == BUTTON_STYLES_KEY))
            setting = result.scalar_one_or_none()
            if setting and setting.value:
                db_data: dict = json.loads(setting.value)
                for section, overrides in db_data.items():
                    if section in merged and isinstance(overrides, dict):
                        if overrides.get('style') in ALLOWED_STYLE_VALUES:
                            merged[section]['style'] = overrides['style']
                        if isinstance(overrides.get('icon_custom_emoji_id'), str):
                            merged[section]['icon_custom_emoji_id'] = overrides['icon_custom_emoji_id']
    except Exception:
        logger.exception('Failed to load button styles from DB, using defaults')

    _cached_styles = merged
    logger.info('Button styles cache loaded: %s', list(merged.keys()))
    return merged
