from app.config import settings
from app.keyboards import inline
from app.services.support_settings_service import SupportSettingsService


def _support_button(markup):
    return next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data == 'menu_support' or button.url
    )


def test_main_menu_support_opens_ticket_menu_when_tickets_enabled(monkeypatch):
    monkeypatch.setattr(SupportSettingsService, 'is_support_menu_enabled', classmethod(lambda cls: True))
    monkeypatch.setattr(SupportSettingsService, 'is_tickets_enabled', classmethod(lambda cls: True))
    monkeypatch.setattr(settings, 'SUPPORT_USERNAME', 'https://t.me/old_support_channel')
    monkeypatch.setattr(settings, 'SUPPORT_MENU_ENABLED', True)
    monkeypatch.setattr(type(settings), 'is_cabinet_mode', lambda self: False)

    markup = inline.get_main_menu_keyboard(language='ru')
    button = _support_button(markup)

    assert button.callback_data == 'menu_support'
    assert button.url is None


def test_main_menu_support_keeps_url_when_tickets_disabled(monkeypatch):
    monkeypatch.setattr(SupportSettingsService, 'is_support_menu_enabled', classmethod(lambda cls: True))
    monkeypatch.setattr(SupportSettingsService, 'is_tickets_enabled', classmethod(lambda cls: False))
    monkeypatch.setattr(settings, 'SUPPORT_USERNAME', 'https://t.me/contact_support')
    monkeypatch.setattr(settings, 'SUPPORT_MENU_ENABLED', True)
    monkeypatch.setattr(type(settings), 'is_cabinet_mode', lambda self: False)

    markup = inline.get_main_menu_keyboard(language='ru')
    button = _support_button(markup)

    assert button.callback_data is None
    assert button.url == 'https://t.me/contact_support'


def test_support_ticket_keyboard_has_styled_actions(monkeypatch):
    monkeypatch.setattr(SupportSettingsService, 'is_tickets_enabled', classmethod(lambda cls: True))
    monkeypatch.setattr(SupportSettingsService, 'is_contact_enabled', classmethod(lambda cls: False))

    keyboard = inline.get_support_keyboard('ru')
    buttons = [row[0] for row in keyboard.inline_keyboard]

    assert buttons[0].callback_data == 'create_ticket'
    assert buttons[0].style == 'success'
    assert buttons[1].callback_data == 'my_tickets'
    assert buttons[1].style == 'primary'
    assert buttons[-1].style == 'danger'
