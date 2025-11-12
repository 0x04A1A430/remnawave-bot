from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.handlers.menu import _get_subscription_status


class DummyTexts:
    def t(self, key: str, default: str):  # pragma: no cover - simple stub
        return default


def _build_user_with_subscription(actual_status: str, is_trial: bool, days_left: int):
    subscription = MagicMock()
    subscription.actual_status = actual_status
    subscription.is_trial = is_trial
    subscription.end_date = datetime.utcnow() + timedelta(days=days_left, hours=1)

    user = MagicMock()
    user.subscription = subscription
    return user


def test_get_subscription_status_marks_trial_as_trial():
    texts = DummyTexts()
    user = _build_user_with_subscription(actual_status="active", is_trial=True, days_left=5)

    status_text = _get_subscription_status(user, texts)

    assert "Тестовая подписка" in status_text
    assert "Активна" not in status_text
