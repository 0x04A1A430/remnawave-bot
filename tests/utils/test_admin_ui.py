"""Политика визуального слоя админки.

Закрепляем правила:
- пагинация и «Назад» — без premium-иконок и без цвета;
- ``danger`` в админке остаётся только у забаненных пользователей;
- иконки глубоких меню (save/delete/edit/refresh/search/chart/backup/chat …)
  сохраняются;
- пользовательские кнопки не трогаются.
"""

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


def _decorated(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return decorate_admin_markup(InlineKeyboardMarkup(inline_keyboard=list(rows)))


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


def test_admin_button_keeps_callback_and_explicit_icon_and_style():
    button = admin_button('Пользователи', callback_data='admin_users', icon='users', style='primary')

    assert button.callback_data == 'admin_users'
    assert button.icon_custom_emoji_id == ADMIN_ICONS['users'].emoji_id
    assert button.style == 'primary'


def test_admin_button_does_not_override_icon_from_text():
    label = f'<tg-emoji emoji-id="{ADMIN_ICONS["star"].emoji_id}">⭐</tg-emoji> Раздел'
    button = admin_button(label, callback_data='section', icon='users')

    # make_button разбирает <tg-emoji> и не подменяет его явной иконкой хелпера.
    assert button.icon_custom_emoji_id == ADMIN_ICONS['star'].emoji_id


def test_admin_back_button_keeps_arrow_and_has_no_icon_or_style():
    button = admin_back_button(_Texts, 'admin_panel')

    assert button.text == '← Назад'
    assert button.callback_data == 'admin_panel'
    assert button.style is None
    assert button.icon_custom_emoji_id is None


def test_admin_back_button_keeps_custom_label_intact():
    button = admin_back_button(_Texts, 'admin_migration_cancel', label='← Отмена')

    assert button.text == '← Отмена'
    assert button.style is None
    assert button.icon_custom_emoji_id is None


def test_decorate_admin_markup_adds_icons_and_semantic_colors():
    decorated = _decorated(
        [
            InlineKeyboardButton(text=' Настройки', callback_data='admin_submenu_settings', style='primary'),
            InlineKeyboardButton(text='Обновления', callback_data='admin_updates', style='success'),
        ]
    )
    settings, updates = decorated.inline_keyboard[0]

    assert settings.text == 'Настройки'
    assert settings.icon_custom_emoji_id == ADMIN_ICONS['settings'].emoji_id
    assert settings.style == 'primary'
    assert updates.icon_custom_emoji_id == ADMIN_ICONS['updates'].emoji_id
    assert updates.style == 'primary'


def test_decorate_admin_markup_keeps_unknown_buttons_untouched():
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Пользователь', callback_data='user_1')]])

    degraded = decorate_admin_markup(markup)

    assert degraded is markup
    assert degraded.inline_keyboard[0][0].icon_custom_emoji_id is None


def test_decorate_admin_markup_strips_legacy_glyph():
    decorated = _decorated([InlineKeyboardButton(text='▤ Список', callback_data='admin_users')])
    button = decorated.inline_keyboard[0][0]

    assert button.text == 'Список'
    assert button.icon_custom_emoji_id == ADMIN_ICONS['users'].emoji_id


def test_decorate_admin_markup_back_has_no_icon_and_no_color():
    decorated = _decorated(
        [
            InlineKeyboardButton(
                text='← Назад',
                callback_data='admin_users',
                style='danger',
                icon_custom_emoji_id=ADMIN_ICONS['back'].emoji_id,
            ),
            InlineKeyboardButton(
                text='← Назад',
                callback_data='admin_tariff_edit_back',
                style='primary',
                icon_custom_emoji_id=ADMIN_ICONS['arrow_left'].emoji_id,
            ),
            InlineKeyboardButton(text='← Отмена', callback_data='admin_migration_cancel', style='danger'),
        ]
    )
    back_icon, back_arrow, cancel = decorated.inline_keyboard[0]

    for button in (back_icon, back_arrow, cancel):
        assert button.text.startswith('←')
        assert button.icon_custom_emoji_id is None
        assert button.style is None


def test_decorate_admin_markup_keeps_deep_menu_icons_and_semantic_colors():
    decorated = _decorated(
        [
            InlineKeyboardButton(text='Сохранить', callback_data='admin_server_promo_save_1', style='success'),
            InlineKeyboardButton(text='Удалить', callback_data='admin_tariff_delete:1', style='danger'),
            InlineKeyboardButton(text='Закрытые', callback_data='admin_tickets_scope_closed', style='primary'),
            InlineKeyboardButton(text='Бекапы', callback_data='backup_panel', style='primary'),
        ]
    )
    save, delete, closed, backups = decorated.inline_keyboard[0]

    assert save.icon_custom_emoji_id == ADMIN_ICONS['save'].emoji_id
    assert delete.icon_custom_emoji_id == ADMIN_ICONS['delete'].emoji_id
    assert closed.icon_custom_emoji_id == ADMIN_ICONS['chat'].emoji_id
    assert backups.icon_custom_emoji_id == ADMIN_ICONS['backup'].emoji_id
    assert save.style == 'success'
    assert delete.style == 'danger'
    assert closed.style == 'primary'
    assert backups.style == 'primary'


def test_decorate_admin_markup_deep_action_icons_by_text():
    decorated = _decorated(
        [
            InlineKeyboardButton(text='Создать', callback_data='admin_promo_create'),
            InlineKeyboardButton(text='Редактировать', callback_data='admin_tariff_edit_name:7'),
            InlineKeyboardButton(text='Обновить', callback_data='admin_blocked_scan'),
            InlineKeyboardButton(text='Поиск', callback_data='admin_user_search'),
            InlineKeyboardButton(text='Фильтр', callback_data='admin_mon_logs_filters'),
            InlineKeyboardButton(text='Статистика', callback_data='admin_stats_summary'),
        ]
    )

    expected = [
        ('plus', 'success'),
        ('edit', 'primary'),
        ('refresh', 'primary'),
        ('search', 'primary'),
        ('filter', 'primary'),
        ('chart', 'primary'),
    ]
    for button, (icon_name, style) in zip(decorated.inline_keyboard[0], expected, strict=True):
        assert button.icon_custom_emoji_id == ADMIN_ICONS[icon_name].emoji_id
        assert button.style == style


def test_decorate_admin_markup_keeps_explicit_deep_icon():
    decorated = _decorated(
        [
            InlineKeyboardButton(
                text='Сохранить',
                callback_data='admin_bot_config_save',
                icon_custom_emoji_id=ADMIN_ICONS['save'].emoji_id,
                style='success',
            )
        ]
    )
    button = decorated.inline_keyboard[0][0]

    assert button.icon_custom_emoji_id == ADMIN_ICONS['save'].emoji_id
    assert button.style == 'success'


def test_decorate_admin_markup_gives_unknown_admin_button_default_icon():
    decorated = _decorated([InlineKeyboardButton(text='Неизвестно', callback_data='admin_unknown_action')])
    button = decorated.inline_keyboard[0][0]

    assert button.style == 'primary'
    assert button.icon_custom_emoji_id == ADMIN_ICONS['settings'].emoji_id


def test_decorate_admin_markup_keeps_user_status_colors():
    decorated = _decorated(
        [
            InlineKeyboardButton(text='Иван', callback_data='admin_user_manage_1'),
            InlineKeyboardButton(text='Мария $', callback_data='admin_user_manage_3', style='success'),
            InlineKeyboardButton(text='× Пётр', callback_data='admin_user_manage_2', style='primary'),
        ]
    )
    active, subscribed, blocked = decorated.inline_keyboard[0]

    assert active.style is None
    assert active.icon_custom_emoji_id == ADMIN_ICONS['user'].emoji_id
    assert subscribed.style == 'success'
    assert subscribed.icon_custom_emoji_id == ADMIN_ICONS['user'].emoji_id
    assert blocked.style == 'danger'
    # «×» в тексте уже маркирует бан — лишнюю иконку не добавляем.
    assert blocked.icon_custom_emoji_id is None


def test_decorate_admin_markup_pagination_has_no_icon_or_color():
    decorated = _decorated(
        [
            InlineKeyboardButton(
                text='←',
                callback_data='admin_users_list_page_1',
                style='primary',
                icon_custom_emoji_id=ADMIN_ICONS['arrow_left'].emoji_id,
            ),
            InlineKeyboardButton(
                text='1/3',
                callback_data='admin_payments_page_current',
                style='primary',
                icon_custom_emoji_id=ADMIN_ICONS['document'].emoji_id,
            ),
            InlineKeyboardButton(
                text='→',
                callback_data='admin_mon_logs_page_3',
                style='primary',
            ),
            InlineKeyboardButton(text='2', callback_data='admin_tickets_page_scope_2', style='success'),
            InlineKeyboardButton(text='Стр. 3/5', callback_data='admin_migration_page_info', style='primary'),
        ]
    )

    for button in decorated.inline_keyboard[0]:
        assert button.icon_custom_emoji_id is None
        assert button.style == 'primary'


def test_decorate_admin_markup_clears_shared_pagination_callbacks():
    decorated = _decorated(
        [
            InlineKeyboardButton(text='1/2', callback_data='current_page', style='primary'),
            InlineKeyboardButton(
                text='2/2',
                callback_data='noop',
                style='primary',
                icon_custom_emoji_id=ADMIN_ICONS['document'].emoji_id,
            ),
        ]
    )

    for button in decorated.inline_keyboard[0]:
        assert button.icon_custom_emoji_id is None
        assert button.style == 'primary'


def test_decorate_admin_markup_leaves_user_buttons_untouched():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text='← Назад',
                    callback_data='back_to_menu',
                    style='danger',
                    icon_custom_emoji_id=ADMIN_ICONS['back'].emoji_id,
                ),
                InlineKeyboardButton(text='← К списку', callback_data='referral_list_page_5', style='danger'),
                InlineKeyboardButton(
                    text='←',
                    callback_data='devices_page_2',
                    style='primary',
                    icon_custom_emoji_id=ADMIN_ICONS['arrow_left'].emoji_id,
                ),
                InlineKeyboardButton(
                    text='30 дн.',
                    callback_data='noop',
                    style='success',
                    icon_custom_emoji_id=ADMIN_ICONS['calendar'].emoji_id,
                ),
            ]
        ]
    )

    assert decorate_admin_markup(markup) is markup


def test_decorate_admin_markup_does_not_treat_backup_as_back():
    decorated = _decorated(
        [
            InlineKeyboardButton(text='Backups', callback_data='backup_panel', style='primary'),
        ]
    )
    button = decorated.inline_keyboard[0][0]

    assert button.icon_custom_emoji_id == ADMIN_ICONS['backup'].emoji_id
    assert button.style == 'primary'
