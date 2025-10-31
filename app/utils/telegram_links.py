"""Utilities for constructing Telegram deep links that work with usernames or IDs."""

from __future__ import annotations

from typing import Optional


def _clean_username(username: str | None) -> str | None:
    if not username:
        return None
    username_clean = username.strip().lstrip("@")
    return username_clean or None


def build_user_dm_url(username: str | None, telegram_id: int | str | None) -> Optional[str]:
    """Return a deep link for opening a DM with a Telegram user.

    Preference is given to usernames because the ``https://t.me/<username>`` scheme
    works reliably across platforms. When a username is not available, we fall back
    to the ``tg://openmessage`` scheme with the numeric Telegram ID so that support
    agents can still contact the user.
    """

    username_clean = _clean_username(username)
    if username_clean:
        return f"https://t.me/{username_clean}"

    if telegram_id is None:
        return None

    try:
        telegram_id_int = int(telegram_id)
    except (TypeError, ValueError):
        return None

    return f"tg://openmessage?user_id={telegram_id_int}"


def build_user_profile_url(username: str | None, telegram_id: int | str | None) -> Optional[str]:
    """Return a link that opens a Telegram profile.

    When an ID is available we use the ``tg://user`` scheme to jump directly to the
    user profile. If the ID cannot be parsed we reuse :func:`build_user_dm_url` as a
    graceful fallback so that the caller always gets a usable link.
    """

    if telegram_id is not None:
        try:
            telegram_id_int = int(telegram_id)
        except (TypeError, ValueError):
            pass
        else:
            return f"tg://user?id={telegram_id_int}"

    return build_user_dm_url(username, telegram_id)

