"""Rich-рендер главного меню через rich-сообщения Bot API 10.1 (aiogram 3.29+).

Главное меню собирается в rich-HTML (заголовки, таблица подписок, details-блоки,
tg-time с датами в таймзоне клиента, footer) и отправляется через sendRichMessage /
editMessageText(rich_message=...). Все try_*-хелперы возвращают bool: False означает
«rich не отрисован» — вызывающий код обязан показать классическое меню.

Fallback-модель повторяет happ-crypt паттерн из app/external/remnawave_api.py:
после первого ответа сервера «метод неизвестен» (устаревший self-hosted
telegram-bot-api) модуль запоминает недоступность до рестарта и больше не
пытается. Ошибки конкретного рендера (например, неотредактированное сообщение)
на флаг не влияют — просто отдаём False и меню рисуется классикой.

Ограничение: у rich-сообщения нет фото, поэтому при ENABLE_LOGO_MODE главное
меню в rich-режиме показывается без логотипа, а переходы меню <-> разделы с
логотипом идут через delete+send (существующие fallback-и photo_message).
"""

import html
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import structlog
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
)
from aiogram.methods import EditMessageText
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.utils.validators import sanitize_html


logger = structlog.get_logger(__name__)

_RTL_LANGUAGES = frozenset({'ar', 'he'})

# Сервер не поддерживает rich-сообщения (устаревший self-hosted bot-api).
# Взводится один раз до рестарта — по образцу _happ_encrypt_unavailable.
_rich_unavailable = False

# Сервер отклонил message_effect_id (например, эффект отключили или id невалиден) —
# дальше шлём меню без эффекта, не роняя rich-рендер в классику.
_effect_unavailable = False

# Трекинг последнего rich-меню: chat_id -> message_id. Позволяет удалить меню,
# когда нажатие кнопки открыло экран ОТДЕЛЬНЫМ сообщением (иначе меню копится в чате).
_tracked_rich_menus: dict[int, int] = {}

# chat_id'ы текущего события, где меню было переиспользовано (отредактировано на месте,
# заменено новым rich или удалено самим rich-флоу). Сбрасывается мидлварью перед хендлером.
_taken_over_chats: set[int] = set()


def mark_rich_menu_taken_over(chat_id: int | None) -> None:
    """Отмечает, что при этом нажатии прежнее rich-меню было поглощено/заменено."""
    if chat_id is not None:
        _taken_over_chats.add(chat_id)


def reset_rich_menu_takeover(chat_id: int | None) -> None:
    """Сбрасывает флаг «переиспользовано» перед обработкой нового события."""
    if chat_id is not None:
        _taken_over_chats.discard(chat_id)


def _track_rich_menu(chat_id: int, result: object) -> None:
    """Запоминает message_id отправленного rich-меню (объект может не вернуться)."""
    mark_rich_menu_taken_over(chat_id)
    message_id = getattr(result, 'message_id', None)
    if isinstance(message_id, int):
        _tracked_rich_menus[chat_id] = message_id


async def cleanup_stale_rich_menu(bot: Bot, chat_id: int) -> None:
    """Удаляет прежнее rich-меню, если это нажатие кнопки его не переиспользовало."""
    message_id = _tracked_rich_menus.pop(chat_id, None)
    if message_id is None or chat_id in _taken_over_chats:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound):
        pass

# Telegram не смог скачать логотип по URL (нет публичного доступа, битый файл) —
# дальше собираем меню без логотипа, не роняя rich-рендер в классику.
_logo_unavailable = False

# Про кривой MAIN_MENU_RICH_LOGO_URL предупреждаем один раз: резолвер зовётся
# на каждый рендер меню, а значение статичное.
_logo_url_warned = False

# MAIN_MENU_RICH_LOGO_URL с таким значением означает «шапка без логотипа».
# Пустая строка занята под авто-режим (свой LOGO_FILE), поэтому нужен явный
# способ выключить картинку, не выключая rich-меню целиком.
_LOGO_DISABLED_VALUES = frozenset({'-', 'disabled', 'false', 'no', 'none', 'off'})

# Маркеры ошибок загрузки медиа по URL со стороны Telegram.
_MEDIA_FETCH_ERROR_MARKERS = (
    'http url',
    'webpage_',
    'media_empty',
    'photo_invalid',
    'image_process',
    'wrong type of the web page',
)

