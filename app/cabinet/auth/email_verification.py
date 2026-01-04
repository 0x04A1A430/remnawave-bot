"""Email verification token generation and validation."""

import secrets
from datetime import datetime, timedelta
from typing import Optional

from app.config import settings


def generate_verification_token() -> str:
    """
    Generate a secure random verification token.

    Returns:
        32-character hex token string
    """
    return secrets.token_hex(32)


def generate_password_reset_token() -> str:
    """
    Generate a secure random password reset token.

    Returns:
        32-character hex token string
    """
    return secrets.token_hex(32)


def get_verification_expires_at() -> datetime:
    """
    Get the expiration datetime for a verification token.

    Returns:
        Datetime when the verification token expires
    """
    hours = settings.get_cabinet_email_verification_expire_hours()
    return datetime.utcnow() + timedelta(hours=hours)


def get_password_reset_expires_at() -> datetime:
    """
    Get the expiration datetime for a password reset token.

    Returns:
        Datetime when the password reset token expires
    """
    hours = settings.get_cabinet_password_reset_expire_hours()
    return datetime.utcnow() + timedelta(hours=hours)


def is_token_expired(expires_at: Optional[datetime]) -> bool:
    """
    Check if a token has expired.

    Args:
        expires_at: Token expiration datetime

    Returns:
        True if expired or no expiration set, False otherwise
    """
    if expires_at is None:
        return True
    return datetime.utcnow() > expires_at
