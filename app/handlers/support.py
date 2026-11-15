import re

import structlog
from aiogram import Dispatcher, F, types
from aiogram.filters import Command

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_support_keyboard
from app.localization.texts import get_texts
from app.services.support_settings_service import SupportSettingsService
from app.utils.admin_ui import ADMIN_ICONS as _UI_ICONS, admin_heading, admin_icon, admin_quote
from app.utils.button_emoji import make_button, parse_button_label
from app.utils.miniapp_buttons import strip_leading_emoji
from app.utils.photo_message import edit_or_answer_photo
from app.utils.text_compact import compact_blank_lines


logger = structlog.get_logger(__name__)

# Ведущие украшения локалей: юникод-эмодзи, селекторы, стрелки «←».
_LEADING_GLYPHS_RE = re.compile(r'^[\s\u200b\u200d\ufe0f\u20e3←⬅→➡◀▶►‹›«»•·●○◉◎▪▫■□◆◇]+')
_HTML_TAG_RE = re.compile(r'</?[a-zA-Z][^>]*>')
# Блочная разметка: если она уже есть, свою цитату не добавляем (иначе вложенность).
_BLOCK_TAG_RE = re.compile(r'<(?:blockquote|pre)\b', re.IGNORECASE)

# Заголовок экрана поддержки: та же premium-иконка, что и в динамических текстах.
_SUPPORT_ICON = 'tools'


def _title(raw: str) -> str:
    """Заголовок из локали без разметки, ведущего эмодзи и хвостового двоеточия.

    Иконку на экране ставит код: иначе рядом с premium-эмодзи оказывался
    юникод-глиф из локали (🤖, 🟢) и строка выглядела как двойной заголовок.
    """
    plain = parse_button_label(raw or '').text
    plain = _HTML_TAG_RE.sub('', plain)
    plain = strip_leading_emoji(plain)
    plain = _LEADING_GLYPHS_RE.sub('', plain).strip()
    return plain.strip(':').strip()


def _support_body(body: str) -> str:
    """Тело экрана одной цитатой, если своих блоков в тексте ещё нет."""
    body = compact_blank_lines(body)
    if not body or _BLOCK_TAG_RE.search(body):
        return body
    return admin_quote(body)


def _support_heading(texts) -> str:
    return admin_heading(_SUPPORT_ICON, _title(texts.MENU_SUPPORT))


def _split_heading(body: str) -> tuple[str | None, str]:
    """Отделяет первую строку, если это уже готовый <b>-заголовок.

    Содержание админского текста не переписываем: свой заголовок оставляем
    как есть и лишь добавляем premium-иконку, если её там нет.
    """
    first, _, rest = body.partition('\n')
    if '<b>' not in first:
        return None, body
    if first.lstrip().startswith('<tg-emoji'):
        return first.strip(), rest
    return f'{admin_icon(_SUPPORT_ICON)} {_LEADING_GLYPHS_RE.sub("", first).strip()}', rest


def _support_caption(texts, info_text: str) -> str:
    """Экран поддержки: premium-заголовок + тело цитатой.

    Описание редактируется админом и часто уже начинается со своего <b>-заголовка
    — второй заголовок ему не добавляем, только иконку. Текст самого описания
    (HTML, списки, ссылки) сохраняется без изменений.
    """
    body = compact_blank_lines(info_text or '')
    if not body:
        return _support_heading(texts)

    heading, rest = _split_heading(body)
    if heading is None:
        heading, rest = _support_heading(texts), body

    rest = _support_body(rest)
    return compact_blank_lines(f'{heading}\n\n{rest}') if rest else heading


def _payment_caption(texts) -> str:
    """Экран «Помощь с оплатой» в том же стиле: заголовок, тело, контакты."""
    raw = texts.t('PAYMENT_SUPPORT_INFO')
    text = compact_blank_lines(raw if isinstance(raw, str) else '')
    icon = admin_icon('money')
    if not text:
        return admin_heading('money', _title(texts.t('PAYMENT_SUPPORT_INFO_TITLE', 'Помощь с оплатой')))

    first, _, rest = text.partition('\n')
    first = _LEADING_GLYPHS_RE.sub('', first).strip()
    if '<b>' in first:
        heading_first = first if first.startswith('<tg-emoji') else f'{icon} {first}'
        return compact_blank_lines(f'{heading_first}\n\n{rest}')
    return compact_blank_lines(f'{icon} {text}')


async def show_support_info(callback: types.CallbackQuery, db_user: User):
    texts = get_texts(db_user.language)
    support_info = SupportSettingsService.get_support_info_text(db_user.language)
    await edit_or_answer_photo(
        callback=callback,
        caption=_support_caption(texts, support_info),
        keyboard=get_support_keyboard(db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_support_info, F.data == 'menu_support', flags={'support_access': True})


async def payment_support_command(message: types.Message, db_user: User | None = None):
    language = db_user.language if db_user else (message.from_user.language_code or settings.DEFAULT_LANGUAGE)
    language = 'ua' if language == 'uk' else language
    texts = get_texts(language)
    rows = []
    if db_user and db_user.status == 'active' and SupportSettingsService.is_tickets_enabled():
        rows.extend(
            [
                [
                    make_button(
                        text=texts.t('CREATE_TICKET_BUTTON', 'Создать тикет'),
                        callback_data='payment_support_create',
                        style='success',
                        icon_custom_emoji_id=_UI_ICONS['plus'].emoji_id,
                    )
                ],
                [
                    make_button(
                        text=texts.t('MY_TICKETS_BUTTON', 'Мои тикеты'),
                        callback_data='my_tickets',
                        style='primary',
                        icon_custom_emoji_id=_UI_ICONS['chat'].emoji_id,
                    )
                ],
            ]
        )
    # Keep a contact route even when tickets/account access is blocked or the menu is hidden.
    contact = settings.get_support_contact_url()
    if contact:
        rows.append(
            [
                make_button(
                    text=texts.t('CONTACT_SUPPORT_BUTTON', 'Связаться с поддержкой'),
                    url=contact,
                    style='primary',
                    icon_custom_emoji_id=_UI_ICONS['phone'].emoji_id,
                )
            ]
        )
    text = _payment_caption(texts)
    if not rows:
        text = compact_blank_lines(
            f'{text}\n\n{admin_quote(compact_blank_lines(SupportSettingsService.get_support_info_text(language)))}'
        )
    await message.answer(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
        parse_mode='HTML',
    )


def register_command_handlers(dp: Dispatcher):
    # Register before purchase/registration FSM handlers. Old payments still need help
    # after new Stars payments have been disabled; only menu visibility is conditional.
    dp.message.register(
        payment_support_command, Command('paysupport'), F.chat.type == 'private', flags={'public_payment_support': True}
    )
