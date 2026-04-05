"""Утилиты безопасности и генерации ключей."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Literal


HashAlgorithm = Literal['sha256', 'sha384', 'sha512']


def hash_api_token(
    token: str,
    algorithm: HashAlgorithm = 'sha256',
    *,
    hmac_secret: str | None = None,
) -> str:
    """Возвращает хеш токена в формате hex.

    If ``hmac_secret`` is provided, uses HMAC with the given secret key
    (recommended for production). Otherwise falls back to plain hash
    (backward-compatible).
    """
    normalized = (algorithm or 'sha256').lower()
    if normalized not in {'sha256', 'sha384', 'sha512'}:
        raise ValueError(f'Unsupported hash algorithm: {algorithm}')

    token_bytes = token.encode('utf-8')

    if hmac_secret:
        return hmac.new(hmac_secret.encode('utf-8'), token_bytes, normalized).hexdigest()

    digest = getattr(hashlib, normalized)
    return digest(token_bytes).hexdigest()


def generate_api_token(length: int = 48) -> str:
    """Генерирует криптографически стойкий токен."""
    length = max(24, min(length, 128))
    return secrets.token_urlsafe(length)


__all__ = ['HashAlgorithm', 'generate_api_token', 'hash_api_token']
