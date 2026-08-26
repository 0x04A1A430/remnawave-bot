"""Поведение предиката ``not_referee_directed()`` на настоящих строках.

Предыдущая проверка считала вхождения строки ``not_referee_directed()`` в
исходнике сводки. Она подтверждала, что предикат ВЫЗЫВАЮТ, и ничего не говорила
о том, что он делает: подмена его тела на всегда-истинное условие оставляла весь
набор тестов зелёным. Ровно это и произошло — мутация дожила до коммита.

Здесь предикат исполняется в SQLite на реальных строках ledger'а, поэтому
сломанное тело роняет тест немедленно.
"""

import pytest
from sqlalchemy import func, select

from app.database.crud.referral import not_referee_directed
from app.database.models import ReferralEarning
from tests.fixtures.sqlite_memory import memory_session


REFERRER_ID = 1
REFEREE_ID = 2


def _earning(**kwargs) -> ReferralEarning:
    base = {
        'user_id': REFERRER_ID,
        'referral_id': REFEREE_ID,
        'amount_kopeks': 0,
        'reason': 'referral_commission_topup',
        'reward_type': 'money',
        'level': 1,
        'days_granted': 0,
    }
    base.update(kwargs)
    return ReferralEarning(**base)


@pytest.mark.asyncio
async def test_referee_directed_days_are_excluded(monkeypatch):
    """Строка награды приглашённому не должна попадать в заработок владельца user_id."""
    async with memory_session(monkeypatch, [ReferralEarning.__table__]) as db:
        db.add_all(
            [
                # Заработок пригласившего: деньги и дни.
                _earning(amount_kopeks=10_000, reason='referral_commission_topup'),
                _earning(days_granted=5, reward_type='days', reason='referral_days_reward'),
                # Награда ПРИГЛАШЁННОМУ: принадлежит ему, пара зеркалирована.
                ReferralEarning(
                    user_id=REFEREE_ID,
                    referral_id=REFERRER_ID,
                    amount_kopeks=0,
                    reason='referral_days_bonus',
                    reward_type='days',
                    level=1,
                    days_granted=7,
                ),
            ]
        )
        await db.commit()

        # Заработок пригласившего: обе его строки, чужая не считается.
        result = await db.execute(
            select(
                func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0),
                func.coalesce(func.sum(ReferralEarning.days_granted), 0),
            ).where(ReferralEarning.user_id == REFERRER_ID, not_referee_directed())
        )
        money, days = result.one()
        assert (money, days) == (10_000, 5)

        # У приглашённого, не пригласившего никого, заработка нет.
        result = await db.execute(
            select(func.coalesce(func.sum(ReferralEarning.days_granted), 0)).where(
                ReferralEarning.user_id == REFEREE_ID, not_referee_directed()
            )
        )
        assert result.scalar() == 0, 'бонус приглашённому — не его реферальный заработок'

        # Без предиката он бы засчитался: это и есть цена ошибки.
        result = await db.execute(
            select(func.coalesce(func.sum(ReferralEarning.days_granted), 0)).where(
                ReferralEarning.user_id == REFEREE_ID
            )
        )
        assert result.scalar() == 7


@pytest.mark.asyncio
async def test_predicate_keeps_every_referrer_reason(monkeypatch):
    """Предикат обязан отбрасывать ТОЛЬКО награды приглашённому.

    Слишком широкое условие тихо обнулило бы часть заработка пригласившего.
    """
    async with memory_session(monkeypatch, [ReferralEarning.__table__]) as db:
        reasons = [
            'referral_first_topup',
            'referral_commission_topup',
            'referral_registration_reward',
            'referral_days_reward',
            'referral_registration_pending',
        ]
        db.add_all([_earning(reason=reason, amount_kopeks=100) for reason in reasons])
        await db.commit()

        result = await db.execute(
            select(func.count(ReferralEarning.id)).where(ReferralEarning.user_id == REFERRER_ID, not_referee_directed())
        )
        assert result.scalar() == len(reasons)


@pytest.mark.asyncio
async def test_distinct_referral_id_does_not_count_own_inviter(monkeypatch):
    """«Сколько у меня рефералов» через DISTINCT referral_id.

    Зеркалированная строка ставит в ``referral_id`` пригласившего: без предиката
    пользователь получает в свои рефералы того, кто пригласил его самого.
    """
    async with memory_session(monkeypatch, [ReferralEarning.__table__]) as db:
        db.add(
            ReferralEarning(
                user_id=REFEREE_ID,
                referral_id=REFERRER_ID,
                amount_kopeks=0,
                reason='referral_days_bonus',
                reward_type='days',
                level=1,
                days_granted=7,
            )
        )
        await db.commit()

        result = await db.execute(
            select(func.count(func.distinct(ReferralEarning.referral_id))).where(
                ReferralEarning.user_id == REFEREE_ID, not_referee_directed()
            )
        )
        assert result.scalar() == 0

        result = await db.execute(
            select(func.count(func.distinct(ReferralEarning.referral_id))).where(ReferralEarning.user_id == REFEREE_ID)
        )
        assert result.scalar() == 1, 'без предиката пригласивший попадает в собственные рефералы'
