"""Автоплатёж: дефолт за 1 день и наглядный выбор в карточке подписки."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.handlers.subscription import autopay
from app.keyboards.inline import get_autopay_days_keyboard


def test_default_autopay_days_is_one() -> None:
    assert settings.DEFAULT_AUTOPAY_DAYS_BEFORE == 1


def test_current_autopay_days_is_marked() -> None:
    keyboard = get_autopay_days_keyboard('ru', current_days=3)
    buttons = {button.callback_data: button for row in keyboard.inline_keyboard for button in row}
    assert buttons['autopay_days_3'].style == 'success'
    assert buttons['autopay_days_1'].style is None


@pytest.mark.asyncio
async def test_autopay_days_screen_marks_subscription_setting(monkeypatch) -> None:
    subscription = SimpleNamespace(autopay_days_before=1)
    monkeypatch.setattr(
        autopay,
        '_resolve_subscription',
        AsyncMock(return_value=(subscription, 10)),
    )

    callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
    await autopay.show_autopay_days(callback, SimpleNamespace(language='ru'), AsyncMock(), None)

    keyboard = callback.message.edit_text.await_args.kwargs['reply_markup']
    buttons = {button.callback_data: button for row in keyboard.inline_keyboard for button in row}
    assert buttons['autopay_days_1'].style == 'success'
