"""Тесты rich-экрана «Отчёт по подписке» и его кнопок.

Без Telegram и БД: проверяем чистый сборщик rich-HTML, классический fallback
и callback_data клавиатуры в одиночном/мультитарифном режимах.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import settings
from app.handlers.subscription.report import (
    build_subscription_report_html,
    build_subscription_report_keyboard,
    build_subscription_report_text,
)
from app.keyboards.inline import get_subscription_keyboard


class FakeTexts:
    """t(key, default) без локалей: возвращает переданный русский fallback."""

    def t(self, key, default=None):
        return default


def _report_data(**overrides):
    data = {
        'status': 'active',
        'tariff_name': None,
        'end_date': '01.01.2027 12:00',
        'time_left': '30 дн. 5 ч.',
        'traffic_limit_gb': 100,
        'traffic_used_gb': 25.0,
        'devices_used': '2',
        'device_limit': 5,
        'autopay_enabled': False,
        'link': None,
        'hide_link': False,
        'revoke_enabled': False,
        'revoke_cooldown_seconds': None,
    }
    data.update(overrides)
    return data


def _callbacks(keyboard) -> list[str]:
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


@pytest.mark.parametrize(
    ('status', 'expected'),
    [
        ('active', '🟢 Активна'),
        ('trial', '🧪 Тестовая'),
        ('expired', '🔴 Истекла'),
        ('disabled', '⏸ Приостановлена'),
        ('limited', '🟠 Трафик исчерпан'),
    ],
)
def test_builder_renders_every_status(status: str, expected: str) -> None:
    html = build_subscription_report_html(_report_data(status=status), FakeTexts())

    assert expected in html
    assert '<table bordered striped>' in html
    assert '<footer>@x1lagent · ' in html


def test_builder_limited_traffic_shows_progress_bar() -> None:
    html = build_subscription_report_html(
        _report_data(traffic_limit_gb=100, traffic_used_gb=25.0),
        FakeTexts(),
    )

    assert '25.0 / 100 ГБ' in html
    assert '25%' in html
    assert '▓' in html


def test_builder_unlimited_traffic_has_no_progress_bar() -> None:
    html = build_subscription_report_html(
        _report_data(traffic_limit_gb=0, traffic_used_gb=12.5),
        FakeTexts(),
    )

    assert '∞ (безлимит)' in html
    assert '░' not in html
    assert '12.5' not in html


def test_builder_shows_connection_link_as_code_block() -> None:
    html = build_subscription_report_html(
        _report_data(link='https://sub.example/abc'),
        FakeTexts(),
    )

    assert '<details><summary>🔗 Подключение</summary>' in html
    assert '<code>https://sub.example/abc</code>' in html


@pytest.mark.parametrize(
    ('link', 'hide_link'),
    [
        (None, False),
        ('https://sub.example/abc', True),
    ],
)
def test_builder_hides_connection_when_hidden_or_missing(link, hide_link) -> None:
    html = build_subscription_report_html(
        _report_data(link=link, hide_link=hide_link),
        FakeTexts(),
    )

    assert '<code>' not in html
    assert '🔗 Подключение' not in html


def test_builder_shows_revoke_cooldown() -> None:
    html = build_subscription_report_html(
        _report_data(revoke_enabled=True, revoke_cooldown_seconds=310),
        FakeTexts(),
    )

    assert '⏱ Перевыпуск' in html
    assert '5 мин. 10 сек.' in html


def test_builder_marks_revoke_ready_without_cooldown() -> None:
    html = build_subscription_report_html(
        _report_data(revoke_enabled=True, revoke_cooldown_seconds=None),
        FakeTexts(),
    )

    assert '⏱ Перевыпуск' in html
    assert 'Перевыпуск доступен' in html


def test_builder_omits_revoke_when_disabled() -> None:
    html = build_subscription_report_html(
        _report_data(revoke_enabled=False),
        FakeTexts(),
    )

    assert '⏱ Перевыпуск' not in html


def test_classic_text_has_no_table_or_details() -> None:
    text = build_subscription_report_text(
        _report_data(link='https://sub.example/abc', revoke_enabled=True, revoke_cooldown_seconds=60),
        FakeTexts(),
    )

    assert '<table' not in text
    assert '<details' not in text
    assert '🟢 Активна' in text
    assert '25.0 / 100 ГБ' in text
    assert '<code>https://sub.example/abc</code>' in text
    assert '1 мин. 0 сек.' in text


def _enable_revoke(monkeypatch, enabled: bool = True) -> None:
    monkeypatch.setattr(settings, 'SUBSCRIPTION_REVOKE_ENABLED', enabled)


def _set_multi_tariff(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(settings, 'MULTI_TARIFF_ENABLED', enabled)
    monkeypatch.setattr(settings, 'SALES_MODE', 'tariffs')


def test_report_keyboard_single_uses_legacy_callbacks(monkeypatch) -> None:
    _enable_revoke(monkeypatch)
    _set_multi_tariff(monkeypatch, False)

    keyboard = build_subscription_report_keyboard('ru', SimpleNamespace(id=42))
    callbacks = _callbacks(keyboard)

    assert callbacks == ['subscription_revoke', 'menu_subscription']
    revoke_button = keyboard.inline_keyboard[0][0]
    assert revoke_button.style == 'danger'
    back_button = keyboard.inline_keyboard[1][0]
    assert back_button.style == 'primary'


def test_report_keyboard_multi_encodes_subscription_id(monkeypatch) -> None:
    _enable_revoke(monkeypatch)
    _set_multi_tariff(monkeypatch, True)

    keyboard = build_subscription_report_keyboard('ru', SimpleNamespace(id=77))

    assert _callbacks(keyboard) == ['sr:77', 'sm:77']


def test_report_keyboard_omits_revoke_on_cooldown(monkeypatch) -> None:
    _enable_revoke(monkeypatch)
    _set_multi_tariff(monkeypatch, False)

    keyboard = build_subscription_report_keyboard('ru', SimpleNamespace(id=42), cooldown_seconds=120)

    assert _callbacks(keyboard) == ['menu_subscription']


def test_report_keyboard_omits_revoke_when_disabled(monkeypatch) -> None:
    _enable_revoke(monkeypatch, enabled=False)
    _set_multi_tariff(monkeypatch, False)

    keyboard = build_subscription_report_keyboard('ru', SimpleNamespace(id=42))

    assert _callbacks(keyboard) == ['menu_subscription']


def _subscription_stub(sub_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=sub_id,
        subscription_url=None,
        tariff=None,
        is_trial=True,
        traffic_limit_gb=0,
        device_limit=3,
        status='active',
    )


def test_subscription_keyboard_has_report_button_single(monkeypatch) -> None:
    _set_multi_tariff(monkeypatch, False)

    keyboard = get_subscription_keyboard(
        'ru',
        has_subscription=True,
        is_trial=True,
        subscription=_subscription_stub(42),
    )

    assert 'subscription_report' in _callbacks(keyboard)


def test_subscription_keyboard_report_button_carries_sub_id_in_multi(monkeypatch) -> None:
    _set_multi_tariff(monkeypatch, True)

    keyboard = get_subscription_keyboard(
        'ru',
        has_subscription=True,
        is_trial=True,
        subscription=_subscription_stub(99),
    )

    assert 'subscription_report:99' in _callbacks(keyboard)
