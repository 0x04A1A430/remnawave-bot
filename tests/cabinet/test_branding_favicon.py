"""GET /cabinet/branding/favicon — иконка вкладки, которую понимает Safari.

Safari берёт фавикон только при первой загрузке страницы и игнорирует смену
через JS, поэтому кабинет ссылается на этот адрес прямо из index.html.
Эндпоинт обязан ответить картинкой всегда: логотип из админки, а без него —
монограмма первой буквы имени, та же, что рисует кабинет.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.responses import FileResponse

from app.cabinet.routes import branding as branding_routes
from app.cabinet.utils.brand_monogram import monogram_svg
from app.config import settings


def _name(value: str | None) -> AsyncMock:
    return AsyncMock(return_value=value)


async def test_without_logo_returns_monogram_of_the_first_letter(monkeypatch) -> None:
    monkeypatch.setattr(branding_routes, 'get_logo_path', lambda: None)

    with patch('app.cabinet.routes.branding.get_setting_value', _name('zeroping')):
        response = await branding_routes.get_favicon(db=AsyncMock())

    assert response.media_type == 'image/svg+xml'
    assert response.body.decode() == monogram_svg('Z')
    assert response.headers['cache-control'] == 'public, max-age=300'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['vary'] == 'Origin'


async def test_empty_name_falls_back_to_v(monkeypatch) -> None:
    monkeypatch.setattr(branding_routes, 'get_logo_path', lambda: None)

    with patch('app.cabinet.routes.branding.get_setting_value', _name('')):
        response = await branding_routes.get_favicon(db=AsyncMock())

    assert response.body.decode() == monogram_svg('V')


async def test_unset_name_uses_build_default(monkeypatch) -> None:
    monkeypatch.setattr(branding_routes, 'get_logo_path', lambda: None)
    # У Settings нет поля CABINET_BRANDING_NAME — маршрут читает его через getattr с None.
    assert getattr(settings, 'CABINET_BRANDING_NAME', None) is None
    monkeypatch.delenv('VITE_APP_NAME', raising=False)

    with patch('app.cabinet.routes.branding.get_setting_value', _name(None)):
        response = await branding_routes.get_favicon(db=AsyncMock())

    assert response.body.decode() == monogram_svg('C')  # «Cabinet»


async def test_with_logo_serves_the_logo_with_short_cache(tmp_path: Path, monkeypatch) -> None:
    logo = tmp_path / 'logo.png'
    logo.write_bytes(b'\x89PNG\r\n\x1a\n')
    monkeypatch.setattr(branding_routes, 'get_logo_path', lambda: logo)

    response = await branding_routes.get_favicon(db=AsyncMock())

    assert isinstance(response, FileResponse)
    assert Path(response.path) == logo
    assert response.media_type == 'image/png'
    assert response.headers['cache-control'] == 'public, max-age=300'


async def test_logo_endpoint_keeps_its_hour_cache(tmp_path: Path, monkeypatch) -> None:
    logo = tmp_path / 'logo.svg'
    logo.write_text('<svg/>')
    monkeypatch.setattr(branding_routes, 'get_logo_path', lambda: logo)

    response = await branding_routes.get_logo()

    assert response.media_type == 'image/svg+xml'
    assert response.headers['cache-control'] == 'public, max-age=3600'
    assert 'sandbox' in response.headers['content-security-policy']
    # Логотип грузится и как <img>/фавикон (без Origin), и fetch()-ом (с Origin):
    # без Vary кеш отдал бы fetch()-у ответ без CORS-заголовков.
    assert response.headers['vary'] == 'Origin'


def test_monogram_escapes_and_uppercases() -> None:
    assert '>Z</text>' in monogram_svg('zeroping')
    assert '>Я</text>' in monogram_svg('я')
    assert '>&amp;</text>' in monogram_svg('&')
    assert '>V</text>' in monogram_svg('   ')
    assert monogram_svg('Z').startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">')
