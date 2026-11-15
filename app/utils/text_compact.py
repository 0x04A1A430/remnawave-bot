"""Компактная сборка текстов пользовательских экранов.

Экраны собираются из независимых блоков (заголовок, цитата, подсказка, футер),
и каждый блок может прийти из локали или настроек с собственными переводами
строки. Склейка через ``\\n\\n`` в таком случае даёт две и более пустых строки,
а хвостовой перевод строки оставляет пустоту под последней строкой сообщения.

Нормализация здесь локальная, для конкретного готового текста: глобальные
локали не переписываются, поэтому намеренные отступы в других сообщениях
остаются нетронутыми.
"""

import re


_BLANK_LINE_RUN = re.compile(r'\n{3,}')
_TRAILING_SPACES = re.compile(r'[ \t]+\n')
_PRE_BLOCK = re.compile(r'<pre\b[^>]*>.*?</pre>', re.IGNORECASE | re.DOTALL)


def compact_blank_lines(text: str) -> str:
    """Схлопывает пустые строки и убирает пустые края.

    * любые серии переводов строки схлопываются в один перевод строки;
    * пробелы в конце строк не создают «пустую» строку из одних пробелов;
    * ведущие и хвостовые пустые строки срезаются.
    """
    if not text:
        return ''
    compacted = _TRAILING_SPACES.sub('\n\n', text)
    compacted = _BLANK_LINE_RUN.sub('\n\n', compacted)
    return compacted.strip()


def compact_message_spacing(text: str) -> str:
    """Схлопывает 3+ переводов строки в один пустой ряд, не трогая ``<pre>``.

    Применяется к готовым сообщениям/подписям на общем пути отправки: меню
    собираются из блоков, и лишняя пустая строка между ними выглядит как дыра.
    Блоки кода сохраняют исходные отступы — иначе логи и примеры становятся
    нечитаемыми. Края сообщения не срезаются: это делает вызывающий экран.
    """
    if not text or '\n\n\n' not in text:
        return text

    parts: list[str] = []
    position = 0
    for match in _PRE_BLOCK.finditer(text):
        parts.append(_BLANK_LINE_RUN.sub('\n\n', text[position : match.start()]))
        parts.append(match.group(0))
        position = match.end()
    parts.append(_BLANK_LINE_RUN.sub('\n\n', text[position:]))
    return ''.join(parts)
