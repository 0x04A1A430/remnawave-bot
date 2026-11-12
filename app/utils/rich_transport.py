"""Transparent rich-message transport (Bot API 10.1+).

Every screen of the bot is written as ordinary Telegram HTML messages with an
inline keyboard. This module converts those outgoing calls into rich messages on
the fly, keeping the text, formatting and keyboard exactly as-is, so the whole
bot can run on rich without rewriting thousands of handlers.

Converted methods:
- ``SendMessage`` -> ``SendRichMessage`` (text only, no logo);
- ``SendPhoto`` whose photo is the bot logo -> ``SendRichMessage`` with the logo
  image block + caption (this is the visual the bot already shows);
- ``SendPhoto`` with any other picture (gift QR, ticket attachment, news image)
  -> ``SendRichMessage`` with an uploaded media block (``tg://photo?id=…``);
- ``EditMessageText`` -> ``EditMessageText`` with ``rich_message``.

Documents, albums/media groups and inline-mode results stay classic. If
Telegram rejects a rich message for any reason, the original method is executed
as a fallback, so a screen can never be lost.
"""

from __future__ import annotations

import html
import re

import structlog
from aiogram.client.default import Default
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramNotFound
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, SendPhoto, SendRichMessage
from aiogram.methods.base import TelegramMethod, TelegramType
from aiogram.types import FSInputFile, InputMediaPhoto, InputRichMessage, InputRichMessageMedia, Message

from app.config import settings


logger = structlog.get_logger(__name__)

# Set once when the Bot API server does not know rich messages at all: after that
# every call goes straight to the classic method until restart.
_rich_unavailable = False
_warned_unavailable = False

# Telegram could not download the logo URL (private URL, expired CDN, wrong MIME).
# Rich screens then keep working without the image instead of falling back to the
# classic photo path on every single message.
_logo_unavailable = False
_warned_logo_unavailable = False

_MEDIA_FETCH_ERROR_MARKERS = (
    'http url',
    'webpage_',
    'media_empty',
    'photo_invalid',
    'image_process',
    'wrong type of the web page',
)

# Newlines are line breaks in classic messages, but rich HTML needs <br/>.
# Inside <pre> the newlines are meaningful and must survive untouched.
_PRE_BLOCK_RE = re.compile(r'(<pre\b[^>]*>.*?</pre>)', re.IGNORECASE | re.DOTALL)
# Linear tag stripping: the naive "[^>]+" form is quadratic on a string of "<"
# (CodeQL py/polynomial-redos), and it also eats "1 < 2" as a tag.
_TAG_RE = re.compile(r'<[^<>]+>')
_BR_RE = re.compile(r'<br\s*/?>', re.IGNORECASE)
# Classic Telegram HTML has <blockquote expandable>; rich HTML only knows
# <blockquote>. Passing the attribute through rejects the whole rich message.
_EXPANDABLE_QUOTE_RE = re.compile(r'<blockquote\s+expandable\s*>', re.IGNORECASE)
_HTML_PARSE_MODES = {'html', 'htm', 'text.html'}
_MARKDOWN_PARSE_MODES = {'markdown', 'markdownv2', 'md', 'text.markdown', 'text.markdownv2'}
_LOGO_DISABLED_VALUES = {'none', 'off', 'no', 'false', 'disabled', '-'}


def is_rich_transport_enabled() -> bool:
    return bool(getattr(settings, 'RICH_MESSAGES_ENABLED', False)) and not _rich_unavailable


def _plain_text(value: str) -> str:
    """Approximate the plain `.text` Telegram would expose for a classic message."""
    value = _BR_RE.sub('\n', value)
    value = _TAG_RE.sub('', value)
    return html.unescape(value)


def _rich_plain_text(node: object) -> str:
    """Extract readable text from a RichMessage block tree."""
    if isinstance(node, str):
        return node
    if isinstance(node, (list, tuple)):
        return ''.join(_rich_plain_text(item) for item in node)
    if hasattr(type(node), 'model_fields'):
        name = type(node).__name__
        # Inline text nodes expose their content in the `text` field.
        if hasattr(node, 'text'):
            value = _rich_plain_text(node.text)
            return value + ('\n' if name.startswith('RichBlock') else '')
        chunks: list[str] = []
        for field_name in type(node).model_fields:
            if field_name in {'type', 'url', 'custom_emoji_id', 'emoji', 'size', 'is_rtl'}:
                continue
            chunks.append(_rich_plain_text(getattr(node, field_name, None)))
        return ''.join(chunks)
    return ''