# Теги, которые допускает sanitize_html, но не понимает rich-HTML: спойлерный
# span конвертируем в родной <tg-spoiler>, прочие span разворачиваем (содержимое
# остаётся), img выкидываем целиком.
_SPOILER_SPAN_RE = re.compile(
    r'<span\s+class=(["\'])tg-spoiler\1[^>]*>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)
_SPAN_TAG_RE = re.compile(r'</?span[^>]*>', re.IGNORECASE)
_IMG_TAG_RE = re.compile(r'<img[^>]*/?>', re.IGNORECASE)


def is_rich_menu_enabled() -> bool:
    return bool(settings.MAIN_MENU_RICH_ENABLED) and not _rich_unavailable


def _reset_rich_menu_availability() -> None:
    """Сбрасывает флаги недоступности (используется в тестах)."""
    global _rich_unavailable, _effect_unavailable, _logo_unavailable, _logo_url_warned
    _rich_unavailable = False
    _effect_unavailable = False
    _logo_unavailable = False
    _logo_url_warned = False


def _warn_bad_logo_url_once(value: str) -> None:
    global _logo_url_warned
    if _logo_url_warned:
        return
    _logo_url_warned = True
    logger.warning(
        'MAIN_MENU_RICH_LOGO_URL не похож на http(s)-ссылку — rich-меню отправляется без логотипа. '
        'Чтобы убрать логотип намеренно, укажите none',
        value=value[:100],
    )


def _resolve_rich_logo_url() -> str:
    """Публичный URL логотипа для шапки rich-меню ('' — без логотипа).

    Явный MAIN_MENU_RICH_LOGO_URL приоритетнее. Значение из _LOGO_DISABLED_VALUES
    (none/off/no/false/disabled/-) выключает логотип совсем: пустая строка занята
    под авто-режим, поэтому при существующем LOGO_FILE шапку иначе было не убрать,
    а «подставлю ссылку не на картинку» роняло весь rich в классику. Значение,
    не похожее на http(s)-ссылку, трактуем так же — скачать его Telegram всё
    равно не сможет, а меню важнее логотипа.

    Иначе, если задан WEBHOOK_URL (публичный origin нашего FastAPI) и файл
    LOGO_FILE существует, логотип отдаётся собственным эндпоинтом
    /cabinet/branding/bot-logo.
    """
    if _logo_unavailable:
        return ''

    explicit = (settings.MAIN_MENU_RICH_LOGO_URL or '').strip()
    if explicit:
        if explicit.lower() in _LOGO_DISABLED_VALUES:
            return ''
        if not explicit.lower().startswith(('http://', 'https://')):
            _warn_bad_logo_url_once(explicit)
            return ''
        return explicit

    webhook_url = (settings.WEBHOOK_URL or '').strip()
    if not webhook_url or not settings.LOGO_FILE or not Path(settings.LOGO_FILE).is_file():
        return ''
    parsed = urlparse(webhook_url)
    if not parsed.scheme or not parsed.netloc:
        return ''
    return f'{parsed.scheme}://{parsed.netloc}/cabinet/branding/bot-logo'


def _is_media_fetch_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _MEDIA_FETCH_ERROR_MARKERS)


def _retry_without_logo(error: Exception) -> bool:
    """True — логотип был в меню, выключаем его и повторяем отправку.

    Картинку по URL качает сам Telegram, и это самая частая причина отказа.
    Известные маркеры ловим по тексту, но у rich-сообщений коды ошибок свои и
    список заведомо неполон: раньше при незнакомой ошибке меню целиком уезжало
    в классику («рич не включается»), хотя достаточно было убрать картинку.
    Повтор ровно один — _mark_logo_unavailable_once взводит флаг до рестарта.
    """
    if not _resolve_rich_logo_url():
        return False
    if not _is_media_fetch_error(error):
        logger.warning(
            'Rich-меню отклонено незнакомой ошибкой — повторяем без логотипа',
            error=str(error)[:200],
        )
    return _mark_logo_unavailable_once(error)


def _mark_logo_unavailable_once(error: Exception) -> bool:
    """Взводит флаг «логотип не загружается». True — если флаг только что взвёлся
    (можно один раз пересобрать меню без логотипа и повторить отправку)."""
    global _logo_unavailable
    if _logo_unavailable:
        return False
    _logo_unavailable = True
    logger.warning(
        'Telegram не смог загрузить логотип rich-меню по URL — меню отправляется без логотипа',
        error=str(error),
    )
    return True


