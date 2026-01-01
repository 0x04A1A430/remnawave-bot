"""Telegram authentication validation for cabinet."""

import hashlib
import hmac
import json
from datetime import datetime
from typing import Dict, Any, Optional
from urllib.parse import parse_qsl, unquote

from app.config import settings


def validate_telegram_login_widget(data: Dict[str, Any], max_age_seconds: int = 86400) -> bool:
    """
    Validate Telegram Login Widget data.

    https://core.telegram.org/widgets/login#checking-authorization

    Args:
        data: Dictionary with Telegram login data (id, first_name, auth_date, hash, etc.)
        max_age_seconds: Maximum allowed age of auth_date (default 24 hours)

    Returns:
        True if data is valid, False otherwise
    """
    auth_data = data.copy()
    check_hash = auth_data.pop("hash", None)

    if not check_hash:
        return False

    # Check auth_date is not too old
    auth_date = auth_data.get("auth_date")
    if auth_date:
        try:
            # Use UTC timestamp to avoid timezone issues
            auth_time = datetime.utcfromtimestamp(int(auth_date))
            age = (datetime.utcnow() - auth_time).total_seconds()
            if age > max_age_seconds:
                return False
        except (ValueError, TypeError, OSError):
            return False

    # Build data-check-string (sorted key=value pairs, newline-separated)
    data_check_arr = [f"{k}={v}" for k, v in sorted(auth_data.items()) if v is not None]
    data_check_string = "\n".join(data_check_arr)

    # Create secret key from bot token using SHA256
    bot_token = settings.BOT_TOKEN
    secret_key = hashlib.sha256(bot_token.encode()).digest()

    # Calculate expected hash
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(calculated_hash, check_hash)


def validate_telegram_init_data(init_data: str, max_age_seconds: int = 86400) -> Optional[Dict[str, Any]]:
    """
    Validate Telegram WebApp initData.

    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Args:
        init_data: Raw initData string from Telegram WebApp
        max_age_seconds: Maximum allowed age of auth_date (default 24 hours)

    Returns:
        Parsed user data dict if valid, None otherwise
    """
    try:
        # Parse the init_data string
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        # Check auth_date is not too old
        auth_date = parsed.get("auth_date")
        if auth_date:
            try:
                # Use UTC timestamp to avoid timezone issues
                auth_time = datetime.utcfromtimestamp(int(auth_date))
                age = (datetime.utcnow() - auth_time).total_seconds()
                if age > max_age_seconds:
                    return None
            except (ValueError, TypeError, OSError):
                return None

        # Build data-check-string
        data_check_arr = [f"{k}={v}" for k, v in sorted(parsed.items())]
        data_check_string = "\n".join(data_check_arr)

        # Create secret key: HMAC_SHA256(bot_token, "WebAppData")
        bot_token = settings.BOT_TOKEN
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode(),
            hashlib.sha256
        ).digest()

        # Calculate expected hash
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        # Parse user data from the validated data
        user_data_str = parsed.get("user")
        if user_data_str:
            user_data = json.loads(unquote(user_data_str))
            return user_data

        return parsed

    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def extract_telegram_user_from_init_data(init_data: str) -> Optional[Dict[str, Any]]:
    """
    Extract and validate user info from Telegram WebApp initData.

    Args:
        init_data: Raw initData string from Telegram WebApp

    Returns:
        User data dict with id, first_name, last_name, username, etc. or None if invalid
    """
    return validate_telegram_init_data(init_data)
