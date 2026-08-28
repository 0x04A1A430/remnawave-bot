"""Очередь повторной отправки писем: срок годности содержимого и гигиена тела.

Очередь заводилась ради писем, которые нельзя терять, — а это ровно те письма,
внутри которых живёт секрет с коротким сроком: код смены email живёт 15 минут,
ссылка сброса пароля — час. Бэкофф же растянут почти на сутки
(1м, 6м, 21м, 51м, 1ч51м, ..., 19ч51м от постановки), поэтому без ограничения
человек получает настоящее с виду письмо с уже мёртвым кодом, а тело письма с
этим кодом лежит в таблице неограниченно долго.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cabinet.services.email_service import EmailService
from app.services.email_retry_service import (
    BACKOFF_MINUTES,
    MAX_ATTEMPTS,
    STATUS_DEAD,
    STATUS_SENT,
    EmailRetryService,
)


def _session_mock() -> AsyncMock:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    return session


def _row(**kw) -> MagicMock:
    row = MagicMock()
    row.id = kw.get('id', 1)
    row.to_email = 'user@test.dev'
    row.subject = 'Код подтверждения'
    row.body_html = '<p>Ваш код: 123456</p>'
    row.body_text = None
    row.unsubscribe_url = None
    row.attachments_json = None
    row.attempts = kw.get('attempts', 0)
    row.expires_at = kw.get('expires_at')
    return row


# ============ срок годности содержимого ============


def test_backoff_outlives_short_lived_codes():
    """Фиксируем сам факт расхождения: без срока бэкофф шлёт письма после смерти кода."""
    cumulative, total = [], 0
    for minutes in BACKOFF_MINUTES[:-1]:
        total += minutes
        cumulative.append(total)

    # Код смены email живёт 15 минут по умолчанию, ссылка сброса пароля — 60.
    assert [c for c in cumulative if c > 15], 'бэкофф выходит за срок жизни кода смены email'
    assert [c for c in cumulative if c > 60], 'бэкофф выходит за срок жизни ссылки сброса пароля'


async def test_expired_item_is_killed_without_sending():
    service = EmailRetryService()
    row = _row(expires_at=datetime.now(tz=UTC) - timedelta(minutes=1))

    session = _session_mock()
    session.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [row])))
    attempted: list = []

    with patch('app.services.email_retry_service.AsyncSessionLocal', return_value=session):
        with patch.object(service, '_attempt', AsyncMock(side_effect=lambda item: attempted.append(item))):
            await service._process_due()

    assert attempted == [], 'просроченное письмо отправлять нельзя'
    killed = session.execute.await_args_list[-1].args[0]
    values = killed.compile().params
    assert values['status'] == STATUS_DEAD
    assert values['body_html'] == '', 'тело просроченного письма должно быть стёрто'


async def test_live_item_is_still_sent():
    service = EmailRetryService()
    row = _row(expires_at=datetime.now(tz=UTC) + timedelta(hours=1))

    session = _session_mock()
    session.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [row])))
    attempted: list = []

    with patch('app.services.email_retry_service.AsyncSessionLocal', return_value=session):
        with patch.object(service, '_attempt', AsyncMock(side_effect=lambda item: attempted.append(item))):
            await service._process_due()

    assert len(attempted) == 1


async def test_item_without_expiry_is_unrestricted():
    service = EmailRetryService()
    session = _session_mock()
    session.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [_row()])))
    attempted: list = []

    with patch('app.services.email_retry_service.AsyncSessionLocal', return_value=session):
        with patch.object(service, '_attempt', AsyncMock(side_effect=lambda item: attempted.append(item))):
            await service._process_due()

    assert len(attempted) == 1


@pytest.mark.parametrize(
    ('method', 'kwargs'),
    [
        ('send_verification_email', {'verification_token': 't', 'verification_url': 'https://x/verify'}),
        ('send_password_reset_email', {'reset_token': 't', 'reset_url': 'https://x/reset'}),
        ('send_email_change_code', {'code': '123456'}),
    ],
)
def test_auth_emails_declare_a_deadline(method, kwargs):
    """Три письма с секретом внутри обязаны передавать срок годности в очередь."""
    service = EmailService()
    captured: dict = {}

    def fake_send_email(*args, **kw):
        captured.update(kw)
        return True

    service.send_email = fake_send_email
    getattr(service, method)(to_email='user@test.dev', **kwargs)

    assert captured.get('retry_until') is not None, f'{method} не ограничивает срок повторов'
    assert captured['retry_until'] > datetime.now(tz=UTC)


# ============ тело письма не живёт дольше нужного ============


async def test_body_is_purged_after_successful_delivery():
    service = EmailRetryService()
    session = _session_mock()
    session.execute = AsyncMock()

    with patch('app.services.email_retry_service.AsyncSessionLocal', return_value=session):
        with patch('app.cabinet.services.email_service.email_service') as mail:
            mail.send_email = MagicMock(return_value=True)
            await service._attempt(
                {
                    'id': 1,
                    'to_email': 'u@t.dev',
                    'subject': 's',
                    'body_html': '<p>Ваш код: 123456</p>',
                    'body_text': None,
                    'unsubscribe_url': None,
                    'attachments': None,
                    'attempts': 0,
                }
            )

    values = session.execute.await_args.args[0].compile().params
    assert values['status'] == STATUS_SENT
    assert values['body_html'] == '', 'после доставки тело с секретом хранить незачем'


async def test_body_is_purged_when_attempts_run_out():
    service = EmailRetryService()
    session = _session_mock()
    session.execute = AsyncMock()

    with patch('app.services.email_retry_service.AsyncSessionLocal', return_value=session):
        with patch('app.cabinet.services.email_service.email_service') as mail:
            mail.send_email = MagicMock(return_value=False)
            await service._attempt(
                {
                    'id': 1,
                    'to_email': 'u@t.dev',
                    'subject': 's',
                    'body_html': '<p>Ваш код: 123456</p>',
                    'body_text': None,
                    'unsubscribe_url': None,
                    'attachments': None,
                    'attempts': MAX_ATTEMPTS - 1,
                }
            )

    values = session.execute.await_args.args[0].compile().params
    assert values['status'] == STATUS_DEAD
    assert values['body_html'] == ''


async def test_body_survives_between_attempts():
    """Пока попытки не исчерпаны, тело нужно — иначе повторять будет нечего."""
    service = EmailRetryService()
    session = _session_mock()
    session.execute = AsyncMock()

    with patch('app.services.email_retry_service.AsyncSessionLocal', return_value=session):
        with patch('app.cabinet.services.email_service.email_service') as mail:
            mail.send_email = MagicMock(return_value=False)
            await service._attempt(
                {
                    'id': 1,
                    'to_email': 'u@t.dev',
                    'subject': 's',
                    'body_html': '<p>Ваш код: 123456</p>',
                    'body_text': None,
                    'unsubscribe_url': None,
                    'attachments': None,
                    'attempts': 0,
                }
            )

    values = session.execute.await_args.args[0].compile().params
    assert 'body_html' not in values


async def test_stop_is_safe_without_start():
    await EmailRetryService().stop()