def _mark_rich_unavailable(error: Exception) -> None:
    global _rich_unavailable
    if not _rich_unavailable:
        logger.warning(
            'Bot API сервер не поддерживает rich-сообщения — главное меню переключено на классический рендер',
            error=str(error),
        )
    _rich_unavailable = True


def _looks_like_unsupported(error: Exception) -> bool:
    """Отличает «сервер не знает про rich» от ошибок конкретного рендера.

    Устаревший telegram-bot-api отвечает 404 Not Found на неизвестный метод
    (sendRichMessage) и 'message text is empty' на editMessageText без text.
    """
    if isinstance(error, TelegramNotFound):
        return True
    text = str(error).lower()
    return 'unknown method' in text or 'method not found' in text or 'text is empty' in text


# Telegram принимает дату сущности только в диапазоне [0, сейчас + 1098 дней]
# (core.telegram.org/api/entities, messageEntityFormattedDate: «time()+1098*86400»).
# Дата за границей отклоняется ошибкой RICH_MESSAGE_DATE_INVALID, и сервер роняет
# ВСЁ rich-сообщение, а не одну ячейку — меню целиком уходит в классический вид.
#
# Раньше здесь стоял предел 32-битного unix time (19.01.2038). Он ловил только
# «вечные» подписки из панели, а лимит Telegram почти на десятилетие ближе: любая
# подписка дальше ~3 лет вперёд ломала меню. Считаем границу от текущего момента,
# с суточным запасом на расхождение часов с серверами Telegram.
_TG_TIME_MAX_AHEAD = timedelta(days=1097)


def _tg_time(moment: datetime, time_format: str, fallback: str) -> str:
    try:
        unix_time = int(moment.timestamp())
        max_unix = int((datetime.now(UTC) + _TG_TIME_MAX_AHEAD).timestamp())
    except (OverflowError, OSError, ValueError):
        # datetime.max и прочие сентинелы: timestamp() на них падает на части платформ.
        return html.escape(fallback)

    if not 0 < unix_time <= max_unix:
        return html.escape(fallback)
    return f'<tg-time unix="{unix_time}" format="{time_format}">{html.escape(fallback)}</tg-time>'


def _sanitize_rich_inline(value: str) -> str:
    """Приводит sanitize_html-вывод (случайные сообщения админа) к rich-HTML."""
    value = _SPOILER_SPAN_RE.sub(r'<tg-spoiler>\2</tg-spoiler>', value)
    value = _SPAN_TAG_RE.sub('', value)
    return _IMG_TAG_RE.sub('', value)


def _rich_text(value: str) -> str:
    """Готовит редактируемый из админки ТЕКСТ ШАБЛОНА к вставке в rich-HTML.

    Тексты меню правятся оператором и могут нести разметку из ALLOWED_HTML_TAGS —
    прежде всего `<tg-emoji emoji-id=...>` с премиум-эмодзи. Глухой html.escape()
    выводил такие теги сырыми прямо в сообщение (у клиента видно
    «<tg-emoji emoji-id="…">» текстом), хотя rich-сообщения их поддерживают.
    Поэтому экранируем, возвращаем разрешённое подмножество через sanitize_html
    (он же срежет чужие теги, атрибуты и javascript:-ссылки) и приводим к rich-HTML.

    ТОЛЬКО для шаблонов. Значения, подставляемые в {плейсхолдеры} — имя
    пользователя, название тарифа, суммы, — экранируются как раньше: это данные,
    а не разметка.
    """
    if not value:
        return value
    return _sanitize_rich_inline(sanitize_html(html.escape(value)))


async def build_main_menu_rich_html(user: User, texts, db: AsyncSession) -> str:
    """Собирает rich-HTML главного меню (контент, без клавиатуры).

    Минималистичный рендер: контент ровно тот же, что у классического меню —
    get_main_menu_text остаётся единственным источником правды (шапка с именем,
    статус подписки, подсказки, случайное сообщение). Никаких таблиц,
    прогресс-баров и отдельных блоков трафика/устройств/ссылок.
    """
    from app.handlers.menu import get_main_menu_text

    menu_text = await get_main_menu_text(user, texts, db)

    blocks: list[str] = []

    logo_url = _resolve_rich_logo_url()
    if logo_url:
        blocks.append(f'<img src="{html.escape(logo_url, quote=True)}"/>')

    # Текст классического меню уже HTML (<b>, <blockquote>, tg-emoji) — прогоняем
    # его через тот же _rich_text, что и редактируемые шаблоны, а переносы строк
    # заменяем на <br>: в rich-HTML голый backslash-n не отображается.
    blocks.append(_rich_text(menu_text).replace('\n', '<br/>'))

    return ''.join(blocks)


