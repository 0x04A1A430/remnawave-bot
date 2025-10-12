"""Тесты для функций безопасности из app.utils.security."""

import hashlib

import pytest

from app.utils.security import generate_api_token, hash_api_token


def test_hash_api_token_default_algorithm_matches_hashlib() -> None:
    """Проверяем, что алгоритм по умолчанию совпадает с hashlib.sha256."""
    sample = "secret-token"
    # Самостоятельно считаем эталонное значение.
    expected = hashlib.sha256(sample.encode("utf-8")).hexdigest()
    # Сравниваем с функцией проекта.
    assert hash_api_token(sample) == expected


@pytest.mark.parametrize(
    "algorithm,hash_factory",
    [
        ("sha256", hashlib.sha256),
        ("sha384", hashlib.sha384),
        ("sha512", hashlib.sha512),
    ],
)
def test_hash_api_token_accepts_supported_algorithms(algorithm, hash_factory) -> None:
    """Каждый поддерживаемый алгоритм должен выдавать корректный результат."""
    sample = "token-value"
    expected = hash_factory(sample.encode("utf-8")).hexdigest()
    assert hash_api_token(sample, algorithm=algorithm) == expected


def test_hash_api_token_rejects_unknown_algorithm() -> None:
    """Некорректное имя алгоритма должно приводить к ValueError."""
    with pytest.raises(ValueError):
        hash_api_token("value", algorithm="md5")  # type: ignore[arg-type]


@pytest.mark.parametrize("length", [8, 24, 48, 256])
def test_generate_api_token_respects_length_bounds(length: int) -> None:
    """Функция должна ограничивать длину токена безопасным диапазоном."""
    token = generate_api_token(length)
    clamped = max(24, min(length, 128))
    assert len(token) >= clamped
    # token_urlsafe расширяет строку, поэтому добавляем запас по длине.
    assert len(token) <= clamped * 2


def test_generate_api_token_produces_random_values() -> None:
    """Два последовательных вызова должны выдавать разные токены."""
    first = generate_api_token(48)
    second = generate_api_token(48)
    assert first != second
