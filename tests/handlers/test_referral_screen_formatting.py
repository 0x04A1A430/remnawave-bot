"""Экран «Реферальная программа»: одна пустая строка между блоками и без хвоста.

Жалоба: блоки разделялись двумя-четырьмя пустыми строками, а ссылка на бота
слипалась с заголовком «Последние начисления». Экран собирается из блоков,
которые соединяются ровно одним пустым рядом, а хвостовые переводы строки
срезаются.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.handlers.referral as ref
from app.services import referral_reward_service as reward_service


def _summary() -> dict:
    return {
        'invited_count': 3,
        'paid_referrals_count': 1,
        'active_referrals_count': 2,
        'conversion_rate': 33.3,
        'total_earned_kopeks': 50000,
        'month_earned_kopeks': 10000,
        'recent_earnings': [
            {
                'amount_kopeks': 10900,
                'days_granted': 0,
                'reason': 'referral_commission',
                'referral_name': 'GetCakeDieYoung',
            }
        ],
        'earnings_by_type': {
            'referral_commission': {'count': 1, 'total_amount_kopeks': 10900, 'total_days': 0},
        },
    }


@pytest.mark.parametrize('levels_scheme', [False, True])
@pytest.mark.asyncio
async def test_referral_screen_has_no_extra_blank_lines(monkeypatch, levels_scheme):
    captured = {}

    async def fake_edit(callback, text, keyboard):
        captured['text'] = text

    monkeypatch.setattr(ref, 'edit_or_answer_photo', fake_edit)
    monkeypatch.setattr(ref, 'get_user_referral_summary', AsyncMock(return_value=_summary()))
    monkeypatch.setattr(type(ref.settings), 'is_referral_program_enabled', lambda self: True)
    monkeypatch.setattr(type(ref.settings), 'is_referral_levels_scheme', lambda self: levels_scheme)
    monkeypatch.setattr(type(ref.settings), 'get_bot_referral_link', lambda self, code, bot: 'https://t.me/bot?start=X')
    monkeypatch.setattr(
        type(ref.settings), 'get_cabinet_referral_link', lambda self, code: 'https://cab.example/?ref=X'
    )
    monkeypatch.setattr(ref.settings, 'REFERRAL_FIRST_TOPUP_BONUS_KOPEKS', 0)
    monkeypatch.setattr(ref.settings, 'REFERRAL_INVITER_BONUS_KOPEKS', 0)

    if levels_scheme:
        monkeypatch.setattr(ref, '_reward_tariff_names', AsyncMock(return_value={}))
        monkeypatch.setattr(reward_service, 'describe_active_levels', AsyncMock(return_value=['Уровень 1: 10%']))
        monkeypatch.setattr(reward_service, 'describe_referee_bonus', AsyncMock(return_value='7 дн.'))
        monkeypatch.setattr(reward_service, 'resolve_tier_progress', AsyncMock(return_value=SimpleNamespace()))
        monkeypatch.setattr(reward_service, 'format_tier_progress', lambda *_args, **_kwargs: ['🏅 Ваш уровень: 1'])

    callback = MagicMock()
    callback.bot.get_me = AsyncMock(return_value=SimpleNamespace(username='bot'))
    callback.answer = AsyncMock()
    user = SimpleNamespace(id=1, referral_code='X', language='ru', full_name='Me')

    await ref.show_referral_info(callback, user, None)

    text = captured['text']
    assert '\n\n\n' not in text
    assert not text.endswith('\n')
    # Каждый блок отделён ровно одной пустой строкой, без склейки заголовков.
    assert '</blockquote>\n<b>Ссылка на бота:</b>' in text
    assert '<b>Ссылка на бота:</b>\nhttps://t.me/bot?start=X\n<b>Ссылка на кабинет:</b>' in text
    assert '<b>Ссылка на кабинет:</b>\nhttps://cab.example/?ref=X\n<b>Последние начисления:</b>' in text
    assert 'от GetCakeDieYoung</blockquote>\n<b>Доходы по типам:</b>' in text
