"""Pins the autopay button on the multi-tariff subscription detail card.

Regression: in multi-tariff mode (`MULTI_TARIFF_ENABLED=true`), the detail
keyboard for a single subscription used to show 6 buttons (link / extend /
traffic / devices / reissue / back). The 💳 Автоплатеж button was only
present in the legacy single-subscription menu, so users in multi-tariff
mode had no way to reach the autopay menu from a specific subscription.

This test file pins:
  1. The autopay button is present on active subscriptions
  2. The autopay button is NOT shown on expired/disabled subscriptions
     (no point auto-renewing what's already inactive — and the rest of
     the action set is also stripped for those statuses)
  3. The autopay button uses the legacy callback `subscription_autopay`
     without sub_id — multi-tariff resolution flows through FSM's
     active_subscription_id which `show_subscription_detail` must set.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.handlers.subscription.my_subscriptions import _build_subscription_detail_keyboard


def _callbacks(keyboard) -> list[str]:
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


def test_autopay_button_present_for_active_subscription() -> None:
    sub = SimpleNamespace(actual_status='active')

    keyboard = _build_subscription_detail_keyboard(sub_id=42, sub=sub)

    callbacks = _callbacks(keyboard)
    assert 'subscription_autopay' in callbacks, (
        'Multi-tariff detail card must expose 💳 Автоплатеж; without this button '
        'users with multiple subscriptions have no entry point to the autopay menu.'
    )


def test_autopay_button_uses_legacy_callback_without_sub_id() -> None:
    """The button intentionally uses the existing `subscription_autopay` exact-match
    callback rather than a sub_id-encoded variant. Sub_id resolution flows through
    FSM `active_subscription_id`, set by show_subscription_detail. Changing this
    callback to e.g. `apm:{sub_id}` would require rewiring the entire autopay
    flow (toggle/days/period handlers + back buttons) — keep it as-is."""
    sub = SimpleNamespace(actual_status='active')

    keyboard = _build_subscription_detail_keyboard(sub_id=42, sub=sub)

    autopay_buttons = [
        button
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data == 'subscription_autopay'
    ]
    assert len(autopay_buttons) == 1
    assert ':42' not in autopay_buttons[0].callback_data
    assert '42' not in autopay_buttons[0].callback_data


def test_autopay_button_hidden_on_expired_subscription() -> None:
    sub = SimpleNamespace(actual_status='expired')

    keyboard = _build_subscription_detail_keyboard(sub_id=42, sub=sub)

    assert 'subscription_autopay' not in _callbacks(keyboard)


def test_autopay_button_hidden_on_disabled_subscription() -> None:
    sub = SimpleNamespace(actual_status='disabled')

    keyboard = _build_subscription_detail_keyboard(sub_id=42, sub=sub)

    assert 'subscription_autopay' not in _callbacks(keyboard)


def test_autopay_button_present_when_status_unknown() -> None:
    """When sub=None, the keyboard treats the subscription as active (is_inactive=False).
    The autopay button must be there too — symmetry with traffic/devices buttons that
    appear under the same condition."""
    keyboard = _build_subscription_detail_keyboard(sub_id=42, sub=None)

    assert 'subscription_autopay' in _callbacks(keyboard)
