"""Reusable rich-message blocks for user-facing screens.

Bot API 10.1+ rich messages support headings, tables, details, progress, dates
and footers. Keep the building blocks here so the main menu, subscription report
and future screens share one visual language.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime

from app.utils.rich_transport import get_logo_url


def progress_bar(percent: float, length: int = 10, *, filled: str = '▓', empty: str = '░') -> str:
    """Text progress bar clamped to 0..100%."""
    try:
        value = float(percent)
    except (TypeError, ValueError):
        value = 0.0
    value = max(0.0, min(100.0, value))
    cells = max(1, int(length))
    filled_cells = int(round(cells * value / 100))
    return f'{filled * filled_cells}{empty * (cells - filled_cells)}'


def kv_table(rows: list[tuple[str, str]], *, bordered: bool = True, striped: bool = True) -> str:
    """Two-column table «label → value». Labels are escaped, values are raw HTML."""
    attrs = ' '.join(attr for attr, enabled in (('bordered', bordered), ('striped', striped)) if enabled)
    body = ''.join(f'<tr><td>{html.escape(str(key))}</td><td align="right">{value}</td></tr>' for key, value in rows)
    return f'<table {attrs}>{body}</table>' if attrs else f'<table>{body}</table>'


def details(summary: str, body: str, *, open_by_default: bool = False) -> str:
    """Collapsible block: <details><summary>…</summary>body</details>."""
    open_attr = ' open' if open_by_default else ''
    return f'<details{open_attr}><summary>{html.escape(summary)}</summary>{body}</details>'


def heading(text: str, level: int = 3) -> str:
    tag = f'h{max(1, min(6, int(level)))}'
    return f'<{tag}>{text}</{tag}>'


def rich_logo_html() -> str:
    """Logo image block for hand-built rich messages ('' when no public URL)."""
    url = get_logo_url()
    return f'<img src="{html.escape(url, quote=True)}"/>' if url else ''


def rich_footer(label: str = '@x1lagent') -> str:
    """Footer with the bot label and a client-timezone date."""
    now = datetime.now(UTC)
    stamp = f'<tg-time unix="{int(now.timestamp())}" format="dt">{now.strftime("%d.%m.%Y %H:%M")} UTC</tg-time>'
    return f'<footer>{html.escape(label)} · {stamp}</footer>'
