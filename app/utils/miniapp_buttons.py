from aiogram import types
from aiogram.types import InlineKeyboardButton

from app.config import settings


DEFAULT_UNAVAILABLE_CALLBACK = "menu_profile_unavailable"


def build_miniapp_or_callback_button(
    text: str,
    *,
    callback_data: str,
    unavailable_callback: str = DEFAULT_UNAVAILABLE_CALLBACK,
) -> InlineKeyboardButton:
    """Create a button that opens the miniapp in text menu mode.

    When the simplified text menu mode is enabled we should avoid exposing
    deep bot flows and redirect the user to the configured miniapp instead.
    If the miniapp URL is missing we fall back to a safe callback that shows
    an alert about the unavailable profile rather than opening disabled
    sections of the bot.
    """

    if settings.is_text_main_menu_mode():
        miniapp_url = settings.get_main_menu_miniapp_url()
        if miniapp_url:
            return InlineKeyboardButton(
                text=text,
                web_app=types.WebAppInfo(url=miniapp_url),
            )
        safe_callback = unavailable_callback or DEFAULT_UNAVAILABLE_CALLBACK
        return InlineKeyboardButton(text=text, callback_data=safe_callback)

    return InlineKeyboardButton(text=text, callback_data=callback_data)
