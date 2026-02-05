"""Converts GitHub-flavored Markdown to Telegram-compatible HTML.

Telegram supports a limited subset of HTML tags:
<b>, <i>, <u>, <s>, <code>, <pre>, <a href="...">, <blockquote>, <tg-spoiler>.

This module strips everything else and maps common Markdown constructs
to the supported tags.
"""

import re


# HTML tags that Telegram Bot API supports (case-insensitive tag names)
_ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        'b',
        'strong',
        'i',
        'em',
        'u',
        'ins',
        's',
        'strike',
        'del',
        'code',
        'pre',
        'a',
        'blockquote',
        'tg-spoiler',
        'tg-emoji',
    }
)

# Regex to match any HTML tag (opening, closing, or self-closing)
_HTML_TAG_RE: re.Pattern[str] = re.compile(r'<(/?)(\w[\w-]*)((?:\s+[^>]*)?)(/?)>', re.IGNORECASE)


def _strip_unsupported_html(text: str) -> str:
    """Remove HTML tags that Telegram does not support, keeping only allowed ones."""

    def _replace_tag(match: re.Match[str]) -> str:
        tag_name = match.group(2).lower()
        if tag_name in _ALLOWED_TAGS:
            return match.group(0)
        return ''

    return _HTML_TAG_RE.sub(_replace_tag, text)


def _escape_html(text: str) -> str:
    """Escape characters that conflict with Telegram HTML parsing.

    Only escapes `&`, `<`, `>` that are NOT already part of allowed HTML tags.
    We run this BEFORE markdown conversion so markdown symbols are still intact.
    """
    # Escape ampersands that are not already HTML entities
    text = re.sub(r'&(?!amp;|lt;|gt;|quot;|#\d+;)', '&amp;', text)
    return text


def github_markdown_to_telegram_html(text: str) -> str:
    """Convert GitHub-flavored Markdown to Telegram HTML.

    Handles:
    - ``## Header`` -> ``<b>Header</b>``
    - ``**bold**`` / ``__bold__`` -> ``<b>bold</b>``
    - ``*italic*`` / ``_italic_`` -> ``<i>italic</i>``
    - `` `code` `` -> ``<code>code</code>``
    - ``- item`` / ``* item`` -> ``bullet item``
    - ``[text](url)`` -> ``<a href="url">text</a>``
    - Strips unsupported HTML tags
    """
    if not text:
        return ''

    # Escape HTML-sensitive chars first (but preserve existing tags for later stripping)
    # We do a targeted escape: only bare < > that are NOT part of tags
    result = text

    # --- Code blocks (``` ... ```) -- protect from further processing ---
    code_blocks: list[str] = []

    def _save_code_block(match: re.Match[str]) -> str:
        lang = match.group(1) or ''
        code = match.group(2)
        # Escape HTML inside code
        code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        placeholder = f'\x00CODEBLOCK{len(code_blocks)}\x00'
        if lang:
            code_blocks.append(f'<pre><code class="language-{lang}">{code}</code></pre>')
        else:
            code_blocks.append(f'<pre>{code}</pre>')
        return placeholder

    result = re.sub(r'```(\w+)?\n(.*?)```', _save_code_block, result, flags=re.DOTALL)

    # --- Inline code (`...`) -- protect from further processing ---
    inline_codes: list[str] = []

    def _save_inline_code(match: re.Match[str]) -> str:
        code = match.group(1)
        code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        placeholder = f'\x00INLINECODE{len(inline_codes)}\x00'
        inline_codes.append(f'<code>{code}</code>')
        return placeholder

    result = re.sub(r'`([^`]+)`', _save_inline_code, result)

    # --- Escape remaining bare HTML entities ---
    result = _escape_html(result)

    # --- Headers: ## Header -> <b>Header</b> ---
    result = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', result, flags=re.MULTILINE)

    # --- Bold: **text** or __text__ -> <b>text</b> ---
    result = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', result)
    result = re.sub(r'__(.+?)__', r'<b>\1</b>', result)

    # --- Italic: *text* or _text_ -> <i>text</i> ---
    # Negative lookbehind/lookahead to avoid matching inside words with underscores
    result = re.sub(r'(?<!\w)\*([^*]+?)\*(?!\w)', r'<i>\1</i>', result)
    result = re.sub(r'(?<!\w)_([^_]+?)_(?!\w)', r'<i>\1</i>', result)

    # --- Strikethrough: ~~text~~ -> <s>text</s> ---
    result = re.sub(r'~~(.+?)~~', r'<s>\1</s>', result)

    # --- Links: [text](url) -> <a href="url">text</a> ---
    result = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', result)

    # --- Unordered lists: - item or * item -> bullet ---
    _BULLET = '\u2022'
    result = re.sub(r'^[\s]*[-*]\s+', f'  {_BULLET} ', result, flags=re.MULTILINE)

    # --- Horizontal rules: --- or *** or ___ ---
    result = re.sub(r'^[-*_]{3,}\s*$', '', result, flags=re.MULTILINE)

    # --- Images: ![alt](url) -> just alt text ---
    result = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', result)

    # --- Strip unsupported HTML tags ---
    result = _strip_unsupported_html(result)

    # --- Restore code blocks ---
    for i, block in enumerate(code_blocks):
        result = result.replace(f'\x00CODEBLOCK{i}\x00', block)

    for i, code in enumerate(inline_codes):
        result = result.replace(f'\x00INLINECODE{i}\x00', code)

    # --- Clean up excessive blank lines (max 2 consecutive) ---
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result.strip()


def truncate_for_blockquote(
    description_html: str,
    *,
    message_prefix: str,
    message_suffix: str,
    max_message_length: int = 4096,
    ellipsis: str = '...',
) -> str:
    """Truncate description HTML to fit within Telegram message limit inside a blockquote.

    Calculates available space by subtracting prefix/suffix lengths and blockquote
    tag overhead from the total message limit.

    Args:
        description_html: The already-converted HTML description.
        message_prefix: Everything before the blockquote in the message.
        message_suffix: Everything after the blockquote in the message.
        max_message_length: Telegram message character limit (default 4096).
        ellipsis: String to append when truncating.

    Returns:
        The (possibly truncated) description HTML ready to be placed inside
        ``<blockquote expandable>...</blockquote>``.
    """
    blockquote_open = '<blockquote expandable>'
    blockquote_close = '</blockquote>'
    overhead = len(blockquote_open) + len(blockquote_close)

    available = max_message_length - len(message_prefix) - len(message_suffix) - overhead
    # Leave a small safety margin for any off-by-one with Telegram entity counting
    available -= 20

    if available <= 0:
        return ellipsis

    if len(description_html) <= available:
        return description_html

    # Truncate, trying not to break mid-tag
    truncated = description_html[: available - len(ellipsis)]

    # If we broke an HTML tag, backtrack to before it
    last_open = truncated.rfind('<')
    last_close = truncated.rfind('>')
    if last_open > last_close:
        truncated = truncated[:last_open]

    return truncated.rstrip() + ellipsis
