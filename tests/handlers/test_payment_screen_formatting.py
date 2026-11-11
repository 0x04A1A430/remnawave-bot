"""Экран способов оплаты: не больше одной пустой строки между блоками.

Жалоба: текст экрана собирался конкатенацией заголовка, подсказки, списка
способов и футера, поэтому локализованные блоки с собственными переводами
строки давали по две-три пустые строки, а способ без описания оставлял
висящий разделитель «Название - ». Итоговый текст перед отправкой проходит
через compact_payment_text.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.handlers.balance import main as balance_main
from app.services import user_cart_service as cart_module
from app.utils import payment_utils


def _methods() -> list[dict[str, str]]:
    return [
        {
            'id': 'stars',
            'name': 'Telegram Stars',
            'icon': '⭐',
            'description': 'быстро и удобно',
            'callback': 'topup_stars',
        },
        {
            'id': 'yookassa',
            'name': 'Банковская карта',
            'icon': '💳',
            'description': 'через YooKassa',
            'callback': 'topup_yookassa',
        },
        {
            # Нет локализованного описания — разделитель не должен остаться.
            'id': 'custom_test',
            'name': 'Test',
            'icon': '💳',
            'description': '',
            'callback': 'topup_custom_test',
        },
        {
            'id': 'support',
            'name': 'Через поддержку',
            'icon': '🛠️',
            'description': 'другие способы',
            'callback': 'topup_support',
        },
    ]


@pytest.mark.parametrize('language', ['ru', 'en', 'ua', 'zh', 'fa'])
def test_payment_methods_text_has_no_extra_blank_lines(monkeypatch, language):
    monkeypatch.setattr(payment_utils, 'get_available_payment_methods', _methods)

    text = payment_utils.get_payment_methods_text(language)

    assert '\n\n\n' not in text
    assert text == text.strip()
    assert '<blockquote>' in text
    assert '💳 <b>Test</b> - ' not in text


@pytest.mark.asyncio
async def test_payment_methods_screen_compacts_text_before_send(monkeypatch):
    monkeypatch.setattr(
        payment_utils,
        'get_payment_methods_text',
        lambda _language, **_kwargs: '\n\n💳 <b>Способы пополнения</b>\n\n\n\nВыберите способ:\n\n',
    )
    monkeypatch.setattr(balance_main, 'get_payment_methods_keyboard', lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(cart_module.user_cart_service, 'get_user_cart', AsyncMock(return_value=None))

    callback = SimpleNamespace(
        data='balance_topup',
        message=SimpleNamespace(text='старый экран', edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    user = SimpleNamespace(id=1, language='ru', restriction_topup=False)

    await balance_main.show_payment_methods(callback, user, AsyncMock(), AsyncMock())

    text = callback.message.edit_text.await_args.args[0]
    assert text == '💳 <b>Способы пополнения</b>\nВыберите способ:'
    assert '\n\n\n' not in text
