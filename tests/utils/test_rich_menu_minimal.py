"""Rich-главное меню минималистично: рендерит тот же текст, что классическое меню.

Раньше rich собирал собственную «таблицу подписок», прогресс-бары и inline-ссылки,
расходясь с классикой; теперь build_main_menu_rich_html — обёртка над
get_main_menu_text (единый источник правды для контента главного меню).
"""

import pytest


@pytest.mark.asyncio
async def test_rich_menu_mirrors_classic_menu_text(monkeypatch):
    from app.handlers import menu as menu_mod
    from app.utils import rich_menu

    classic_html = (
        '<blockquote><tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> <b>Ivan</b>\n'
        '<b>🪪 Подписка:</b> Активна (навсегда)</blockquote>'
    )

    async def fake_classic_text(user, texts, db):
        return classic_html

    monkeypatch.setattr(menu_mod, 'get_main_menu_text', fake_classic_text)
    monkeypatch.setattr(rich_menu, '_resolve_rich_logo_url', lambda: '')

    html_out = await rich_menu.build_main_menu_rich_html(user=object(), texts=object(), db=object())

    # Контент классики переносится как есть
    assert 'Активна (навсегда)' in html_out
    assert '<blockquote>' in html_out
    assert '<b>Ivan</b>' in html_out
    assert '<br/>' in html_out

    # Премиум tg-эмодзи из шаблона MAIN_MENU не теряются при конвертации
    assert 'tg-emoji emoji-id="5886412370347036129"' in html_out

    # Никаких собственных украшательств поверх классического контента
    assert '<table' not in html_out
    assert '<details' not in html_out
    assert '<footer>' not in html_out
