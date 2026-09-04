"""Tests for notify_gift_claim_available — the gift claim-link delivery.

Guarantees pinned here (unified claimable-gift model):
  - The claim link is ALWAYS sent to the buyer (durable backstop) when the buyer
    used email, so the link is never lost if they close the success page.
  - An EMAIL recipient gets the claim link.
  - A TELEGRAM recipient is NOT auto-DMed (a spoofed @username would otherwise
    receive the gift) — the buyer forwards the link manually instead.
  - A send failure never raises (must never break the payment flow).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.guest_purchase_service import notify_gift_claim_available


def _gift(**overrides) -> SimpleNamespace:
    base = dict(
        id=1,
        is_gift=True,
        token='T' * 64,
        period_days=30,
        gift_message=None,
        gift_recipient_type=None,
        gift_recipient_value=None,
        contact_type=None,
        contact_value=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _patches(send_mock: MagicMock, override: tuple[str, str] | None = None):
    """Patch the lazily-imported email machinery + cabinet URL.

    ``override`` — что вернёт сервис сохранённых в редакторе шаблонов
    (None = override не задан, письмо строится по дефолтному шаблону).
    """
    email_module = SimpleNamespace(email_service=SimpleNamespace(send_email=send_mock))
    templates_module = SimpleNamespace(
        EmailNotificationTemplates=lambda: SimpleNamespace(
            get_template=lambda *a, **k: {'subject': 's', 'body_html': 'b'}
        )
    )
    override_calls: list[tuple] = []

    async def get_rendered_override(notification_type, language, context, *a, **k):
        override_calls.append((notification_type, language, context))
        return override

    overrides_module = SimpleNamespace(get_rendered_override=get_rendered_override, calls=override_calls)
    return (
        patch.dict(
            'sys.modules',
            {
                'app.cabinet.services.email_service': email_module,
                'app.cabinet.services.email_templates': templates_module,
                'app.cabinet.services.email_template_overrides': overrides_module,
            },
        ),
        patch('app.services.guest_purchase_service.settings.CABINET_URL', 'https://cab.example'),
    )


@pytest.mark.asyncio
async def test_email_recipient_and_buyer_both_get_claim_link() -> None:
    send = MagicMock(return_value=True)
    purchase = _gift(
        gift_recipient_type='email',
        gift_recipient_value='friend@example.com',
        contact_type='email',
        contact_value='buyer@example.com',
    )
    mods, cab = _patches(send)
    with mods, cab:
        await notify_gift_claim_available(purchase, tariff_name='Lite', period_days=30)

    recipients = {c.kwargs['to_email'] for c in send.call_args_list}
    assert recipients == {'friend@example.com', 'buyer@example.com'}, (
        'both the email recipient and the buyer must receive the claim link'
    )
    # The buyer's backstop email must carry the actual claim URL.
    buyer_call = next(c for c in send.call_args_list if c.kwargs['to_email'] == 'buyer@example.com')
    assert f'/buy/gift/{purchase.token}' in buyer_call.kwargs['body_html']


@pytest.mark.asyncio
async def test_telegram_recipient_is_not_auto_dmed_but_buyer_still_gets_link() -> None:
    send = MagicMock(return_value=True)
    purchase = _gift(
        gift_recipient_type='telegram',
        gift_recipient_value='@maybe_spoofed',
        contact_type='email',
        contact_value='buyer@example.com',
    )
    mods, cab = _patches(send)
    with mods, cab:
        await notify_gift_claim_available(purchase, tariff_name='Lite', period_days=30)

    recipients = [c.kwargs['to_email'] for c in send.call_args_list]
    # No email to a telegram handle; only the buyer backstop email is sent.
    assert recipients == ['buyer@example.com']


@pytest.mark.asyncio
async def test_send_failure_never_raises() -> None:
    send = MagicMock(side_effect=RuntimeError('smtp down'))
    purchase = _gift(
        gift_recipient_type='email',
        gift_recipient_value='friend@example.com',
        contact_type='email',
        contact_value='buyer@example.com',
    )
    mods, cab = _patches(send)
    with mods, cab:
        # Must swallow the error — a notification failure cannot break payment.
        await notify_gift_claim_available(purchase, tariff_name='Lite', period_days=30)


@pytest.mark.asyncio
async def test_non_gift_purchase_is_a_noop() -> None:
    send = MagicMock(return_value=True)
    purchase = _gift(is_gift=False, contact_type='email', contact_value='buyer@example.com')
    mods, cab = _patches(send)
    with mods, cab:
        await notify_gift_claim_available(purchase)
    send.assert_not_called()


@pytest.mark.asyncio
async def test_email_recipient_gets_admin_override_when_saved() -> None:
    """Жалоба из «Багов»: шаблон письма нельзя было поменять со стандартного.

    Получатель подарка по email-ссылке получал письмо по дефолтному шаблону,
    даже если админ сохранил свой в редакторе, — этот путь единственный из
    гостевых не заглядывал в override.
    """
    send = MagicMock(return_value=True)
    purchase = _gift(gift_recipient_type='email', gift_recipient_value='friend@example.com')
    mods, cab = _patches(send, override=('Свой заголовок', '<p>свой текст</p>'))
    with mods, cab:
        await notify_gift_claim_available(purchase, tariff_name='Lite', period_days=30)

    call = send.call_args_list[0]
    assert call.kwargs['to_email'] == 'friend@example.com'
    assert call.kwargs['subject'] == 'Свой заголовок'
    assert call.kwargs['body_html'] == '<p>свой текст</p>'


@pytest.mark.asyncio
async def test_override_lookup_uses_the_gift_template_type_and_claim_context() -> None:
    """Override ищется под тем же типом, что и дефолт, и с тем же контекстом (ссылка на claim)."""
    send = MagicMock(return_value=True)
    purchase = _gift(gift_recipient_type='email', gift_recipient_value='friend@example.com')
    mods, cab = _patches(send)
    with mods, cab:
        await notify_gift_claim_available(purchase, tariff_name='Lite', period_days=30)
        import sys

        calls = sys.modules['app.cabinet.services.email_template_overrides'].calls

    assert len(calls) == 1
    notification_type, language, context = calls[0]
    assert notification_type == 'guest_gift_received'
    assert language == 'ru'
    assert context['success_page_url'] == f'https://cab.example/buy/gift/{purchase.token}'
    assert context['tariff_name'] == 'Lite'
