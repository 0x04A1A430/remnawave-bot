"""JWT token handling for cabinet authentication."""

import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from app.config import settings

JWT_ALGORITHM = "HS256"


def create_access_token(user_id: int, telegram_id: int) -> str:
    """
    Create a short-lived access token.

    Args:
        user_id: Database user ID
        telegram_id: Telegram user ID

    Returns:
        Encoded JWT access token
    """
    expire_minutes = settings.get_cabinet_access_token_expire_minutes()
    expires = datetime.utcnow() + timedelta(minutes=expire_minutes)

    payload = {
        "sub": str(user_id),
        "telegram_id": telegram_id,
        "type": "access",
        "exp": expires,
        "iat": datetime.utcnow(),
    }

    secret = settings.get_cabinet_jwt_secret()
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    """
    Create a long-lived refresh token.

    Args:
        user_id: Database user ID

    Returns:
        Encoded JWT refresh token
    """
    expire_days = settings.get_cabinet_refresh_token_expire_days()
    expires = datetime.utcnow() + timedelta(days=expire_days)

    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": expires,
        "iat": datetime.utcnow(),
    }

    secret = settings.get_cabinet_jwt_secret()
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Decode and validate a JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded payload dict or None if invalid/expired
    """
    try:
        secret = settings.get_cabinet_jwt_secret()
        return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_token_payload(token: str, expected_type: str = "access") -> Optional[Dict[str, Any]]:
    """
    Decode token and verify its type.

    Args:
        token: JWT token string
        expected_type: Expected token type ("access" or "refresh")

    Returns:
        Decoded payload dict or None if invalid/expired/wrong type
    """
    payload = decode_token(token)

    if not payload:
        return None

    if payload.get("type") != expected_type:
        return None

    return payload


def get_refresh_token_expires_at() -> datetime:
    """Get the expiration datetime for a new refresh token."""
    expire_days = settings.get_cabinet_refresh_token_expire_days()
    return datetime.utcnow() + timedelta(days=expire_days)
