from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils.admin_ui import (
    ADMIN_ICONS,
    admin_back_button,
    admin_button,
    admin_heading,
    admin_quote,
    admin_screen,
    decorate_admin_markup,
)


class _Texts:
    BACK = '← Назад'


def test_admin_screen_converts_legacy_markdown_and_adds_icon():
    result = admin_screen(' **Настройки системы**\n\nОписание', icon='settings', compact=True)

    assert result.startswith(ADMIN_ICONS['settings'].html())
    assert '<b>Настройки системы</b>' in result
    assert '**' not in result
    assert not result.startswith('\n')


def test_admin_screen_does_not_duplicate_existing_icon():
    icon = ADMIN_ICONS['users'].html()
    result = admin_screen(f'{icon} <b>Пользователи</b>', icon='users')

    assert result.count(icon) == 1


def test_admin_screen_strips_zero_width_and_edges():
    result = admin_screen('\ufeff\n  <b>Панель</b>  ', icon='crown')

    assert result == f'{ADMIN_ICONS["crown"].html()} <b>Панель</b>'


def test_heading_and_quote_match_user_style():
    assert admin_heading('crown', 'Админка') == f'{ADMIN_ICONS["crown"].html()} <b>Админка</b>'
    assert admin_quote('Строка') == '<blockquote>Строка</blockquote>'


def test_admin_button_keeps_callback_and_adds_icon_and_style():
    button = admin_button('Пользователи', callback_data='admin_users', icon='users', style='primary')

    assert button.callback_data == 'admin_users'
    assert button.icon_custom_emoji_id == ADMIN_ICONS['users'].emoji_id
    assert button.style == 'primary'


def test_admin_button_does_not_override_icon_from_text():
    label = f'<tg-emoji emoji-id="{ADMIN_ICONS["star"].emoji_id}">⭐</tg-emoji> Раздел'
    button = admin_button(label, callback_data='section', icon='users')

    # make_button разбирает <tg-emoji> и не подменяет его явной иконкой хелпера.
    assert button.icon_custom_emoji_id == ADMIN_ICONS['star'].emoji_id


def test_admin_back_button_is_danger():
    button = admin_back_button(_Texts, 'admin_panel')

    assert button.text == 'Назад'
    assert button.callback_data == 'admin_panel'
    assert button.style == 'danger'
    assert button.icon_custom_emoji_id == ADMIN_ICONS['back'].emoji_id


def test_decorate_admin_markup_adds_icons_styles_and_cleans_labels():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=' Настройки', callback_data='admin_submenu_settings'),
                InlineKeyboardButton(text='Своя кнопка', callback_data='admin_updates', style='success'),
            ]
        ]
    )

    decorated = decorate_admin_markup(markup)
    settings, updates = decorated.inline_keyboard[0]

    assert settings.text == 'Настройки'
    assert settings.icon_custom_emoji_id == ADMIN_ICONS['settings'].emoji_id
    assert settings.style == 'primary'
    assert updates.style == 'success'
    assert updates.icon_custom_emoji_id == ADMIN_ICONS['updates'].emoji_id


def test_decorate_admin_markup_keeps_unknown_buttons_untouched():
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Пользователь', callback_data='user_1')]])

    degraded = decorate_admin_markup(markup)

    assert degraded is markup
    assert degraded.inline_keyboard[0][0].icon_custom_emoji_id is None


def test_decorate_admin_markup_strips_legacy_glyph_and_adds_back_icon():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text='▤ Список', callback_data='admin_users'),
                InlineKeyboardButton(text='Назад', callback_data='admin_users', style='danger'),
            ]
        ]
    )

    decorated = decorate_admin_markup(markup)
    forward, back = decorated.inline_keyboard[0]

    assert forward.text == 'Список'
    assert forward.icon_custom_emoji_id == ADMIN_ICONS['users'].emoji_id
    assert back.style == 'danger'
    assert back.icon_custom_emoji_id == ADMIN_ICONS['back'].emoji_id


def test_decorate_admin_markup_infers_deep_menu_action_icon_and_style():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text='Сохранить', callback_data='admin_server_promo_save_1'),
                InlineKeyboardButton(text='Удалить', callback_data='admin_tariff_delete:1'),
                InlineKeyboardButton(text='Закрытые', callback_data='admin_tickets_scope_closed'),
                InlineKeyboardButton(text='Бекапы', callback_data='backup_panel'),
            ]
        ]
    )

    decorated = decorate_admin_markup(markup)
    save, delete, closed, backups = decorated.inline_keyboard[0]

    assert save.style == 'success' and save.icon_custom_emoji_id == ADMIN_ICONS['save'].emoji_id
    assert delete.style == 'danger' and delete.icon_custom_emoji_id == ADMIN_ICONS['delete'].emoji_id
    assert closed.style == 'primary' and closed.icon_custom_emoji_id == ADMIN_ICONS['chat'].emoji_id
    assert backups.style == 'primary' and backups.icon_custom_emoji_id == ADMIN_ICONS['backup'].emoji_id


def test_decorate_admin_markup_gives_unknown_admin_button_default_icon():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='Неизвестно', callback_data='admin_unknown_action')]]
    )

    decorated = decorate_admin_markup(markup)
    button = decorated.inline_keyboard[0][0]

    assert button.style == 'primary'
    assert button.icon_custom_emoji_id == ADMIN_ICONS['settings'].emoji_id
