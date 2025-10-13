"""Базовые тесты для валидаторов из app.utils.validators."""

import pytest

from app.utils import validators


@pytest.mark.parametrize(
    "email,is_valid",
    [
        ("user@example.com", True),
        ("user.name+tag@sub.domain.ru", True),
        ("plain-address", False),
        ("missing-at.example.com", False),
        ("user@invalid", False),
    ],
)
def test_validate_email_handles_expected_patterns(email: str, is_valid: bool) -> None:
    """Проверяем типичные корректные и некорректные адреса."""
    assert validators.validate_email(email) is is_valid


@pytest.mark.parametrize(
    "phone,is_valid",
    [
        ("+71234567890", True),
        ("+1 (202) 555-0101", True),
        ("12345", True),
        ("+0 123456789", False),
        ("abc", False),
    ],
)
def test_validate_phone_strips_formatting_and_checks_pattern(phone: str, is_valid: bool) -> None:
    """Телефон должен соответствовать стандарту E.164 после очистки."""
    assert validators.validate_phone(phone) is is_valid


@pytest.mark.parametrize(
    "username,is_valid",
    [
        ("@valid_name", True),
        ("simpleUser", True),
        ("bad", False),
        ("toolongusername_more_than32_chars", False),
        ("", False),
    ],
)
def test_validate_telegram_username_enforces_length(username: str, is_valid: bool) -> None:
    """Telegram-логин должен быть 5-32 символов и содержать допустимые символы."""
    assert validators.validate_telegram_username(username) is is_valid


def test_validate_amount_returns_float_within_bounds() -> None:
    """Числа должны конвертироваться с уважением к диапазону."""
    assert validators.validate_amount("10.5", min_amount=5, max_amount=20) == pytest.approx(10.5)
    assert validators.validate_amount("2", min_amount=5, max_amount=20) is None
    assert validators.validate_amount("abc", min_amount=0, max_amount=10) is None


def test_validate_positive_integer_enforces_upper_bound() -> None:
    """Положительное целое число выходит за пределы — возвращаем None."""
    assert validators.validate_positive_integer("12", max_value=20) == 12
    assert validators.validate_positive_integer("0", max_value=20) is None
    assert validators.validate_positive_integer("50", max_value=20) is None
    assert validators.validate_positive_integer("NaN") is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("500", 500),
        ("10gb", 10240),
        ("2 TB", 2097152),
        ("безлимит", 0),
        ("invalid", None),
    ],
)
def test_validate_traffic_amount_supports_units(value: str, expected: int | None) -> None:
    """Валидатор трафика распознаёт разные единицы измерения и особые значения."""
    assert validators.validate_traffic_amount(value) == expected


def test_validate_subscription_period_accepts_reasonable_range() -> None:
    """Диапазон допустимой длительности от 1 до 3650 дней."""
    assert validators.validate_subscription_period("30") == 30
    assert validators.validate_subscription_period(0) is None
    assert validators.validate_subscription_period(4000) is None


def test_validate_uuid_detects_standard_format() -> None:
    """UUID должен соответствовать HEX шаблону версии 4/5."""
    sample = "123e4567-e89b-12d3-a456-426614174000"
    assert validators.validate_uuid(sample) is True
    assert validators.validate_uuid("not-a-uuid") is False


def test_validate_url_recognises_https_links() -> None:
    """Валидатор URL допускает http/https ссылки и отклоняет произвольные строки."""
    assert validators.validate_url("https://example.com/path?query=1")
    assert not validators.validate_url("ftp://example.com")


def test_validate_html_tags_rejects_unknown_tags() -> None:
    """Неизвестные HTML теги должны приводить к отказу."""
    ok, message = validators.validate_html_tags("<b>bold</b>")
    assert ok is True
    bad, error = validators.validate_html_tags("<marquee>run</marquee>")
    assert bad is False
    assert "Неподдерживаемый тег" in error


def test_validate_html_structure_detects_wrong_nesting() -> None:
    """Неправильная вложенность тегов должна сообщаться пользователю."""
    ok, message = validators.validate_html_structure("<b><i>text</i></b>")
    assert ok is True
    bad, error = validators.validate_html_structure("<b><i>text</b></i>")
    assert bad is False
    assert "Неправильная вложенность" in error


def test_fix_html_tags_repairs_missing_quotes() -> None:
    """Автоисправление должно добавлять кавычки у ссылок."""
    broken = '<a href=https://example.com>link</a>'
    fixed = validators.fix_html_tags(broken)
    assert 'href="https://example.com"' in fixed


def test_validate_rules_content_detects_structure_error() -> None:
    """При нарушении структуры должны вернуться сообщение и отсутствие подсказки."""
    is_valid, message, suggestion = validators.validate_rules_content("<b>text</i>")
    assert is_valid is False
    assert "Неправильная вложенность" in message
    assert suggestion is None


def test_validate_rules_content_accepts_supported_markup() -> None:
    """Корректный HTML должен проходить проверку без сообщений."""
    is_valid, message, suggestion = validators.validate_rules_content("<b>Добро пожаловать!</b>")
    assert is_valid is True
    assert message == ""
    assert suggestion is None
