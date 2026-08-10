"""Отписка от маркетинговых писем: подпись токена, заголовки RFC 8058,
ссылка в футере и фильтрация получателей email-рассылки по настройкам.

Без отписки Gmail/Yahoo штрафуют отправителя за жалобы «Спам» вместо клика по
ссылке, а bulk-политики этих провайдеров прямо требуют one-click unsubscribe.
До этих тестов email-путь рассылки ещё и игнорировал тумблеры
``news_enabled`` / ``promo_offers_enabled``, которые Telegram-путь уважает:
пользователь выключал новости в кабинете и всё равно получал их на почту.
"""

from __future__ import annotations

from typing import Self

import pytest

from app.cabinet.services import email_unsubscribe as unsub
from app.cabinet.services.email_service import email_service
from app.cabinet.services.email_templates import EmailNotificationTemplates


class _FakeSMTP:
    """Ловит сырое письмо, отданное в ``sendmail``, и работает как CM."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def sendmail(self, _from_addr: str, _to_addr: str, msg: str) -> None:
        self.messages.append(msg)


@pytest.fixture
def smtp_ready(monkeypatch):
    """Делает email_service «настроенным» и подменяет SMTP-соединение."""
    from app.config import settings

    fake = _FakeSMTP()
    monkeypatch.setattr(type(settings), 'is_smtp_configured', lambda self: True)
    monkeypatch.setattr(settings, 'SMTP_FROM_EMAIL', 'noreply@example.com')
    monkeypatch.setattr(settings, 'SMTP_FROM_NAME', 'Example VPN')
    monkeypatch.setattr(email_service, '_get_smtp_connection', lambda: fake)
    return fake


# --------------------------------------------------------------------------
# Токен
# --------------------------------------------------------------------------


def test_token_roundtrip(monkeypatch):
    """Свежий токен опознаётся: отдаёт user_id и категорию."""
    monkeypatch.setattr(unsub, '_secret', lambda: 'unit-test-secret')

    token = unsub.build_token(42, 'User@Example.com', 'all')
    assert unsub.parse_token(token) == (42, 'all')
    assert unsub.verify_token(token, 'user@example.com') is True


def test_token_email_is_case_insensitive(monkeypatch):
    """Регистр адреса не должен ломать ссылку из письма."""
    monkeypatch.setattr(unsub, '_secret', lambda: 'unit-test-secret')

    token = unsub.build_token(42, 'user@example.com', 'all')
    assert unsub.verify_token(token, 'USER@EXAMPLE.COM') is True


def test_token_rejects_tampering(monkeypatch):
    """Подменённый user_id не проходит проверку подписи."""
    monkeypatch.setattr(unsub, '_secret', lambda: 'unit-test-secret')

    token = unsub.build_token(42, 'user@example.com', 'all')
    forged = token.replace('42.', '43.', 1)

    assert unsub.parse_token(forged) == (43, 'all')
    assert unsub.verify_token(forged, 'user@example.com') is False


def test_token_dies_with_old_email(monkeypatch):
    """Смена адреса обесценивает старые ссылки — токен привязан к email."""
    monkeypatch.setattr(unsub, '_secret', lambda: 'unit-test-secret')

    token = unsub.build_token(42, 'old@example.com', 'all')
    assert unsub.verify_token(token, 'new@example.com') is False


@pytest.mark.parametrize('garbage', ['', 'nonsense', 'a.b.c', '42.all', '42.all.', 'x.all.sig'])
def test_parse_token_survives_garbage(garbage):
    """Мусор в query-параметре не должен ронять публичный эндпоинт."""
    assert unsub.parse_token(garbage) is None


def test_build_url_empty_when_disabled(monkeypatch):
    """Выключенная отписка не должна протаскивать битую ссылку в письмо."""
    from app.config import settings

    monkeypatch.setattr(settings, 'EMAIL_UNSUBSCRIBE_ENABLED', False)
    assert unsub.build_unsubscribe_url(1, 'user@example.com') == ''


def test_build_url_uses_cabinet_url_by_default(monkeypatch):
    """Без явного EMAIL_UNSUBSCRIBE_BASE_URL берём публичный путь кабинета."""
    from app.config import settings

    monkeypatch.setattr(settings, 'EMAIL_UNSUBSCRIBE_ENABLED', True)
    monkeypatch.setattr(settings, 'EMAIL_UNSUBSCRIBE_BASE_URL', '')
    monkeypatch.setattr(settings, 'CABINET_URL', 'https://cab.example.com/')
    monkeypatch.setattr(unsub, '_secret', lambda: 'unit-test-secret')

    url = unsub.build_unsubscribe_url(7, 'user@example.com')
    assert url.startswith('https://cab.example.com/api/cabinet/public/unsubscribe?token=7.all.')


# --------------------------------------------------------------------------
# Заголовки письма (RFC 8058)
# --------------------------------------------------------------------------


def test_send_email_adds_one_click_headers(smtp_ready):
    """List-Unsubscribe + One-Click — то, из чего Gmail рисует свою кнопку."""
    ok = email_service.send_email(
        to_email='user@example.com',
        subject='Скидка',
        body_html='<p>hi</p>',
        unsubscribe_url='https://cab.example.com/api/cabinet/public/unsubscribe?token=7.all.sig',
    )

    assert ok is True
    raw = smtp_ready.messages[0]
    assert 'List-Unsubscribe: <https://cab.example.com/api/cabinet/public/unsubscribe?token=7.all.sig>' in raw
    assert 'List-Unsubscribe-Post: List-Unsubscribe=One-Click' in raw


def test_transactional_email_has_no_unsubscribe_headers(smtp_ready):
    """Письмо со сбросом пароля не должно предлагать отписку."""
    email_service.send_email(to_email='user@example.com', subject='Код', body_html='<p>1234</p>')

    raw = smtp_ready.messages[0]
    assert 'List-Unsubscribe' not in raw


def test_unsubscribe_url_is_header_injection_safe(smtp_ready):
    """Перенос строки в URL не должен дописать чужой заголовок."""
    email_service.send_email(
        to_email='user@example.com',
        subject='Скидка',
        body_html='<p>hi</p>',
        unsubscribe_url='https://x.example.com/u\r\nBcc: victim@example.com',
    )

    raw = smtp_ready.messages[0]
    assert 'Bcc: victim@example.com' not in raw


# --------------------------------------------------------------------------
# Шаблоны
# --------------------------------------------------------------------------


def test_base_template_renders_footer_link():
    """Ссылка в футере — для клиентов, которые не рисуют кнопку из заголовка."""
    html = EmailNotificationTemplates()._get_base_template(
        '<p>text</p>', 'ru', unsubscribe_url='https://cab.example.com/u?token=t'
    )
    assert 'https://cab.example.com/u?token=t' in html
    assert 'Отписаться' in html


def test_base_template_without_url_has_no_dangling_footer():
    """У транзакционных писем футер остаётся прежним."""
    html = EmailNotificationTemplates()._get_base_template('<p>text</p>', 'ru')
    assert 'Отписаться' not in html


def test_common_context_exposes_unsubscribe_placeholder():
    """{unsubscribe_url} обязан резолвиться, иначе он утечёт в письмо литералом."""
    from app.cabinet.services.email_template_overrides import COMMON_CONTEXT_VARS, build_common_context

    assert 'unsubscribe_url' in COMMON_CONTEXT_VARS
    assert 'unsubscribe_url' in build_common_context()


# --------------------------------------------------------------------------
# Фильтрация получателей рассылки
# --------------------------------------------------------------------------


class _StubUser:
    def __init__(self, notification_settings: dict | None) -> None:
        self.notification_settings = notification_settings


def test_email_broadcast_filters_by_category():
    """Тумблеры кабинета обязаны резать email-рассылку так же, как Telegram."""
    from app.utils.notification_prefs import filter_users_by_broadcast_category

    opted_out_news = _StubUser({'news_enabled': False})
    opted_out_promo = _StubUser({'promo_offers_enabled': False})
    default_user = _StubUser(None)

    users = [opted_out_news, opted_out_promo, default_user]

    assert filter_users_by_broadcast_category(users, 'news') == [opted_out_promo, default_user]
    assert filter_users_by_broadcast_category(users, 'promo') == [opted_out_news, default_user]
    # Системные письма (истечение подписки, оплата) отписке не подлежат.
    assert filter_users_by_broadcast_category(users, 'system') == users
