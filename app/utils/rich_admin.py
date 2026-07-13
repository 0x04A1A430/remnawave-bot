"""Rich-рендер сообщений админ-чата (Bot API 10.1, aiogram 3.29+).

Общий слой для всех отправок в админ-чат: уведомления
(AdminNotificationService._send_message), error-логи
(send_error_to_admin_chat), стартовое сообщение и отчёты. Даёт заголовки,
таблицы, сворачиваемые details-блоки с трейсбеками в <pre><code> и лимит
rich-сообщений 32768 символов вместо 4096 у классических.

Fallback-модель как у rich-меню (app/utils/rich_menu.py): после первого ответа
сервера «метод неизвестен» модуль запоминает недоступность до рестарта, и все
вызывающие пути возвращаются к классическим HTML-отправкам.

АНТИЛУП: этот модуль вызывается в том числе из конвейера отправки error-логов
в Telegram — любые собственные сбои логируются НЕ ВЫШЕ warning и строкой
(без объекта исключения), иначе получится усиление через
TelegramNotifierProcessor и flood control.
"""

import html
import re
from datetime import UTC, datetime

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNotFound
from aiogram.types import InlineKeyboardMarkup, InputRichMessage

from app.config import settings
from app.utils.rich_menu import _looks_like_unsupported


logger = structlog.get_logger(__name__)

# Официальный лимит rich-сообщений — 32768 UTF-8 символов; держим запас
# на служебную разметку.
RICH_TEXT_LIMIT = 30_000

# Сервер не поддерживает rich-сообщения — латч до рестарта (отдельный от
# rich-меню: включаться/выключаться они могут независимо).
_rich_unavailable = False

# Классические админ-тексты используют <blockquote expandable> — rich-HTML
# такого атрибута не знает и отклонил бы всё сообщение.
_EXPANDABLE_QUOTE_RE = re.compile(r'<blockquote\s+expandable>', re.IGNORECASE)
# Первая жирная строка классического уведомления («<b>💎 ПОКУПКА</b>\n…») —
# становится заголовком rich-сообщения.
_LEADING_BOLD_RE = re.compile(r'^<b>(?P<title>[^<]{1,120})</b>\s*\n+')


def is_rich_admin_enabled() -> bool:
    return bool(settings.ADMIN_NOTIFICATIONS_RICH_ENABLED) and not _rich_unavailable


def _reset_rich_admin_availability() -> None:
    """Сбрасывает латч недоступности (используется в тестах)."""
    global _rich_unavailable
    _rich_unavailable = False


def _mark_rich_admin_unavailable(error: Exception) -> None:
    global _rich_unavailable
    if not _rich_unavailable:
        logger.warning(
            'Bot API сервер не поддерживает rich-сообщения — админ-уведомления переключены на классический вид',
            error=str(error),
        )
    _rich_unavailable = True


def rich_footer_now(label: str = 'Remnawave Bedolaga Bot') -> str:
    """Футер с меткой и временем: tg-time рендерится в таймзоне админа."""
    now = datetime.now(UTC)
    stamp = f'<tg-time unix="{int(now.timestamp())}" format="dt">{now.strftime("%d.%m.%Y %H:%M")} UTC</tg-time>'
    return f'<footer>{html.escape(label)} · {stamp}</footer>'


def rich_kv_table(rows: list[tuple[str, str]]) -> str:
    """Таблица «показатель → значение» (bordered/striped). Значения — сырой HTML."""
    body = ''.join(f'<tr><td>{html.escape(key)}</td><td align="right">{value}</td></tr>' for key, value in rows)
    return f'<table bordered striped>{body}</table>'


def rich_traceback_details(summary: str, traceback_text: str, *, open_by_default: bool = False) -> str:
    """Сворачиваемый traceback: <details> + <pre><code class="language-python">."""
    open_attr = ' open' if open_by_default else ''
    return (
        f'<details{open_attr}><summary>{html.escape(summary)}</summary>'
        f'<pre><code class="language-python">{html.escape(traceback_text)}</code></pre></details>'
    )


def classic_admin_html_to_rich(text: str, *, footer_label: str | None = None) -> str:
    """Конвертирует классическое HTML-уведомление в rich-разметку.

    Консервативно: содержимое не переписывается, только оформление —
    первая жирная строка становится заголовком h6 с разделителем,
    неподдерживаемый rich-HTML атрибут expandable у blockquote убирается,
    в конец добавляется footer с временем.
    """
    rich = _EXPANDABLE_QUOTE_RE.sub('<blockquote>', text.strip())

    match = _LEADING_BOLD_RE.match(rich)
    if match:
        rich = f'<h6>{match.group("title").strip()}</h6><hr/>{rich[match.end() :]}'

    footer = rich_footer_now(footer_label) if footer_label else rich_footer_now()
    return f'{rich}\n<hr/>{footer}'


async def try_send_rich_admin_message(
    bot: Bot,
    chat_id: int | str,
    rich_html: str,
    *,
    thread_id: int | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Отправляет rich-сообщение в админ-чат. False — слать классический вариант.

    Без ретраев: ретраи и обработку flood control делает классический путь,
    на который вызывающий код обязан откатиться при False.
    """
    if not is_rich_admin_enabled():
        return False
    if len(rich_html) > RICH_TEXT_LIMIT:
        return False

    kwargs: dict = {
        'chat_id': chat_id,
        'rich_message': InputRichMessage(html=rich_html, skip_entity_detection=True),
    }
    if thread_id:
        kwargs['message_thread_id'] = thread_id
    if reply_markup:
        kwargs['reply_markup'] = reply_markup

    try:
        await bot.send_rich_message(**kwargs)
        return True
    except (TelegramNotFound, TelegramBadRequest) as error:
        if _looks_like_unsupported(error):
            _mark_rich_admin_unavailable(error)
        else:
            logger.warning('Не удалось отправить rich-сообщение в админ-чат', error=str(error))
        return False
    except TelegramForbiddenError as error:
        # Бот не может писать в чат — классический путь упрётся в то же самое,
        # но пусть отработает его штатная обработка.
        logger.warning('Rich-сообщение в админ-чат запрещено', error=str(error))
        return False
    except Exception as error:
        logger.warning('Ошибка отправки rich-сообщения в админ-чат', error=str(error))
        return False
