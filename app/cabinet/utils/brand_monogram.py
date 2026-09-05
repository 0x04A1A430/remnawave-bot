"""Монограмма бренда для фавикона кабинета: квадрат с первой буквой.

Тот же SVG, что рисует кабинет у себя (vite-plugins/brandMonogram.ts): цвета,
скругление, шрифт. Бот отдаёт её по /cabinet/branding/favicon, когда логотип из
админки не загружен, — так у <link rel="icon"> кабинета всегда есть ответ.
"""

from __future__ import annotations


MONOGRAM_BACKGROUND = '#0a0f1a'
MONOGRAM_FOREGROUND = '#ffffff'
DEFAULT_MONOGRAM_LETTER = 'V'


def monogram_letter(letter: str | None, fallback: str = DEFAULT_MONOGRAM_LETTER) -> str:
    """Первая буква строки заглавной; при пустой строке — ``fallback``."""
    ch = (letter or '').strip()[:1].upper()
    return ch or fallback


def _escape_xml(value: str) -> str:
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def monogram_svg(letter: str | None) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="14" fill="{MONOGRAM_BACKGROUND}"/>'
        '<text x="50%" y="50%" font-family="Manrope,Arial,sans-serif" font-size="38" '
        f'font-weight="700" fill="{MONOGRAM_FOREGROUND}" text-anchor="middle" '
        f'dominant-baseline="central">{_escape_xml(monogram_letter(letter))}</text>'
        '</svg>'
    )
