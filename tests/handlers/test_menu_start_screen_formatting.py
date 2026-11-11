"""Ключевые экраны меню и /start: максимум одна пустая строка, без пустых краёв.

Экраны собираются из необязательных блоков, и пустой блок, склеенный через
``'\\n\\n'``, оставлял в сообщении «дыру» из нескольких пустых строк (а иногда и
пустую строку в начале или в конце). Проверяем итоговый текст, который уходит
пользователю, а не промежуточные шаблоны.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.handlers import menu as menu_mod, start as start_mod


def _assert_clean_screen(text: str) -> None:
    assert '\n\n\n' not in text
    assert text == text.strip('\n')
    assert '\n \n' not in text


@pytest.mark.asyncio
async def test_main_menu_has_no_extra_blank_lines(monkeypatch):
    class DummyTexts:
        MAIN_MENU = '<b>{user_name}</b>\n<b>Подписка:</b> {subscription_status}\n\n'

        def t(self, key, default=None):  # pragma: no cover - trivial stub
            return default

    monkeypatch.setattr(type(menu_mod.settings), 'is_multi_tariff_enabled', lambda self: True)
    monkeypatch.setattr(
        menu_mod,
        '_get_multi_tariff_status',
        AsyncMock(return_value=('\n<blockquote>🟢 Базовый — до 01.01.2027</blockquote>', '')),
    )
    monkeypatch.setattr(
        menu_mod,
        'build_promo_offer_hint',
        AsyncMock(return_value='\n\n💡 Подсказка о скидке\n\n\n'),
    )
    monkeypatch.setattr(menu_mod, 'build_test_access_hint', AsyncMock(return_value=''))
    monkeypatch.setattr(
        menu_mod,
        'get_random_active_message',
        AsyncMock(return_value='\n🎲 Случайное сообщение\n\n'),
    )

    user = SimpleNamespace(id=1, full_name='Тест')
    text = await menu_mod.get_main_menu_text(user, DummyTexts(), object())

    _assert_clean_screen(text)
    assert text.startswith('<b>Тест</b>')
    assert '💡 Подсказка о скидке' in text
    assert '🎲 Случайное сообщение' in text
    # Блоки разделены ровно одной пустой строкой, а не «дырой».
    assert '</blockquote>\n\n💡 Подсказка о скидке' in text
    assert 'Подсказка о скидке\n\n🎲 Случайное сообщение' in text


@pytest.mark.asyncio
async def test_start_referral_question_screen_has_no_blank_line_holes(monkeypatch):
    class DummyTexts:
        def t(self, key, default=None):  # pragma: no cover - trivial stub
            if key == 'REFERRAL_CODE_QUESTION':
                # Как в app/localization/locales/ru.json: пустые строки по краям
                # и лишняя пустая строка между блоками.
                return '\n<b>У вас есть код?</b>\n\n\nВведите код или пропустите:\n\n'
            return default

    monkeypatch.setattr(start_mod, 'get_texts', lambda language=None: DummyTexts())
    monkeypatch.setattr(start_mod.settings, 'SKIP_REFERRAL_CODE', False)

    edit_text = AsyncMock()

    class DummyState:
        def __init__(self):
            self.state = None
            self.data = {'language': 'ru'}

        async def get_data(self):
            return dict(self.data)

        async def set_data(self, data):
            self.data = dict(data)

        async def set_state(self, value):
            self.state = value

    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=edit_text),
        from_user=SimpleNamespace(id=42),
        data='rules_accept',
    )

    await start_mod._continue_registration_after_rules(callback, DummyState(), object(), 'ru')

    text = edit_text.await_args.args[0]
    _assert_clean_screen(text)
    assert text == '<b>У вас есть код?</b>\n\nВведите код или пропустите:'
