"""Плитка фавикона из логотипа: квадрат со скруглёнными углами, как в шапке кабинета.

Safari берёт иконку вкладки только по статической ссылке из index.html и смену
через JS не замечает, а скруглённую плитку из логотипа кабинет рисует на canvas
уже после загрузки — Safari оставался с сырым квадратом. Скругляем здесь, в той
же пропорции, что и плитка логотипа в шапке (LOGO_TILE_RADIUS кабинета).
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps


FAVICON_TILE_SIZE = 256
# Доля стороны под радиус скругления — как у плитки логотипа в шапке кабинета.
FAVICON_CORNER_RATIO = 0.3
# Что Pillow умеет растеризовать; SVG отдаётся файлом как есть.
_RASTER_SUFFIXES = frozenset({'.png', '.jpg', '.jpeg', '.webp', '.gif'})


def is_raster_logo(logo_path: Path) -> bool:
    return logo_path.suffix.lower() in _RASTER_SUFFIXES


def rounded_logo_tile(
    logo_path: Path,
    size: int = FAVICON_TILE_SIZE,
    corner_ratio: float = FAVICON_CORNER_RATIO,
) -> bytes:
    """PNG ``size``×``size``: логотип вписан по принципу object-fit: cover, углы прозрачные."""
    with Image.open(logo_path) as source:
        tile = ImageOps.fit(source.convert('RGBA'), (size, size), Image.Resampling.LANCZOS)
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1),
        radius=round(size * corner_ratio),
        fill=255,
    )
    tile.putalpha(ImageChops.multiply(tile.getchannel('A'), mask))
    buffer = BytesIO()
    tile.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()


@lru_cache(maxsize=4)
def _render_cached(logo_path: str, mtime_ns: int, file_size: int) -> bytes:
    # mtime и размер в ключе: новый логотип из админки — новый файл, старая плитка забывается.
    return rounded_logo_tile(Path(logo_path))


def cached_rounded_logo_tile(logo_path: Path) -> bytes:
    """Плитка по файлу логотипа; перерисовывается только когда файл заменили."""
    stat = logo_path.stat()
    return _render_cached(str(logo_path), stat.st_mtime_ns, stat.st_size)
