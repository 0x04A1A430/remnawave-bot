"""Старая подписка в меню бота: одна кнопка «Перейти на тариф».

Старая подписка — платная, без тарифа, при включённом режиме тарифов (куплена
в классике, потом оператор включил тарифы). Продлить её нельзя и автоплатёж
для неё не работает, но меню показывало [Продлить] [Автоплатёж] и «Тариф»
с мгновенным переключением — все три вели в тупик. Теперь у такой подписки
один путь: список тарифов, выбранный тариф надевается на неё же.
"""

from __future__ import annotations

from types import SimpleNamespace

import app.keyboards.inline as kb


def _callbacks(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]


def _sub(*, tariff_id: int | None, actual_status: str = 'active') -> SimpleNamespace:
    tariff = None
    if tariff_id is not None:
        tariff = SimpleNamespace(id=tariff_id, is_daily=False, is_free=False, can_topup_traffic=lambda: False)
    return SimpleNamespace(
        id=1,
        actual_status=actual_status,
        status=actual_status,
        tariff_id=tariff_id,
        tariff=tariff,
        traffic_limit_gb=0,
        end_date=None,
        is_daily_paused=False,
    )


def _patch_mode(monkeypatch, *, tariffs: bool) -> None:
    from app.config import Settings

    monkeypatch.setattr(Settings, 'is_tariffs_mode', lambda self: tariffs)
    monkeypatch.setattr(Settings, 'is_multi_tariff_enabled', lambda self: tariffs)
    monkeypatch.setattr(kb, 'get_display_subscription_link', lambda sub: None)


def _keyboard(sub) -> list[str]:
    return _callbacks(kb.get_subscription_keyboard('ru', has_subscription=True, is_trial=False, subscription=sub))


def test_legacy_subscription_offers_only_move_to_tariff(monkeypatch):
    _patch_mode(monkeypatch, tariffs=True)

    cbs = _keyboard(_sub(tariff_id=None))

    assert cbs.count('tariff_switch') == 1
    assert 'subscription_extend' not in cbs, 'продления у старой подписки нет'
    assert 'subscription_autopay' not in cbs, 'автоплатёж старой подписке недоступен'
    assert 'instant_switch' not in cbs, 'мгновенное переключение считает разницу с тарифом, которого нет'


def test_expired_legacy_subscription_still_moves_to_tariff(monkeypatch):
    """Истёкшая старая подписка тоже переводится на тариф той же строкой, а не покупкой с нуля."""
    _patch_mode(monkeypatch, tariffs=True)

    cbs = _keyboard(_sub(tariff_id=None, actual_status='expired'))

    assert cbs.count('tariff_switch') == 1
    assert 'menu_buy' not in cbs
    assert 'subscription_extend' not in cbs


def test_subscription_with_tariff_keeps_renew_and_autopay(monkeypatch):
    _patch_mode(monkeypatch, tariffs=True)

    cbs = _keyboard(_sub(tariff_id=7))

    assert 'subscription_extend' in cbs
    assert 'subscription_autopay' in cbs
    assert 'instant_switch' in cbs


def test_classic_mode_subscription_keeps_renew(monkeypatch):
    """В классическом режиме подписка без тарифа — обычная, продление на месте."""
    _patch_mode(monkeypatch, tariffs=False)

    cbs = _keyboard(_sub(tariff_id=None))

    assert 'subscription_extend' in cbs
    assert 'tariff_switch' not in cbs
