"""Integration-style tests for the СБП-автопродление handlers
(``handle_sbp_recurring_menu`` / ``_enable`` / ``_cancel``,
``app/handlers/subscription/autopay.py``).

Drives the real handler functions with the heavy I/O mocked (DB session,
Platega network calls via the module-level helpers, subscription
resolution), following the same style as
``tests/handlers/test_device_rename_cancel.py``: real handler code path,
``MagicMock``/``AsyncMock`` stand-ins for ``callback``/``db_user``/``db``,
``monkeypatch`` on the exact seam each handler calls through.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import app.handlers.subscription.autopay as autopay_mod
from app.config import settings


def _configure_gate(monkeypatch, enabled: bool) -> None:
    """Same fields as test_platega_recurrent_cancel_hooks.py's _configure_gate_on,
    toggled both ways — is_platega_recurrent_enabled() reads all four."""
    monkeypatch.setattr(settings, 'PLATEGA_ENABLED', enabled, raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_MERCHANT_ID', ('m' if enabled else None), raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_SECRET', ('s' if enabled else None), raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_RECURRENT_ENABLED', enabled, raising=False)


def _make_callback():
    cb = MagicMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _make_user():
    user = MagicMock()
    user.id = 1
    user.language = 'ru'
    return user


def _keyboard_callbacks(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]


def _keyboard_urls(markup) -> list[str]:
    return [btn.url for row in markup.inline_keyboard for btn in row if btn.url]


# --- handle_sbp_recurring_menu ---


async def test_menu_gate_off_shows_alert_and_does_not_render(monkeypatch):
    _configure_gate(monkeypatch, False)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()

    await autopay_mod.handle_sbp_recurring_menu(cb, user, db)

    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs.get('show_alert') is True
    cb.message.edit_text.assert_not_awaited()


async def test_menu_no_active_record_shows_enable_button(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    subscription = SimpleNamespace(id=10)

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(subscription, 10)))
    monkeypatch.setattr(
        'app.database.crud.platega_subscription.get_active_platega_subscription_by_subscription',
        AsyncMock(return_value=None),
    )

    await autopay_mod.handle_sbp_recurring_menu(cb, user, db)

    cb.message.edit_text.assert_awaited_once()
    _, kwargs = cb.message.edit_text.call_args
    callbacks = _keyboard_callbacks(kwargs['reply_markup'])
    assert 'sbp_recurring_enable' in callbacks
    assert 'sbp_recurring_cancel' not in callbacks


async def test_menu_active_record_shows_cancel_button(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    subscription = SimpleNamespace(id=10)
    record = SimpleNamespace(status='ACTIVE', next_charge_at=None)

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(subscription, 10)))
    monkeypatch.setattr(
        'app.database.crud.platega_subscription.get_active_platega_subscription_by_subscription',
        AsyncMock(return_value=record),
    )

    await autopay_mod.handle_sbp_recurring_menu(cb, user, db)

    cb.message.edit_text.assert_awaited_once()
    _, kwargs = cb.message.edit_text.call_args
    callbacks = _keyboard_callbacks(kwargs['reply_markup'])
    assert 'sbp_recurring_cancel' in callbacks
    assert 'sbp_recurring_enable' not in callbacks


async def test_menu_no_subscription_shows_alert(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(None, None)))

    await autopay_mod.handle_sbp_recurring_menu(cb, user, db)

    cb.message.edit_text.assert_not_awaited()


# --- handle_sbp_recurring_enable ---


async def test_enable_gate_off_shows_alert(monkeypatch):
    _configure_gate(monkeypatch, False)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()

    await autopay_mod.handle_sbp_recurring_enable(cb, user, db)

    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs.get('show_alert') is True
    cb.message.edit_text.assert_not_awaited()


async def test_enable_without_tariff_shows_alert_and_skips_helper(monkeypatch):
    """No tariff on the subscription -> must short-circuit BEFORE calling the
    enable helper: create_platega_sbp_subscription has no None-guard on
    tariff.get_purchasable_price_for_period() (contract from Task 5), so
    calling through would be an AttributeError, not a friendly message."""
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    subscription = SimpleNamespace(id=10, tariff=None)

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(subscription, 10)))
    mock_enable = AsyncMock()
    monkeypatch.setattr('app.services.payment.platega.enable_platega_sbp_recurring', mock_enable)

    await autopay_mod.handle_sbp_recurring_enable(cb, user, db)

    mock_enable.assert_not_awaited()
    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs.get('show_alert') is True


async def test_enable_value_error_shows_friendly_alert(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    subscription = SimpleNamespace(id=10, tariff=SimpleNamespace(id=5))

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(subscription, 10)))
    monkeypatch.setattr(
        'app.services.payment.platega.enable_platega_sbp_recurring',
        AsyncMock(side_effect=ValueError('no price for period')),
    )

    await autopay_mod.handle_sbp_recurring_enable(cb, user, db)

    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs.get('show_alert') is True
    cb.message.edit_text.assert_not_awaited()


async def test_enable_runtime_error_shows_friendly_alert(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    subscription = SimpleNamespace(id=10, tariff=SimpleNamespace(id=5))

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(subscription, 10)))
    monkeypatch.setattr(
        'app.services.payment.platega.enable_platega_sbp_recurring',
        AsyncMock(side_effect=RuntimeError('platega down')),
    )

    await autopay_mod.handle_sbp_recurring_enable(cb, user, db)

    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs.get('show_alert') is True
    cb.message.edit_text.assert_not_awaited()


async def test_enable_success_shows_redirect_url_button(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    subscription = SimpleNamespace(id=10, tariff=SimpleNamespace(id=5))

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(subscription, 10)))
    monkeypatch.setattr(
        'app.services.payment.platega.enable_platega_sbp_recurring',
        AsyncMock(
            return_value={
                'local_id': 1,
                'platega_subscription_id': 'tx-1',
                'redirect_url': 'https://pay.example/1',
                'status': 'PENDING',
            }
        ),
    )

    await autopay_mod.handle_sbp_recurring_enable(cb, user, db)

    cb.message.edit_text.assert_awaited_once()
    _, kwargs = cb.message.edit_text.call_args
    assert 'https://pay.example/1' in _keyboard_urls(kwargs['reply_markup'])


async def test_enable_idempotent_return_without_redirect_shows_status(monkeypatch):
    """Idempotent return (already-active record) may carry no redirect_url —
    must not build a dead url=None button (Telegram would reject it); falls
    back to re-rendering the status view instead."""
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    subscription = SimpleNamespace(id=10, tariff=SimpleNamespace(id=5))

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(subscription, 10)))
    monkeypatch.setattr(
        'app.services.payment.platega.enable_platega_sbp_recurring',
        AsyncMock(
            return_value={
                'local_id': 1,
                'platega_subscription_id': 'tx-1',
                'redirect_url': None,
                'status': 'ACTIVE',
            }
        ),
    )
    monkeypatch.setattr(
        'app.database.crud.platega_subscription.get_active_platega_subscription_by_subscription',
        AsyncMock(return_value=SimpleNamespace(status='ACTIVE', next_charge_at=None)),
    )

    await autopay_mod.handle_sbp_recurring_enable(cb, user, db)

    # Falls through to handle_sbp_recurring_menu, which edits with the status view.
    cb.message.edit_text.assert_awaited_once()
    _, kwargs = cb.message.edit_text.call_args
    assert _keyboard_urls(kwargs['reply_markup']) == []
    assert 'sbp_recurring_cancel' in _keyboard_callbacks(kwargs['reply_markup'])


# --- handle_sbp_recurring_cancel ---


async def test_cancel_gate_off_shows_alert_and_skips_helper(monkeypatch):
    _configure_gate(monkeypatch, False)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()

    mock_cancel = AsyncMock()
    monkeypatch.setattr('app.services.payment.platega.cancel_platega_recurring_for_subscription_safe', mock_cancel)

    await autopay_mod.handle_sbp_recurring_cancel(cb, user, db)

    mock_cancel.assert_not_awaited()
    cb.answer.assert_awaited_once()
    assert cb.answer.await_args.kwargs.get('show_alert') is True


async def test_cancel_gate_on_calls_helper_and_refreshes_menu(monkeypatch):
    _configure_gate(monkeypatch, True)
    cb, user, db = _make_callback(), _make_user(), AsyncMock()
    subscription = SimpleNamespace(id=10)

    monkeypatch.setattr(autopay_mod, '_resolve_subscription', AsyncMock(return_value=(subscription, 10)))
    mock_cancel = AsyncMock()
    monkeypatch.setattr('app.services.payment.platega.cancel_platega_recurring_for_subscription_safe', mock_cancel)
    monkeypatch.setattr(
        'app.database.crud.platega_subscription.get_active_platega_subscription_by_subscription',
        AsyncMock(return_value=None),
    )

    await autopay_mod.handle_sbp_recurring_cancel(cb, user, db)

    mock_cancel.assert_awaited_once_with(db, 10)
    # handle_sbp_recurring_cancel re-invokes handle_sbp_recurring_menu, which
    # re-renders the (now inactive) status view.
    cb.message.edit_text.assert_awaited_once()
    _, kwargs = cb.message.edit_text.call_args
    assert 'sbp_recurring_enable' in _keyboard_callbacks(kwargs['reply_markup'])