def patch_rich_message_compat(message: object) -> None:
    """Give rich messages a local `.text` so legacy edit/send checks keep working.

    ``Message`` is frozen, so the value is injected with ``object.__setattr__``.
    This only affects the running process, never the wire payload.
    """
    if message is None:
        return
    rich = getattr(message, 'rich_message', None)
    if rich is None:
        return
    if getattr(message, 'text', None):
        return
    try:
        text = _rich_plain_text(getattr(rich, 'blocks', None)).strip()
    except Exception:
        text = ''
    try:
        object.__setattr__(message, 'text', text or ' ')
    except Exception:
        pass


def _restore_message_fields(result: object, method: TelegramMethod) -> None:
    """Keep in-process checks working: rich messages have no `.text`/`.caption`.

    Message is a frozen pydantic model, so fields are set through
    ``object.__setattr__``. Only our own process sees this; the wire payload is
    untouched.
    """
    if not isinstance(result, Message):
        return
    text = getattr(method, 'text', None)
    caption = getattr(method, 'caption', None)
    try:
        if text and result.text is None:
            object.__setattr__(result, 'text', _plain_text(text))
        if caption and result.caption is None:
            object.__setattr__(result, 'caption', _plain_text(caption))
    except Exception:
        pass


def is_rich_transport_available() -> bool:
    return not _rich_unavailable


def get_logo_url() -> str:
    """Public logo URL for hand-built rich screens ('' when unavailable)."""
    return _resolve_logo_url()


def _mark_rich_unavailable(error: Exception) -> None:
    global _rich_unavailable, _warned_unavailable
    _rich_unavailable = True
    if not _warned_unavailable:
        _warned_unavailable = True
        logger.warning(
            'Bot API сервер не поддерживает rich-сообщения — весь бот переключён на классический рендер до рестарта',
            error=str(error)[:300],
        )


def _is_unsupported_error(error: Exception) -> bool:
    if isinstance(error, TelegramNotFound):
        return True
    text = str(error).lower()
    return (
        'unknown method' in text
        or 'method not found' in text
        or 'unsupported method' in text
        or 'not implemented' in text
    )


def _is_media_fetch_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _MEDIA_FETCH_ERROR_MARKERS)


def _mark_logo_unavailable(error: Exception) -> None:
    global _logo_unavailable, _warned_logo_unavailable
    _logo_unavailable = True
    if not _warned_logo_unavailable:
        _warned_logo_unavailable = True
        logger.warning(
            'Telegram не смог скачать логотип для rich-сообщений — экраны продолжат отправляться без картинки',
            error=str(error)[:300],
        )


def _resolve_logo_url() -> str:
    """Public http(s) URL of the bot logo, or '' when it cannot be rendered."""
    if _logo_unavailable:
        return ''
    explicit = (
        getattr(settings, 'RICH_MESSAGES_LOGO_URL', '') or getattr(settings, 'MAIN_MENU_RICH_LOGO_URL', '') or ''
    ).strip()
    if not explicit or explicit.lower() in _LOGO_DISABLED_VALUES:
        return ''
    if not explicit.lower().startswith(('http://', 'https://')):
        return ''
    return explicit


def _normalize_parse_mode(parse_mode: object) -> str:
    """Return the effective parse mode.

    aiogram distinguishes "not passed" (``Default('parse_mode')`` → the bot's
    HTML default) from an explicit ``None`` (no parsing at all). Treating both as
    HTML corrupted plain-text messages and could break on a bare ``<``.
    """
    if isinstance(parse_mode, Default):
        return 'html'
    if parse_mode is None:
        return 'plain'
    return str(parse_mode).lower()


def _newlines_to_br(text: str) -> str:
    # Одна пустая строка — нормальный разделитель, две и больше — «дыры» в экране.
    text = re.sub(r'\n{3,}', '\n\n', text)
    parts = _PRE_BLOCK_RE.split(text)
    for index in range(0, len(parts), 2):  # even indexes — outside <pre>
        parts[index] = parts[index].replace('\r\n', '\n').replace('\n', '<br/>')
    return ''.join(parts)


