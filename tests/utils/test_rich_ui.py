"""Общие rich-блоки для пользовательских экранов."""

from __future__ import annotations

from unittest.mock import patch

from app.utils import rich_ui


def test_progress_bar_bounds():
    assert rich_ui.progress_bar(0, 10) == '░' * 10
    assert rich_ui.progress_bar(100, 10) == '▓' * 10
    assert rich_ui.progress_bar(-50, 4) == '░░░░'
    assert rich_ui.progress_bar(500, 4) == '▓▓▓▓'
    assert rich_ui.progress_bar(50, 10) == '▓▓▓▓▓░░░░░'


def test_kv_table_escapes_labels_only():
    table = rich_ui.kv_table([('Трафик', '<b>10 / 100 ГБ</b>'), ('A & B', 'x')])

    assert '<table bordered striped>' in table
    assert '<td>Трафик</td>' in table
    assert '<td align="right"><b>10 / 100 ГБ</b></td>' in table
    assert '<td>A &amp; B</td>' in table


def test_details_open_flag():
    assert rich_ui.details('Итог', '<p>x</p>') == '<details><summary>Итог</summary><p>x</p></details>'
    assert rich_ui.details('Итог', '<p>x</p>', open_by_default=True).startswith('<details open>')


def test_logo_and_footer():
    with patch('app.utils.rich_ui.get_logo_url', return_value='https://example.com/logo.png'):
        assert rich_ui.rich_logo_html() == '<img src="https://example.com/logo.png"/>'
    with patch('app.utils.rich_ui.get_logo_url', return_value=''):
        assert rich_ui.rich_logo_html() == ''

    footer = rich_ui.rich_footer('@test')
    assert footer.startswith('<footer>@test · <tg-time unix="')
    assert footer.endswith('</tg-time></footer>')
