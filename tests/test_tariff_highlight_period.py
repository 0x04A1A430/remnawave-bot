"""Выделение одного периода тарифа как самого выгодного.

Механизма не было вовсе: клиент видел четыре одинаковые строки и выбирал самую
дешёвую, хотя длинный период оператору выгоднее. Выделение живёт у тарифа, в днях,
и должно доезжать до всех четырёх клавиатур бота и до обеих ручек кабинета.

Отдельно держится инвариант: выделенным может быть только существующий период —
иначе после правки цен метка молча ничего не выделяет, а старое решение оператора
всплывает, если тот же период когда-нибудь вернут.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.handlers.subscription.tariff_purchase import (
    get_tariff_extend_keyboard,
    get_tariff_periods_keyboard,
    get_tariff_periods_keyboard_with_traffic,
    get_tariff_switch_periods_keyboard,
)


BADGE = '⭐ выгодно'


def _tariff(highlight: int | None = None, **extra) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        name='Базовый',
        period_prices={'30': 60000, '90': 150000, '180': 270000},
        highlight_period_days=highlight,
        device_limit=1,
        device_price_kopeks=None,
        **extra,
    )


def _labels(keyboard) -> list[str]:
    return [button.text for row in keyboard.inline_keyboard for button in row]


def _extend_keyboard(tariff, language):
    """Продление берёт те же периоды, но требует id подписки в колбэке."""
    return get_tariff_extend_keyboard(tariff, language, subscription_id=1)


KEYBOARDS = (
    get_tariff_periods_keyboard,
    get_tariff_periods_keyboard_with_traffic,
    get_tariff_switch_periods_keyboard,
    _extend_keyboard,
)


@pytest.mark.parametrize('build', KEYBOARDS, ids=lambda f: f.__name__)
def test_highlighted_period_is_marked_in_every_keyboard(build):
    labels = _labels(build(_tariff(highlight=180), 'ru'))

    marked = [label for label in labels if BADGE in label]
    assert len(marked) == 1, labels
    assert '180' in marked[0]


@pytest.mark.parametrize('build', KEYBOARDS, ids=lambda f: f.__name__)
def test_nothing_is_marked_without_a_highlight(build):
    labels = _labels(build(_tariff(highlight=None), 'ru'))

    assert not [label for label in labels if BADGE in label]


@pytest.mark.parametrize('build', KEYBOARDS, ids=lambda f: f.__name__)
def test_stale_highlight_marks_nothing(build):
    """Период удалили, а число осталось: отмечать нечего, но и падать нельзя."""
    labels = _labels(build(_tariff(highlight=365), 'ru'))

    assert not [label for label in labels if BADGE in label]
    assert len(labels) == 4  # три периода и «назад»


def test_prices_stay_visible_next_to_the_badge():
    """Отметка добавляется к строке, а не вместо неё: цена обязана остаться."""
    labels = _labels(get_tariff_periods_keyboard(_tariff(highlight=90), 'ru'))
    marked = next(label for label in labels if BADGE in label)

    assert '1500' in marked.replace(' ', '').replace(' ', '')


# ==================== нормализация в CRUD ====================


def test_resolve_keeps_only_existing_periods():
    from app.database.crud.tariff import _resolve_highlight_period

    prices = {'30': 60000, '90': 150000}

    assert _resolve_highlight_period(prices, 90) == 90
    assert _resolve_highlight_period(prices, '30') == 30
    assert _resolve_highlight_period(prices, 180) is None
    assert _resolve_highlight_period(prices, None) is None
    assert _resolve_highlight_period(prices, 'ерунда') is None


# ==================== сохранение в БД ====================


@pytest.mark.asyncio
async def test_highlight_survives_price_edit_only_if_the_period_survives(monkeypatch):
    """Правка цен не должна оставлять метку на периоде, которого больше нет."""
    from app.database.crud.tariff import create_tariff, update_tariff
    from app.database.models import PromoGroup, Subscription, Tariff, tariff_promo_groups
    from tests.fixtures.sqlite_memory import memory_session

    tables = (
        Tariff.__table__,
        PromoGroup.__table__,
        Subscription.__table__,
        tariff_promo_groups,
    )

    async with memory_session(monkeypatch, tables) as db:
        tariff = await create_tariff(
            db=db,
            name='Базовый',
            period_prices={30: 60000, 90: 150000, 180: 270000},
            highlight_period_days=180,
        )
        assert tariff.highlight_period_days == 180

        # Периоды переписали, выделенный остался — метка на месте.
        tariff = await update_tariff(db, tariff, period_prices={30: 60000, 180: 250000})
        assert tariff.highlight_period_days == 180

        # Выделенный период убрали — метка обязана уйти сама.
        tariff = await update_tariff(db, tariff, period_prices={30: 60000, 90: 140000})
        assert tariff.highlight_period_days is None


@pytest.mark.asyncio
async def test_highlight_cannot_point_at_a_missing_period(monkeypatch):
    from app.database.crud.tariff import create_tariff, update_tariff
    from app.database.models import PromoGroup, Subscription, Tariff, tariff_promo_groups
    from tests.fixtures.sqlite_memory import memory_session

    tables = (Tariff.__table__, PromoGroup.__table__, Subscription.__table__, tariff_promo_groups)

    async with memory_session(monkeypatch, tables) as db:
        tariff = await create_tariff(
            db=db,
            name='Базовый',
            period_prices={30: 60000},
            highlight_period_days=365,
        )
        assert tariff.highlight_period_days is None

        tariff = await update_tariff(db, tariff, highlight_period_days=90)
        assert tariff.highlight_period_days is None

        tariff = await update_tariff(db, tariff, highlight_period_days=30)
        assert tariff.highlight_period_days == 30

        # None снимает выделение, «не передан» — не трогает.
        tariff = await update_tariff(db, tariff, name='Базовый+')
        assert tariff.highlight_period_days == 30
        tariff = await update_tariff(db, tariff, highlight_period_days=None)
        assert tariff.highlight_period_days is None
