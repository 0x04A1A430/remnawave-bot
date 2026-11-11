"""Rich-главное меню: тот же текст, что у классического, плюс оформление.

get_main_menu_text остаётся единственным источником правды для контента
(его текст переносится как есть). Rich-слой добавляет только оформление:
логотип в шапке, компактную сводку баланса/трафика/устройств в одиночном
тарифном режиме и footer.
"""

from types import SimpleNamespace

import pytest

from app.utils import rich_ui


class FakeUser(SimpleNamespace):
    """Минимальный пользователь + подписка для сводки."""


def _classic_html() -> str:
    return (
        '<blockquote><tg-emoji emoji-id="5886412370347036129">👤</tg-emoji> <b>Ivan</b>\n'
        '<b>🪪 Подписка:</b> Активна (навсегда)</blockquote>'
    )


def _install_classic_text(monkeypatch) -> None:
    from app.handlers import menu as menu_mod

    async def fake_classic_text(user, texts, db):
        return _classic_html()

    monkeypatch.setattr(menu_mod, 'get_main_menu_text', fake_classic_text)


def _set_multi_tariff(monkeypatch, enabled: bool) -> None:
    """is_multi_tariff_enabled() = MULTI_TARIFF_ENABLED and SALES_MODE == 'tariffs'."""
    from app.utils import rich_menu

    monkeypatch.setattr(rich_menu.settings, 'MULTI_TARIFF_ENABLED', enabled)
    monkeypatch.setattr(rich_menu.settings, 'SALES_MODE', 'tariffs' if enabled else 'classic')


def _fake_user(*, subscription) -> FakeUser:
    return FakeUser(balance_kopeks=15000, subscription=subscription, language='ru')


def _subscription(*, traffic_limit_gb: int, traffic_used_gb: float = 0.0, device_limit: int = 3):
    return SimpleNamespace(
        traffic_limit_gb=traffic_limit_gb,
        traffic_used_gb=traffic_used_gb,
        device_limit=device_limit,
    )


@pytest.mark.asyncio
async def test_rich_menu_has_logo_stats_progress_and_footer(monkeypatch):
    from app.utils import rich_menu

    _install_classic_text(monkeypatch)
    monkeypatch.setattr(rich_ui, 'get_logo_url', lambda: 'https://example.com/logo.png')
    _set_multi_tariff(monkeypatch, False)

    user = _fake_user(
        subscription=_subscription(traffic_limit_gb=100, traffic_used_gb=25.0, device_limit=3),
    )
    html_out = await rich_menu.build_main_menu_rich_html(user=user, texts=object(), db=object())

    # Логотип приходит из rich_ui.rich_logo_html
    assert '<img src="https://example.com/logo.png"/>' in html_out

    # Сводка баланса/трафика/устройств
    assert '<table bordered striped>' in html_out
    assert '<td>💰 Баланс</td>' in html_out
    assert '<td>📊 Трафик</td>' in html_out
    assert '25.0 / 100 ГБ' in html_out
    assert '<td>📱 Устройства</td>' in html_out

    # Прогресс-бар и процент (25 / 100)
    assert '<p>▓▓░░░░░░░░ 25%</p>' in html_out

    # Footer в конце
    assert '<footer>' in html_out
    assert html_out.endswith('</footer>')

    # Контент классики переносится как есть
    assert 'Активна (навсегда)' in html_out
    assert '<blockquote>' in html_out
    assert '<b>Ivan</b>' in html_out
    assert '<br/>' in html_out

    # Премиум tg-эмодзи из шаблона MAIN_MENU не теряются при конвертации
    assert 'tg-emoji emoji-id="5886412370347036129"' in html_out


@pytest.mark.asyncio
async def test_rich_menu_unlimited_traffic_has_no_progress(monkeypatch):
    from app.utils import rich_menu

    _install_classic_text(monkeypatch)
    monkeypatch.setattr(rich_ui, 'get_logo_url', lambda: '')
    _set_multi_tariff(monkeypatch, False)

    user = _fake_user(subscription=_subscription(traffic_limit_gb=0, device_limit=0))
    html_out = await rich_menu.build_main_menu_rich_html(user=user, texts=object(), db=object())

    assert '<td>📊 Трафик</td>' in html_out
    assert '<td align="right">∞</td>' in html_out
    assert '<p>' not in html_out
    assert '░' not in html_out


@pytest.mark.asyncio
async def test_rich_menu_multi_tariff_or_no_subscription_has_no_table(monkeypatch):
    from app.utils import rich_menu

    _install_classic_text(monkeypatch)
    monkeypatch.setattr(rich_ui, 'get_logo_url', lambda: 'https://example.com/logo.png')

    cases = [
        (True, _subscription(traffic_limit_gb=100, traffic_used_gb=50.0)),  # мультитариф
        (False, None),  # одиночный режим без подписки
    ]
    for multi_tariff, subscription in cases:
        _set_multi_tariff(monkeypatch, multi_tariff)
        user = _fake_user(subscription=subscription)

        html_out = await rich_menu.build_main_menu_rich_html(user=user, texts=object(), db=object())

        assert '<table' not in html_out
        assert '░' not in html_out
        # Логотип, классический текст и footer остаются
        assert '<img src="https://example.com/logo.png"/>' in html_out
        assert 'Активна (навсегда)' in html_out
        assert '<footer>' in html_out
