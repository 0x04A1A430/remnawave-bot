"""Canonical utilities for building Telegram and Cabinet gift claim and share links.

Security constraints:
- Telegram start parameters are limited to 64 characters (alphanumeric, underscore, hyphen).
- A gift start parameter must use the 'GIFT_' prefix (5 chars) and retain at least
  GIFT_TOKEN_MIN_PREFIX_LENGTH (48 chars) of entropy from the 64-char purchase token.
- Standalone gift tokens must never be placed directly in share text, logs, or user copy.
"""

from __future__ import annotations

import re
import urllib.parse


TELEGRAM_START_PARAM_MAX_LENGTH: int = 64
TELEGRAM_GIFT_START_PREFIX: str = 'GIFT_'
GIFT_TOKEN_MIN_PREFIX_LENGTH: int = 48
GIFT_TOKEN_BOT_PREFIX_LENGTH: int = TELEGRAM_START_PARAM_MAX_LENGTH - len(TELEGRAM_GIFT_START_PREFIX)  # 59

_TOKEN_RE = re.compile(r'^[a-zA-Z0-9_-]+$')
_USERNAME_RE = re.compile(r'^[a-zA-Z0-9_]+$')


class GiftLinkError(Exception):
    """Base exception for gift link formatting and validation errors."""


class InvalidGiftTokenError(GiftLinkError, ValueError):
    """Raised when a gift token is malformed, too short, or contains invalid characters."""


class InvalidBotUsernameError(GiftLinkError, ValueError):
    """Raised when a bot username is invalid or malformed."""


class MissingBotUsernameError(InvalidBotUsernameError):
    """Raised when a bot username is empty or missing."""


class InvalidCabinetUrlError(GiftLinkError, ValueError):
    """Raised when a cabinet base URL is invalid or malformed."""


class MissingCabinetUrlError(InvalidCabinetUrlError):
    """Raised when a cabinet base URL is empty or missing."""


class InvalidClaimLinkError(GiftLinkError, ValueError):
    """Raised when a claim link for sharing is invalid, empty, or malformed."""


class InvalidShareTextError(GiftLinkError, ValueError):
    """Raised when share text is invalid, empty, or malformed."""


def _validate_gift_token(token: str) -> str:
    """Validate that the token is a valid, secure URL-safe token."""
    if not isinstance(token, str) or not token:
        raise InvalidGiftTokenError('Gift token must be a non-empty string')

    if not _TOKEN_RE.match(token):
        raise InvalidGiftTokenError(f'Gift token contains invalid characters: {token!r}')

    if len(token) < GIFT_TOKEN_MIN_PREFIX_LENGTH:
        raise InvalidGiftTokenError(
            f'Gift token length ({len(token)}) is below security threshold of {GIFT_TOKEN_MIN_PREFIX_LENGTH}'
        )

    return token


def _normalize_bot_username(bot_username: str) -> str:
    """Normalize and validate a Telegram bot username."""
    if not isinstance(bot_username, str):
        raise MissingBotUsernameError('Bot username must be a string')

    cleaned = bot_username.strip()
    if not cleaned or cleaned == '@':
        raise MissingBotUsernameError('Bot username cannot be empty')

    cleaned = cleaned.lstrip('@')
    if not cleaned:
        raise MissingBotUsernameError('Bot username cannot be empty')

    if not _USERNAME_RE.match(cleaned):
        raise InvalidBotUsernameError(f'Invalid bot username: {bot_username!r}')

    return cleaned


def _normalize_cabinet_url(cabinet_url: str) -> str:
    """Normalize and validate a Cabinet base URL."""
    if not isinstance(cabinet_url, str):
        raise MissingCabinetUrlError('Cabinet URL must be a string')

    cleaned = cabinet_url.strip()
    if not cleaned:
        raise MissingCabinetUrlError('Cabinet URL cannot be empty')

    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise InvalidCabinetUrlError(f'Invalid cabinet URL: {cabinet_url!r}')

    return cleaned.rstrip('/')


def build_bot_gift_claim_link(token: str, bot_username: str) -> str:
    """Build a canonical Telegram deep-link for claiming a gift subscription.

    The start parameter is formatted as ``GIFT_<token_prefix>``, truncated to
    fit exactly within Telegram's 64-character start_param limit while retaining
    59 characters of entropy (exceeding the 48-character security threshold).

    Args:
        token: Full 64-character (or minimum 48-character) URL-safe purchase token.
        bot_username: Telegram bot username (with or without leading '@').

    Returns:
        Canonical claim link, e.g. ``https://t.me/my_bot?start=GIFT_<59_chars>``.

    Raises:
        InvalidGiftTokenError: If token is malformed, too short, or non-URL-safe.
        MissingBotUsernameError: If bot username is missing or empty.
        InvalidBotUsernameError: If bot username contains invalid characters.
    """
    valid_token = _validate_gift_token(token)
    clean_username = _normalize_bot_username(bot_username)

    token_fragment = valid_token[:GIFT_TOKEN_BOT_PREFIX_LENGTH]
    start_param = f'{TELEGRAM_GIFT_START_PREFIX}{token_fragment}'

    return f'https://t.me/{clean_username}?start={start_param}'


def build_cabinet_gift_claim_link(token: str, cabinet_url: str) -> str:
    """Build a canonical web cabinet claim link containing the full bearer token.

    Args:
        token: Full 64-character purchase token.
        cabinet_url: Cabinet base URL (e.g. ``https://cabinet.example.com``).

    Returns:
        Canonical web claim URL, e.g. ``https://cabinet.example.com/buy/gift/<token>``.

    Raises:
        InvalidGiftTokenError: If token is malformed, too short, or non-URL-safe.
        MissingCabinetUrlError: If cabinet URL is missing or empty.
        InvalidCabinetUrlError: If cabinet URL has an invalid scheme or format.
    """
    valid_token = _validate_gift_token(token)
    clean_cabinet_base = _normalize_cabinet_url(cabinet_url)

    return f'{clean_cabinet_base}/buy/gift/{valid_token}'


def build_telegram_gift_share_url(claim_link: str, localized_share_text: str) -> str:
    """Build a native Telegram share URL (``https://t.me/share/url``) with prefilled text.

    Args:
        claim_link: The canonical bot or cabinet gift claim link.
        localized_share_text: Localized greeting and instructions to prefill in the chat picker.

    Returns:
        Canonical share URL, e.g. ``https://t.me/share/url?url=...&text=...``.

    Raises:
        InvalidClaimLinkError: If claim_link is empty or not a valid URL.
        InvalidShareTextError: If localized_share_text is not a string or is empty.
    """
    if not isinstance(claim_link, str):
        raise InvalidClaimLinkError('Claim link must be a string')

    cleaned_link = claim_link.strip()
    if not cleaned_link:
        raise InvalidClaimLinkError('Claim link cannot be empty')

    parsed = urllib.parse.urlparse(cleaned_link)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise InvalidClaimLinkError(f'Invalid claim link: {claim_link!r}')

    if not isinstance(localized_share_text, str):
        raise InvalidShareTextError('Share text must be a string')

    cleaned_text = localized_share_text.strip()
    if not cleaned_text:
        raise InvalidShareTextError('Share text cannot be empty')

    query = urllib.parse.urlencode(
        {
            'url': cleaned_link,
            'text': localized_share_text,
        }
    )
    return f'https://t.me/share/url?{query}'
