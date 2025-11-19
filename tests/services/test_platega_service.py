import logging

import pytest

from app.services.platega_service import PlategaService


def test_sanitize_description_limits_utf8_bytes(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    original = "Интернет-сервис - Пополнение баланса на 50 ₽ и ещё чуть-чуть"

    trimmed = PlategaService._sanitize_description(original, 64)

    assert len(trimmed.encode("utf-8")) <= 64
    assert trimmed != original
    assert any("trimmed" in record.message for record in caplog.records)


def test_sanitize_description_returns_clean_value() -> None:
    original = "  Обычное описание  "

    trimmed = PlategaService._sanitize_description(original, 64)

    assert trimmed == "Обычное описание"
    assert len(trimmed.encode("utf-8")) <= 64
