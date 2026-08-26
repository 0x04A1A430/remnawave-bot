"""Пользовательские уведомления rich-сообщением.

Главная опасность перевода — переносы строк. Классический Telegram-HTML рвёт
текст по '\\n', а rich-разметка блочная: спецификация про свой набор тегов прямо
отмечает «all the text above was on the same line». Отданный как есть текст
уведомления слипся бы в сплошное полотно, и это было бы видно каждому получателю.
"""

from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import ClientDecodeError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.services.notification_delivery_service import NotificationType
from app.utils import rich_notify
from app.utils.rich_notify import build_notification_rich_html, try_send_rich_notification


@pytest.fixture(autouse=True)
def _enable_rich(monkeypatch):
    monkeypatch.setattr(settings, 'USER_NOTIFICATIONS_RICH_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_INLINE_BUTTONS', False, raising=False)
    monkeypatch.setattr(rich_notify, 'is_rich_menu_enabled', lambda: True)


class TestBuildHtml:
    def test_single_newline_becomes_br(self):
        assert build_notification_rich_html('первая\nвторая') == '<p>первая<br>вторая</p>'

    def test_blank_line_starts_new_paragraph(self):
        html = build_notification_rich_html('заголовок\n\nтело')

        assert html == '<p>заголовок</p><p>тело</p>'

    def test_several_blank_lines_do_not_make_empty_paragraphs(self):
        html = build_notification_rich_html('а\n\n\n\nб')

        assert html == '<p>а</p><p>б</p>'
        assert '<p></p>' not in html

    def test_inline_markup_survives(self):
        html = build_notification_rich_html('<b>Подписка</b> истекает <a href="https://t.me">тут</a>')

        assert '<b>Подписка</b>' in html
        assert '<a href="https://t.me">тут</a>' in html

    def test_classic_spoiler_span_becomes_rich_tag(self):
        html = build_notification_rich_html('<span class="tg-spoiler">секрет</span>')

        assert '<tg-spoiler>секрет</tg-spoiler>' in html
        assert 'span' not in html

    @pytest.mark.parametrize(
        'text',
        [
            '<pre>код</pre>',
            '<blockquote>цитата</blockquote>',
            '<ul><li>пункт</li></ul>',
        ],
    )
    def test_block_markup_refuses_conversion(self, text):
        """Блочную разметку rich воспроизводит иначе — честнее отдать классику."""
        assert build_notification_rich_html(text) is None

    @pytest.mark.parametrize('text', ['', '   ', '\n\n'])
    def test_empty_text_refuses_conversion(self, text):
        assert build_notification_rich_html(text) is None


class TestSend:
    async def test_sends_rich_and_reports_success(self):
        bot = AsyncMock()

        sent = await try_send_rich_notification(bot, 42, 'Подписка истекает\n\nПродлите её')

        assert sent is True
        html = bot.send_rich_message.await_args.kwargs['rich_message'].html
        assert html == '<p>Подписка истекает</p><p>Продлите её</p>'

    async def test_disabled_setting_falls_back(self, monkeypatch):
        monkeypatch.setattr(settings, 'USER_NOTIFICATIONS_RICH_ENABLED', False, raising=False)
        bot = AsyncMock()

        assert await try_send_rich_notification(bot, 42, 'текст') is False
        bot.send_rich_message.assert_not_awaited()

    async def test_rich_menu_off_falls_back(self, monkeypatch):
        """Вне rich-режима сервер может не знать про rich вовсе."""
        monkeypatch.setattr(rich_notify, 'is_rich_menu_enabled', lambda: False)
        bot = AsyncMock()

        assert await try_send_rich_notification(bot, 42, 'текст') is False
        bot.send_rich_message.assert_not_awaited()

    async def test_keyboard_stays_outside_by_default(self):
        bot = AsyncMock()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='a', callback_data='a')]])

        await try_send_rich_notification(bot, 42, 'текст', keyboard=keyboard)

        kwargs = bot.send_rich_message.await_args.kwargs
        assert kwargs['reply_markup'] is keyboard
        assert '<tg-button' not in kwargs['rich_message'].html

    async def test_buttons_move_inside_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, 'MAIN_MENU_RICH_INLINE_BUTTONS', True, raising=False)
        bot = AsyncMock()
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text='Продлить', callback_data='subscription_extend')]]
        )

        await try_send_rich_notification(bot, 42, 'текст', keyboard=keyboard)

        kwargs = bot.send_rich_message.await_args.kwargs
        assert '<tg-button type="callback_data" data="subscription_extend">' in kwargs['rich_message'].html
        assert 'reply_markup' not in kwargs

    async def test_unmovable_button_keeps_keyboard_outside(self, monkeypatch):
        monkeypatch.setattr(settings, 'MAIN_MENU_RICH_INLINE_BUTTONS', True, raising=False)
        bot = AsyncMock()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Оплатить', pay=True)]])

        await try_send_rich_notification(bot, 42, 'текст', keyboard=keyboard)

        kwargs = bot.send_rich_message.await_args.kwargs
        assert kwargs['reply_markup'] is keyboard
        assert '<tg-button' not in kwargs['rich_message'].html

    @pytest.mark.parametrize(
        'error',
        [
            TelegramBadRequest(method=None, message='RICH_MESSAGE_INVALID'),
            ClientDecodeError('Failed to decode object', ValueError('unknown block'), '{}'),
        ],
    )
    async def test_send_failure_falls_back_to_classic(self, error):
        """Любой отказ обязан отдать False, чтобы отработал классический путь."""
        bot = AsyncMock()
        bot.send_rich_message.side_effect = error

        assert await try_send_rich_notification(bot, 42, 'текст') is False

    async def test_block_markup_falls_back_without_calling_api(self):
        bot = AsyncMock()

        assert await try_send_rich_notification(bot, 42, '<pre>код</pre>') is False
        bot.send_rich_message.assert_not_awaited()


class TestDeliveryIntegration:
    """Единый сток доставки обязан пробовать rich и честно откатываться на классику."""

    @staticmethod
    def _service_and_user():
        from types import SimpleNamespace

        from app.services.notification_delivery_service import NotificationDeliveryService

        service = NotificationDeliveryService.__new__(NotificationDeliveryService)
        return service, SimpleNamespace(telegram_id=42, id=1, language='ru')

    async def test_rich_success_skips_classic_send(self, monkeypatch):
        service, user = self._service_and_user()
        monkeypatch.setattr('app.utils.rich_notify.try_send_rich_notification', AsyncMock(return_value=True))
        bot = AsyncMock()

        ok = await service._send_telegram_notification(
            user, NotificationType.SUBSCRIPTION_EXPIRING, {}, bot, 'текст', None
        )

        assert ok is True
        bot.send_message.assert_not_awaited()

    async def test_rich_refusal_falls_through_to_classic_send(self, monkeypatch):
        service, user = self._service_and_user()
        monkeypatch.setattr('app.utils.rich_notify.try_send_rich_notification', AsyncMock(return_value=False))
        bot = AsyncMock()

        ok = await service._send_telegram_notification(
            user, NotificationType.SUBSCRIPTION_EXPIRING, {}, bot, 'текст', None
        )

        assert ok is True
        bot.send_message.assert_awaited_once()
        assert bot.send_message.await_args.kwargs['parse_mode'] == 'HTML'
