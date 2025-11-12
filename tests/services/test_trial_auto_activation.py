from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Subscription, User
from app.services.trial_activation_service import (
    TrialPaymentInsufficientFunds,
    maybe_activate_trial_after_topup,
)


@pytest.fixture
def auto_trial_user():
    user = MagicMock(spec=User)
    user.id = 1
    user.telegram_id = 123
    user.language = "ru"
    user.subscription = None
    user.has_had_paid_subscription = False
    user.trial_activation_pending = True
    return user


@pytest.fixture
def auto_trial_db():
    db = AsyncMock(spec=AsyncSession)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_maybe_activate_trial_after_topup_no_pending(auto_trial_db, auto_trial_user):
    auto_trial_user.trial_activation_pending = False

    result = await maybe_activate_trial_after_topup(auto_trial_db, auto_trial_user)

    assert result is None


@pytest.mark.asyncio
async def test_maybe_activate_trial_after_topup_insufficient_funds(auto_trial_db, auto_trial_user):
    with (
        patch(
            "app.services.trial_activation_service.preview_trial_activation_charge",
            side_effect=TrialPaymentInsufficientFunds(100, 50),
        ) as preview,
        patch("app.services.trial_activation_service.create_trial_subscription") as create_subscription,
    ):
        result = await maybe_activate_trial_after_topup(auto_trial_db, auto_trial_user)

    preview.assert_called_once_with(auto_trial_user)
    create_subscription.assert_not_called()
    assert result is not None
    assert result.success is False
    assert result.reason == "insufficient_funds"


@pytest.mark.asyncio
async def test_maybe_activate_trial_after_topup_success(auto_trial_db, auto_trial_user, monkeypatch):
    subscription = MagicMock(spec=Subscription)

    monkeypatch.setattr(
        "app.services.trial_activation_service.settings",
        MagicMock(
            is_devices_selection_enabled=lambda: True,
            get_disabled_mode_device_limit=lambda: None,
        ),
    )

    with (
        patch(
            "app.services.trial_activation_service.preview_trial_activation_charge",
            return_value=0,
        ),
        patch(
            "app.services.trial_activation_service.create_trial_subscription",
            new=AsyncMock(return_value=subscription),
        ) as create_subscription,
        patch(
            "app.services.trial_activation_service.charge_trial_activation_if_required",
            new=AsyncMock(return_value=0),
        ) as charge_balance,
        patch(
            "app.services.trial_activation_service.SubscriptionService",
            return_value=MagicMock(create_remnawave_user=AsyncMock()),
        ) as subscription_service,
        patch(
            "app.services.trial_activation_service.clear_trial_activation_pending",
            new=AsyncMock(),
        ) as clear_flag,
        patch(
            "app.services.trial_activation_service._notify_user_about_auto_trial",
            new=AsyncMock(),
        ) as notify_user,
        patch(
            "app.services.trial_activation_service._notify_admin_about_auto_trial",
            new=AsyncMock(),
        ) as notify_admin,
    ):
        result = await maybe_activate_trial_after_topup(
            auto_trial_db,
            auto_trial_user,
            bot=AsyncMock(),
        )

    create_subscription.assert_awaited_once_with(
        auto_trial_db,
        auto_trial_user.id,
        device_limit=None,
    )
    charge_balance.assert_awaited_once()
    subscription_service.return_value.create_remnawave_user.assert_awaited_once_with(
        auto_trial_db,
        subscription,
    )
    clear_flag.assert_awaited()
    notify_user.assert_awaited()
    notify_admin.assert_awaited()
    assert result is not None
    assert result.success is True