def html_to_rich(text: str, parse_mode: object) -> str | None:
    """Convert classic Telegram HTML into rich HTML.

    Returns ``None`` when the content cannot be represented reliably (Markdown,
    entity arrays), so the caller falls back to the classic method.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    mode = _normalize_parse_mode(parse_mode)
    if mode in _MARKDOWN_PARSE_MODES:
        return None
    if mode not in _HTML_PARSE_MODES:
        # Unknown/plain mode: the text is data, not markup.
        return _newlines_to_br(html.escape(text, quote=False))

    return _newlines_to_br(_EXPANDABLE_QUOTE_RE.sub('<blockquote>', text))


def _is_logo_photo(photo: object) -> bool:
    """True when a sendPhoto call carries the bot logo (and can be a rich image)."""
    if isinstance(photo, FSInputFile):
        try:
            from app.utils.message_patch import LOGO_PATH, _logo_send_path

            candidate = getattr(photo, 'path', None)
            if candidate is None:
                return False
            path = candidate if hasattr(candidate, 'resolve') else None
            if path is None:
                return False
            target = (_logo_send_path or LOGO_PATH).resolve()
            return path.resolve() == target
        except Exception:
            return False

    if isinstance(photo, str):
        # Cached file_id of the logo or its public URL.
        try:
            from app.utils import message_patch

            if message_patch._logo_file_id and photo == message_patch._logo_file_id:
                return True
        except Exception:
            pass
        logo_url = _resolve_logo_url()
        return bool(logo_url) and photo.strip() == logo_url

    return False


def _rich_html_for_text(text: str, parse_mode: object) -> str | None:
    rich_html = html_to_rich(text, parse_mode)
    if rich_html is None:
        return None
    return rich_html


def _rich_html_for_logo(caption: str | None, parse_mode: object) -> str | None:
    logo_url = _resolve_logo_url()
    if not logo_url:
        return None
    if caption is None:
        return f'<img src="{html.escape(logo_url, quote=True)}"/>'
    rich_caption = html_to_rich(caption, parse_mode)
    if rich_caption is None:
        return None
    return f'<img src="{html.escape(logo_url, quote=True)}"/>{rich_caption}'


def _has_entities(method: TelegramMethod) -> bool:
    return bool(getattr(method, 'entities', None) or getattr(method, 'caption_entities', None))


def _rich_send_kwargs(method: SendMessage | SendPhoto) -> dict[str, object]:
    """Common SendRichMessage fields copied from the classic send method."""
    reply_parameters = method.reply_parameters
    if reply_parameters is None and getattr(method, 'reply_to_message_id', None):
        from aiogram.types import ReplyParameters

        reply_parameters = ReplyParameters(
            message_id=method.reply_to_message_id,
            allow_sending_without_reply=getattr(method, 'allow_sending_without_reply', None),
        )

    return {
        'chat_id': method.chat_id,
        'business_connection_id': method.business_connection_id,
        'message_thread_id': method.message_thread_id,
        'direct_messages_topic_id': getattr(method, 'direct_messages_topic_id', None),
        'disable_notification': method.disable_notification,
        'protect_content': method.protect_content,
        'allow_paid_broadcast': method.allow_paid_broadcast,
        'message_effect_id': method.message_effect_id,
        'suggested_post_parameters': method.suggested_post_parameters,
        'reply_parameters': reply_parameters,
        'reply_markup': method.reply_markup,
        'ephemeral_message_parameters': getattr(method, 'ephemeral_message_parameters', None),
    }


def _convert_send_message(method: SendMessage) -> SendRichMessage | None:
    if _has_entities(method):
        return None
    rich_html = _rich_html_for_text(method.text, method.parse_mode)
    if rich_html is None:
        return None

    return SendRichMessage(
        rich_message=InputRichMessage(html=rich_html),
        **_rich_send_kwargs(method),
    )


def _with_logo(rich_html: str) -> str:
    """Prepend the bot logo to a text screen, unless it already carries media.

    Classic navigation always kept the logo: text menus were photo messages, and
    editing one replaced the photo with the logo. Rich messages have no caption
    photo, so the logo has to be added to the rich payload explicitly.
    """
    if not getattr(settings, 'ENABLE_LOGO_MODE', False):
        return rich_html
    if '<img' in rich_html.lower() or 'tg://photo' in rich_html.lower():
        return rich_html
    logo_url = _resolve_logo_url()
    if not logo_url:
        return rich_html
    return f'<img src="{html.escape(logo_url, quote=True)}"/>{rich_html}'


def _convert_edit_message_text(method: EditMessageText) -> EditMessageText | None:
    if _has_entities(method) or not method.text:
        return None
    rich_html = _rich_html_for_text(method.text, method.parse_mode)
    if rich_html is None:
        return None
    rich_html = _with_logo(rich_html)

    return method.model_copy(
        update={
            'text': None,
            'parse_mode': None,
            'entities': None,
            'link_preview_options': None,
            'disable_web_page_preview': None,
            'rich_message': InputRichMessage(html=rich_html),
        }
    )


def _convert_send_photo(method: SendPhoto) -> SendRichMessage | None:
    if _has_entities(method):
        return None

    # Logo -> public URL, exactly like the classic logo+caption screen.
    if _is_logo_photo(method.photo):
        if _logo_unavailable:
            # URL failed once — keep the rich screen, just without the picture.
            body = html_to_rich(method.caption, method.parse_mode) if method.caption else None
            if not body:
                return None
            return SendRichMessage(
                rich_message=InputRichMessage(html=body),
                **_rich_send_kwargs(method),
            )
        rich_html = _rich_html_for_logo(method.caption, method.parse_mode)
        if rich_html is None:
            return None
        return SendRichMessage(
            rich_message=InputRichMessage(html=rich_html),
            **_rich_send_kwargs(method),
        )

    # Any other photo (gift QR, ticket attachment, news image…) becomes a rich
    # media block. Bot API 10.2 lets us upload the file and reference it through
    # tg://photo?id=, so locally generated images stay supported.
    #
    # In-memory uploads (BufferedInputFile) are functional attachments: staging
    # and support routes read `message.photo[-1].file_id` from the result, and a
    # rich message has no `.photo`. Keep those classic so uploads cannot break.
    if not isinstance(method.photo, (str, FSInputFile)):
        return None

    caption_html = html_to_rich(method.caption, method.parse_mode) if method.caption else ''
    if caption_html is None:
        return None
    media_id = 'photo'
    image = f'<img src="tg://photo?id={media_id}"/>'
    show_above = getattr(method, 'show_caption_above_media', None) is True
    rich_html = f'{caption_html}{image}' if show_above else f'{image}{caption_html}'
    rich_media = [
        InputRichMessageMedia(
            id=media_id,
            media=InputMediaPhoto(
                media=method.photo,
                has_spoiler=method.has_spoiler or None,
            ),
        )
    ]
    return SendRichMessage(
        rich_message=InputRichMessage(html=rich_html, media=rich_media),
        **_rich_send_kwargs(method),
    )


def convert_method(method: TelegramMethod) -> TelegramMethod | None:
    """Return a rich-message twin of ``method``, or None to keep it classic."""
    if not is_rich_transport_enabled():
        return None

    if isinstance(method, SendMessage):
        return _convert_send_message(method)
    if isinstance(method, EditMessageText):
        return _convert_edit_message_text(method)
    if isinstance(method, SendPhoto):
        return _convert_send_photo(method)
    return None


_TG_EMOJI_TAG_RE = re.compile(r'<tg-emoji\b[^>]*>(.*?)</tg-emoji>', re.DOTALL)


def _sanitize_method(method: TelegramMethod[TelegramType]) -> TelegramMethod[TelegramType]:
    """Keep method payloads valid outside HTML parsing.

    Button captions cannot carry HTML — custom emoji becomes a button icon.
    Callback answers are plain text (toasts/alerts) — tags are unwrapped.
    """
    updates: dict = {}

    markup = getattr(method, 'reply_markup', None)
    if markup is not None:
        from app.utils.button_emoji import sanitize_markup

        sanitized = sanitize_markup(markup)
        if sanitized is not markup:
            updates['reply_markup'] = sanitized

    if isinstance(method, AnswerCallbackQuery) and method.text and '<tg-emoji' in method.text:
        updates['text'] = _TG_EMOJI_TAG_RE.sub(r'\1', method.text)

    return method.model_copy(update=updates) if updates else method


class RichAiohttpSession(AiohttpSession):
    """Aiohttp session that transparently upgrades messages to rich ones."""

    async def make_request(
        self,
        bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        method = _sanitize_method(method)
        rich_method = convert_method(method)
        if rich_method is None:
            return await super().make_request(bot, method, timeout)

        try:
            result = await super().make_request(bot, rich_method, timeout)
        except TelegramNetworkError:
            # Network failure: the message was not delivered, but retrying through
            # the classic path could duplicate it. Let the caller handle it.
            raise
        except TelegramBadRequest as error:
            if _is_unsupported_error(error):
                _mark_rich_unavailable(error)
                return await super().make_request(bot, method, timeout)
            if (
                isinstance(method, SendPhoto)
                and _is_logo_photo(method.photo)
                and not _logo_unavailable
                and _is_media_fetch_error(error)
            ):
                # The logo URL is the only broken part: retry as rich without it.
                _mark_logo_unavailable(error)
                retry_method = convert_method(method)
                if retry_method is not None:
                    try:
                        result = await super().make_request(bot, retry_method, timeout)
                    except (TelegramBadRequest, TelegramNotFound):
                        pass
                    else:
                        _restore_message_fields(result, method)
                        return result
                return await super().make_request(bot, method, timeout)
            logger.debug(
                'Rich-сообщение отклонено, повтор классическим методом',
                method=type(method).__name__,
                error=str(error)[:200],
            )
            return await super().make_request(bot, method, timeout)
        except TelegramNotFound as error:
            _mark_rich_unavailable(error)
            return await super().make_request(bot, method, timeout)

        # Some handlers inspect the sent message (message_id, text/caption
        # presence). Rich messages have neither, so expose the original text
        # locally to keep those checks working.
        _restore_message_fields(result, method)
        return result
