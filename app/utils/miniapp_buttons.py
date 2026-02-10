from aiogram import types
from aiogram.types import InlineKeyboardButton

from app.config import settings


def build_miniapp_or_callback_button(
    text: str,
    *,
    callback_data: str,
) -> InlineKeyboardButton:
    """Create a button that opens the miniapp or falls back to a callback.

    In text menu mode, if ``MINIAPP_CUSTOM_URL`` is configured the button
    opens the full cabinet miniapp.  Otherwise (or outside text menu mode)
    the regular ``callback_data`` is used so the user stays in the bot.

    Only ``MINIAPP_CUSTOM_URL`` is considered here — the purchase-only URL
    (``MINIAPP_PURCHASE_URL``) is intentionally excluded because it cannot
    display subscription details and would load indefinitely.
    """

    if settings.is_text_main_menu_mode():
        miniapp_url = (settings.MINIAPP_CUSTOM_URL or '').strip()
        if miniapp_url:
            return InlineKeyboardButton(
                text=text,
                web_app=types.WebAppInfo(url=miniapp_url),
            )

    return InlineKeyboardButton(text=text, callback_data=callback_data)
