"""Снимок панели не укорачивает срок, за который недавно заплатили.

Панель — истина, но её снимок бывает устаревшим ровно тогда, когда запись
нового срока из бота в панель не прошла: панель хранит старую дату, бот —
оплаченную. Вебхук или сверка тогда откатывали оплату, а автопродление
списывало ещё раз (15.09, подписка #3639). Пока с оплаты не прошло двух суток,
более ранняя дата панели не принимается; более поздняя (продлили в панели) —
принимается как раньше.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.panel_sync.projection import (
    PAID_DATE_HOLD,
    WEBHOOK,
    PanelSnapshot,
    panel_date_behind_paid_renewal,
    project_onto_subscription,
)


NOW = datetime(2026, 9, 15, 21, 40, tzinfo=UTC)
OLD_END = datetime(2026, 9, 17, 21, 12, tzinfo=UTC)
PAID_UNTIL = OLD_END + timedelta(days=30)


def _subscription(end_date: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        status='active',
        end_date=end_date,
        traffic_used_gb=0.0,
        traffic_limit_gb=150,
        device_limit=3,
        connected_squads=['s1'],
        remnawave_short_uuid='abc',
        subscription_url=None,
        subscription_crypto_link=None,
        grace_session_open=False,
        grace_candidate_reason=None,
        grace_candidate_at=None,
        updated_at=NOW - timedelta(hours=11),
        last_webhook_update_at=None,
    )


def _snapshot(expire_at: datetime) -> PanelSnapshot:
    return PanelSnapshot(status='ACTIVE', expire_at=expire_at, squads=('s1',), short_uuid='abc')


def test_earlier_panel_date_is_held_right_after_a_payment():
    sub = _subscription(PAID_UNTIL)
    paid_at = NOW - timedelta(hours=11)

    assert panel_date_behind_paid_renewal(sub, _snapshot(OLD_END), paid_at=paid_at, now=NOW)
    changed = project_onto_subscription(sub, _snapshot(OLD_END), now=NOW, policy=WEBHOOK, paid_at=paid_at)

    assert sub.end_date == PAID_UNTIL
    assert 'end_date' not in changed


def test_later_panel_date_is_still_taken_after_a_payment():
    """Продлили ещё и в панели — это не откат, берём как раньше."""
    sub = _subscription(PAID_UNTIL)
    extended_in_panel = PAID_UNTIL + timedelta(days=30)

    project_onto_subscription(
        sub, _snapshot(extended_in_panel), now=NOW, policy=WEBHOOK, paid_at=NOW - timedelta(hours=1)
    )

    assert sub.end_date == extended_in_panel


def test_hold_expires_with_the_window_and_without_a_payment():
    stale = _snapshot(OLD_END)

    old_payment = _subscription(PAID_UNTIL)
    project_onto_subscription(
        old_payment, stale, now=NOW, policy=WEBHOOK, paid_at=NOW - PAID_DATE_HOLD - timedelta(minutes=1)
    )
    assert old_payment.end_date == OLD_END, 'оплата давно — панель снова истина'

    never_paid = _subscription(PAID_UNTIL)
    project_onto_subscription(never_paid, stale, now=NOW, policy=WEBHOOK, paid_at=None)
    assert never_paid.end_date == OLD_END


def test_minute_tolerance_is_not_a_hold():
    """Разница в пределах минуты и так не переносится — сторож на неё не реагирует."""
    sub = _subscription(PAID_UNTIL)

    assert not panel_date_behind_paid_renewal(sub, _snapshot(PAID_UNTIL - timedelta(seconds=30)), paid_at=NOW, now=NOW)