_TG_TIME_TAG_RE = re.compile(r'<tg-time\b[^>]*>(.*?)</tg-time>', re.DOTALL | re.IGNORECASE)


def _is_rich_date_error(error: Exception) -> bool:
    return 'rich_message_date_invalid' in str(error).lower()


def _strip_tg_time(rich_html: str) -> str:
    """Убирает теги tg-time, оставляя их текст.

    Страховка от RICH_MESSAGE_DATE_INVALID: одна дата вне допустимого диапазона
    отвергает ВСЁ rich-сообщение, а не свою ячейку. Границу мы держим сами
    (см. _tg_time), но Telegram уже дважды оказывался строже, чем мы считали, —
    пусть в таком случае меню теряет форматирование дат, а не уезжает в классику.
    """
    return _TG_TIME_TAG_RE.sub(r'\1', rich_html)


def _input_rich_message(rich_html: str, language: str | None) -> InputRichMessage:
    return InputRichMessage(
        html=rich_html,
        is_rtl=True if (language or '').lower() in _RTL_LANGUAGES else None,
        skip_entity_detection=True,
    )


async def _send_rich_menu(
    bot: Bot,
    chat_id: int,
    rich_html: str,
    keyboard: InlineKeyboardMarkup,
    language: str | None,
) -> None:
    global _effect_unavailable

    effect_id = (settings.MAIN_MENU_RICH_EFFECT_ID or '').strip() or None
    if _effect_unavailable:
        effect_id = None

    try:
        result = await bot.send_rich_message(
            chat_id=chat_id,
            rich_message=_input_rich_message(rich_html, language),
            reply_markup=keyboard,
            message_effect_id=effect_id,
        )
        _track_rich_menu(chat_id, result)
        return
    except TelegramBadRequest as error:
        if _is_rich_date_error(error):
            logger.warning(
                'Сервер отклонил дату в rich-меню — повтор без tg-time',
                error=str(error),
                chat_id=chat_id,
            )
            result = await bot.send_rich_message(
                chat_id=chat_id,
                rich_message=_input_rich_message(_strip_tg_time(rich_html), language),
                reply_markup=keyboard,
                message_effect_id=effect_id,
            )
            _track_rich_menu(chat_id, result)
            return
        # Невалидный/отключённый эффект не должен ронять rich-меню в классику —
        # повторяем без эффекта и больше его не шлём до рестарта.
        if effect_id and 'effect' in str(error).lower():
            _effect_unavailable = True
            logger.warning(
                'Сервер отклонил message_effect_id — меню отправляется без эффекта',
                effect_id=effect_id,
                error=str(error),
            )
            result = await bot.send_rich_message(
                chat_id=chat_id,
                rich_message=_input_rich_message(rich_html, language),
                reply_markup=keyboard,
            )
            _track_rich_menu(chat_id, result)
        else:
            raise


async def try_send_rich_main_menu(
    bot: Bot,
    chat_id: int,
    db_user: User,
    texts,
    db: AsyncSession,
    keyboard: InlineKeyboardMarkup,
) -> bool:
    """Отправляет главное меню rich-сообщением. False — показать классическое меню."""
    if not is_rich_menu_enabled():
        return False

    try:
        rich_html = await build_main_menu_rich_html(db_user, texts, db)
    except Exception as error:
        logger.error('Ошибка сборки rich-меню', error=error, user_id=getattr(db_user, 'id', None))
        return False

    try:
        await _send_rich_menu(bot, chat_id, rich_html, keyboard, db_user.language)
        return True
    except TelegramForbiddenError:
        # Пользователь заблокировал бота — классический рендер упадёт так же, не ретраим.
        logger.warning('Не удалось отправить rich-меню: бот заблокирован пользователем', chat_id=chat_id)
        return True
    except (TelegramNotFound, TelegramBadRequest) as error:
        if _looks_like_unsupported(error):
            _mark_rich_unavailable(error)
        elif _retry_without_logo(error):
            # Логотип не скачался — единственный повтор уже без него (флаг взведён).
            return await try_send_rich_main_menu(bot, chat_id, db_user, texts, db, keyboard)
        else:
            logger.error('Не удалось отправить rich-меню', error=error, chat_id=chat_id)
        return False
    except TelegramNetworkError as error:
        logger.warning('Сетевая ошибка при отправке rich-меню', error=str(error), chat_id=chat_id)
        return False


