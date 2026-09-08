"""Дата окончания в панели у истёкшей подписки не переписывается «сейчас плюс минута».

Из отчёта: у всех expired и disabled подписок в панели дата стала «Истекла N дней
назад», а после ручной синхронизации из бота — «Истекла минуту назад». В боте даты
при этом прежние: затиралась только копия в панели.

Причина: доступ истёкшей подписке закрывают статусом DISABLED, но вместе со
статусом отправляли и дату — а прошедшую дату панель не принимает, поэтому её
подменяли ближайшим будущим. Настоящая дата окончания при этом терялась.

Правило теперь одно на все точки записи: живой подписке — её дата, истёкшей при
обновлении поле не отправляется вовсе, и только при СОЗДАНИИ аккаунта, где без
даты нельзя, остаётся допустимый минимум.
"""

from __future__ import annotations

import ast
import pathlib
import re
from datetime import UTC, datetime, timedelta

import pytest

from app.services.panel_expiry import panel_expire_at


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
PAST = NOW - timedelta(days=30)
FUTURE = NOW + timedelta(days=30)


def test_live_subscription_keeps_its_own_date():
    assert panel_expire_at(FUTURE, is_active=True, creating=False, now=NOW) == FUTURE
    assert panel_expire_at(FUTURE, is_active=True, creating=True, now=NOW) == FUTURE


def test_expired_subscription_does_not_touch_the_date_on_update():
    """Главное свойство: поле не отправляется, панель хранит настоящую дату."""
    assert panel_expire_at(PAST, is_active=False, creating=False, now=NOW) is None


def test_new_panel_account_still_gets_an_acceptable_date():
    """При создании без даты нельзя, а прошедшую панель не примет."""
    created = panel_expire_at(PAST, is_active=False, creating=True, now=NOW)

    assert created == NOW + timedelta(minutes=1)


def test_future_date_survives_even_for_an_inactive_subscription():
    """Заблокированный пользователь с ещё не истёкшей подпиской: дату не занижаем."""
    assert panel_expire_at(FUTURE, is_active=False, creating=True, now=NOW) == FUTURE


# ==================== сторож на все точки записи ====================

WRITERS = (
    'app/services/subscription_service.py',
    'app/services/monitoring_service.py',
    'app/services/remnawave_service.py',
    'app/cabinet/routes/admin_users.py',
)

#: Прежняя формула. Любая её копия — это ещё одно место, которое затирает дату.
OLD_FORMULA = re.compile(r'(now|current_time|datetime\.now\(UTC\))\s*\+\s*timedelta\(minutes=1\)')


@pytest.mark.parametrize('path', WRITERS)
def test_no_writer_builds_the_date_by_hand(path):
    source = pathlib.Path(path).read_text(encoding='utf-8')

    assert not OLD_FORMULA.search(source), (
        f'{path}: дата окончания для панели снова считается на месте — правило живёт в app/services/panel_expiry.py'
    )


@pytest.mark.parametrize('path', WRITERS)
def test_every_writer_uses_the_shared_rule(path):
    tree = ast.parse(pathlib.Path(path).read_text(encoding='utf-8'))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == 'app.services.panel_expiry'
        for alias in node.names
    }

    assert 'panel_expire_at' in imported, f'{path} пишет дату в панель мимо общего правила'