async def try_answer_rich_main_menu(
    message: Message,
    db_user: User,
    texts,
    db: AsyncSession,
    keyboard: InlineKeyboardMarkup,
) -> bool:
    """Rich-аналог message.answer(menu_text) для /start и завершения регистрации."""
    bot = message.bot
    if bot is None:
        return False
    return await try_send_rich_main_menu(bot, message.chat.id, db_user, texts, db, keyboard)


async def try_edit_rich_main_menu(
    callback: CallbackQuery,
    db_user: User,
    texts,
    db: AsyncSession,
    keyboard: InlineKeyboardMarkup,
) -> bool:
    """Rich-аналог edit_or_answer_photo для callback-навигации. False — рисовать классику."""
    if not is_rich_menu_enabled():
        return False

    message = callback.message
    bot = callback.bot
    if message is None or bot is None:
        return False

    try:
        rich_html = await build_main_menu_rich_html(db_user, texts, db)
    except Exception as error:
        logger.error('Ошибка сборки rich-меню', error=error, user_id=getattr(db_user, 'id', None))
        return False

    chat_id = message.chat.id
    language = db_user.language

    is_editable_as_rich = (
        not isinstance(message, InaccessibleMessage)
        and not getattr(message, 'photo', None)
        and (message.text is not None or getattr(message, 'rich_message', None) is not None)
    )

    try:
        if is_editable_as_rich:
            # parse_mode=None явно: иначе дефолтный parse_mode бота (HTML) сериализуется
            # в запрос рядом с rich_message.
            await bot(
                EditMessageText(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    rich_message=_input_rich_message(rich_html, language),
                    reply_markup=keyboard,
                    parse_mode=None,
                )
            )
            mark_rich_menu_taken_over(chat_id)
        else:
            # Фото/медиа-сообщение (логотип) или недоступное (>48ч) нельзя превратить
            # в rich редактированием — пересоздаём, как это делает edit_or_answer_photo
            # при смене типа сообщения.
            if not isinstance(message, InaccessibleMessage):
                try:
                    await message.delete()
                except (TelegramBadRequest, TelegramForbiddenError) as delete_error:
                    # Например, сообщению больше 48 часов — deleteMessage запрещён, хотя
                    # редактирование ещё работает. Отдаём классическому рендеру: он
                    # отредактирует уцелевшее сообщение на месте и не наплодит дублей.
                    logger.debug('Не удалось удалить сообщение перед rich-меню', error=str(delete_error))
                    return False
            await _send_rich_menu(bot, chat_id, rich_html, keyboard, language)
        return True
    except TelegramForbiddenError:
        logger.warning('Не удалось показать rich-меню: бот заблокирован пользователем', chat_id=chat_id)
        return True
    except (TelegramNotFound, TelegramBadRequest) as error:
        if 'message is not modified' in str(error).lower():
            mark_rich_menu_taken_over(chat_id)
            return True
        if _is_rich_date_error(error) and is_editable_as_rich:
            # Та же страховка, что и в _send_rich_menu: дата вне диапазона роняет
            # всё сообщение, поэтому повторяем один раз без tg-time.
            logger.warning('Сервер отклонил дату в rich-меню — правка без tg-time', error=str(error))
            try:
                await bot(
                    EditMessageText(
                        chat_id=chat_id,
                        message_id=message.message_id,
                        rich_message=_input_rich_message(_strip_tg_time(rich_html), language),
                        reply_markup=keyboard,
                        parse_mode=None,
                    )
                )
                mark_rich_menu_taken_over(chat_id)
                return True
            except TelegramBadRequest as retry_error:
                logger.warning('Повтор rich-меню без tg-time не удался', error=str(retry_error))
                return False
        if _looks_like_unsupported(error):
            _mark_rich_unavailable(error)
        elif _retry_without_logo(error):
            # Логотип не скачался — единственный повтор уже без него (флаг взведён).
            return await try_edit_rich_main_menu(callback, db_user, texts, db, keyboard)
        else:
            # Правка не удалась (сообщение удалено/устарело и т.п.) — классический
            # рендер разрулит своей цепочкой фоллбеков (edit_or_answer_photo).
            logger.warning(
                'Не удалось отредактировать rich-меню, фоллбек на классику', error=str(error), chat_id=chat_id
            )
        return False
    except TelegramNetworkError as error:
        logger.warning('Сетевая ошибка при показе rich-меню', error=str(error), chat_id=chat_id)
        return False
