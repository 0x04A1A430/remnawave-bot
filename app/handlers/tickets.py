import asyncio
import html
import re
import time

import structlog
from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InaccessibleMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.ticket import TicketCRUD, TicketMessageCRUD
from app.database.crud.user import get_user_by_id
from app.database.models import Ticket, TicketStatus, User
from app.keyboards.inline import (
    get_my_tickets_keyboard,
    get_ticket_cancel_keyboard,
    get_ticket_notification_keyboard,
    get_ticket_reply_cancel_keyboard,
    get_ticket_view_keyboard,
)
from app.localization.texts import get_texts
from app.services.admin_notification_service import AdminNotificationService
from app.utils.admin_ui import ADMIN_ICONS as _UI_ICONS, admin_heading, admin_icon, admin_quote
from app.utils.button_emoji import make_button, parse_button_label
from app.utils.cache import RateLimitCache, cache, cache_key
from app.utils.formatters import format_username_link
from app.utils.miniapp_buttons import build_admin_ticket_cabinet_button, strip_leading_emoji
from app.utils.photo_message import edit_or_answer_photo, safe_edit_or_resend
from app.utils.text_compact import compact_blank_lines
from app.utils.ticket_media import get_ticket_media, ticket_media_chunks
from app.utils.ticket_text import (
    TICKET_MESSAGE_MAX_LENGTH,
    TICKET_PAGE_MAX_LEN,
    build_ticket_pages,
    preview_text,
)
from app.utils.timezone import format_local_datetime


logger = structlog.get_logger(__name__)

# Ведущие украшения локалей: юникод-эмодзи, селекторы, стрелки «←».
_LEADING_GLYPHS_RE = re.compile(r'^[\s\u200b\u200d\ufe0f\u20e3←⬅→➡◀▶►‹›«»•·●○◉◎▪▫■□◆◇]+')
_HTML_TAG_RE = re.compile(r'</?[a-zA-Z][^>]*>')


def _title(raw: str) -> str:
    """Заголовок из локали без разметки, ведущего эмодзи и хвостового двоеточия.

    Иконку на экране ставит код: иначе рядом с premium-эмодзи оказывался
    юникод-глиф из локали (🟢, 🤖) и строка выглядела как двойной заголовок.
    """
    plain = parse_button_label(raw or '').text
    plain = _HTML_TAG_RE.sub('', plain)
    plain = strip_leading_emoji(plain)
    plain = _LEADING_GLYPHS_RE.sub('', plain).strip()
    return plain.strip(':').strip()


def _heading(icon_name: str, raw_title: str) -> str:
    """Иконка + жирный заголовок — как в остальном пользовательском меню."""
    title = _title(raw_title)
    return admin_heading(icon_name, title) if title else admin_icon(icon_name)


def _screen(
    icon_name: str,
    raw_title: str,
    *,
    quote: list[str] | tuple[str, ...] = (),
    body: str = '',
) -> str:
    """Экран из блоков: заголовок, цитата с параметрами, текст."""
    parts: list[str] = [_heading(icon_name, raw_title)]
    quote_lines = [line for line in quote if line]
    if quote_lines:
        parts.append(admin_quote('\n'.join(quote_lines)))
    if body:
        parts.append(body)
    return compact_blank_lines('\n\n'.join(parts))


def _alert(icon_name: str, message: str, *, title: str = '') -> str:
    """Ошибка/лимит: заголовок экрана + причина в цитате.

    Без заголовка экран терминальный (тикет закрыт, ответ отправлен) — тогда
    это одна строка «иконка + жирный текст», как в остальном меню.
    """
    if title:
        return _screen(icon_name, title, body=admin_quote(message))
    return _heading(icon_name, message)


_ARROW_PREFIXES = ('←', '⬅', '→', '➡', '↩', '⟵')


def _btn_text(raw: str) -> str:
    """Подпись кнопки без юникод-эмодзи: свой значок ставится через icon_custom_emoji_id.

    Стрелку в начале («← Назад») оставляем — она навигационная, а не эмодзи,
    хотя strip_leading_emoji считает её украшением.
    """
    text = parse_button_label(raw or '').text.replace('\ufe0f', '').lstrip()
    for arrow in _ARROW_PREFIXES:
        if text.startswith(arrow):
            return f'{arrow} {text[len(arrow) :].strip()}'.strip()
    return strip_leading_emoji(text).strip()


def _plain(raw: str) -> str:
    """Текст для alert'ов: Telegram показывает их без parse_mode, теги — мусор."""
    return html.unescape(_HTML_TAG_RE.sub('', raw or '')).strip()


class TicketStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_message = State()
    waiting_for_reply = State()


async def show_ticket_priority_selection(
    callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession, *, payment_support: bool = False
):
    """Начать создание тикета без выбора приоритета: сразу просим заголовок"""
    texts = get_texts(db_user.language)

    # Глобальный блок и наличие активного тикета
    from app.database.crud.ticket import TicketCRUD

    blocked_until = await TicketCRUD.is_user_globally_blocked(db, db_user.id)
    if blocked_until:
        if blocked_until.year > 9999 - 1:
            await callback.answer(
                _plain(texts.t('USER_BLOCKED_FOREVER', 'Вы заблокированы для обращений в поддержку.')), show_alert=True
            )
        else:
            await callback.answer(
                _plain(
                    texts.t('USER_BLOCKED_UNTIL', 'Вы заблокированы до {time}').format(
                        time=blocked_until.strftime('%d.%m.%Y %H:%M')
                    )
                ),
                show_alert=True,
            )
        return
    if await TicketCRUD.user_has_active_ticket(db, db_user.id):
        await callback.answer(
            _plain(texts.t('TICKET_ALREADY_OPEN', 'У вас уже есть незакрытый тикет. Сначала закройте его.')),
            show_alert=True,
        )
        return

    if payment_support:
        prompt_text = _screen(
            'money',
            texts.t('PAYMENT_SUPPORT_TITLE', 'Проблема с оплатой'),
            body=texts.t(
                'PAYMENT_SUPPORT_PROMPT',
                'Опишите проблему с оплатой: укажите дату и сумму. Можно приложить фото или видео.',
            ),
        )
    else:
        prompt_text = _screen(
            'plus',
            texts.t('CREATE_TICKET_BUTTON', 'Создать тикет'),
            quote=[texts.t('TICKET_TITLE_INPUT', 'Введите заголовок тикета:')],
        )
    cancel_kb = get_ticket_cancel_keyboard(db_user.language)
    prompt_msg = callback.message
    try:
        await callback.message.edit_text(prompt_text, reply_markup=cancel_kb, parse_mode='HTML')
    except TelegramBadRequest:
        # Предыдущее сообщение — фото (нет текста для edit_text), удаляем и шлём новое
        try:
            await callback.message.delete()
        except Exception:
            pass
        prompt_msg = await callback.message.answer(prompt_text, reply_markup=cancel_kb, parse_mode='HTML')
    # Запоминаем исходное сообщение бота, чтобы далее редактировать его, а не слать новые
    await state.update_data(
        prompt_chat_id=prompt_msg.chat.id, prompt_message_id=prompt_msg.message_id, payment_support=payment_support
    )
    if payment_support:
        await state.update_data(title=texts.t('PAYMENT_SUPPORT_TITLE')[:255])
        await state.set_state(TicketStates.waiting_for_message)
    else:
        await state.set_state(TicketStates.waiting_for_title)
    await callback.answer()


async def start_payment_ticket(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    from app.services.support_settings_service import SupportSettingsService

    if not SupportSettingsService.is_tickets_enabled():
        await callback.answer()
        from app.handlers.support import payment_support_command

        await payment_support_command(callback.message, db_user=db_user)
        return
    await show_ticket_priority_selection(callback, state, db_user, db, payment_support=True)


async def handle_ticket_title_input(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    # Проверяем, что пользователь в правильном состоянии
    current_state = await state.get_state()
    if current_state != TicketStates.waiting_for_title:
        return

    """Обработать ввод заголовка тикета"""
    if not message.text:
        asyncio.create_task(_try_delete_message_later(message.bot, message.chat.id, message.message_id, 2.0))
        return
    title = message.text.strip()

    data_prompt = await state.get_data()
    prompt_chat_id = data_prompt.get('prompt_chat_id')
    prompt_message_id = data_prompt.get('prompt_message_id')
    # Удалим сообщение пользователя через 2 секунды, чтобы не засорять чат
    asyncio.create_task(_try_delete_message_later(message.bot, message.chat.id, message.message_id, 2.0))
    if len(title) < 5:
        texts = get_texts(db_user.language)
        text_val = _alert(
            'warning',
            texts.t('TICKET_TITLE_TOO_SHORT', 'Заголовок должен содержать минимум 5 символов. Попробуйте еще раз:'),
            title=texts.t('CREATE_TICKET_BUTTON', 'Создать тикет'),
        )
        await _edit_or_send(message, prompt_chat_id, prompt_message_id, text_val, db_user.language)
        return

    if len(title) > 255:
        texts = get_texts(db_user.language)
        text_val = _alert(
            'warning',
            texts.t('TICKET_TITLE_TOO_LONG', 'Заголовок слишком длинный. Максимум 255 символов. Попробуйте еще раз:'),
            title=texts.t('CREATE_TICKET_BUTTON', 'Создать тикет'),
        )
        await _edit_or_send(message, prompt_chat_id, prompt_message_id, text_val, db_user.language)
        return

    # Глобальный блок
    from app.database.crud.ticket import TicketCRUD

    blocked_until = await TicketCRUD.is_user_globally_blocked(db, db_user.id)
    if blocked_until:
        texts = get_texts(db_user.language)
        if blocked_until.year > 9999 - 1:
            blocked_text = texts.t('USER_BLOCKED_FOREVER', 'Вы заблокированы для обращений в поддержку.')
        else:
            blocked_text = texts.t('USER_BLOCKED_UNTIL', 'Вы заблокированы до {time}').format(
                time=blocked_until.strftime('%d.%m.%Y %H:%M')
            )
        await message.answer(_alert('lock', blocked_text), parse_mode='HTML')
        await state.clear()
        return

    await state.update_data(title=title)

    texts = get_texts(db_user.language)
    text_val = _screen(
        'plus',
        texts.t('CREATE_TICKET_BUTTON', 'Создать тикет'),
        quote=[f'{admin_icon("document")} {html.escape(title)}'],
        body=texts.t(
            'TICKET_MESSAGE_INPUT', 'Опишите проблему (до {limit} символов) или отправьте фото с подписью:'
        ).format(limit=TICKET_MESSAGE_MAX_LENGTH),
    )
    await _edit_or_send(message, prompt_chat_id, prompt_message_id, text_val, db_user.language)

    await state.set_state(TicketStates.waiting_for_message)


async def handle_ticket_message_input(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    # Проверяем, что пользователь в правильном состоянии
    current_state = await state.get_state()
    if current_state != TicketStates.waiting_for_message:
        return

    if (await state.get_data()).get('payment_support'):
        from app.services.support_settings_service import SupportSettingsService

        if not SupportSettingsService.is_tickets_enabled():
            from app.handlers.support import payment_support_command

            await state.clear()
            await payment_support_command(message, db_user=db_user)
            return

    # Защита от спама: принимаем только первое сообщение в коротком окне
    try:
        # Глобальный мягкий супрессор на 6 секунд после создания тикета
        try:
            from_cache = await cache.get(cache_key('suppress_user_input', db_user.id))
            if from_cache:
                asyncio.create_task(_try_delete_message_later(message.bot, message.chat.id, message.message_id, 2.0))
                return
        except Exception:
            pass
        limited = await RateLimitCache.is_rate_limited(db_user.id, 'ticket_create_message', limit=1, window=2)
        if limited:
            # Удаляем лишние части длинного сообщения
            try:
                asyncio.create_task(_try_delete_message_later(message.bot, message.chat.id, message.message_id, 2.0))
            except Exception as exc:
                logger.debug('Антиспам тикетов: сообщение не поставлено на удаление', error=str(exc))
            return
    except Exception:
        pass
    try:
        data_rl = await state.get_data()
        last_ts = data_rl.get('rl_ts_create')
        now_ts = time.time()
        if last_ts and (now_ts - float(last_ts)) < 2:
            try:
                asyncio.create_task(_try_delete_message_later(message.bot, message.chat.id, message.message_id, 2.0))
            except Exception as exc:
                logger.debug('Антиспам тикетов: сообщение не поставлено на удаление', error=str(exc))
            return
        await state.update_data(rl_ts_create=now_ts)
    except Exception:
        pass

    """Обработать ввод сообщения тикета и создать тикет"""
    # Сохраняем вложение отдельно от подписи, в том числе видео без текста.
    message_text = (message.text or message.caption or '').strip()
    media_type = None
    media_file_id = None
    media_caption = None
    if message.photo:
        media_type = 'photo'
        media_file_id = message.photo[-1].file_id
        media_caption = message.caption
    elif message.video:
        media_type = 'video'
        media_file_id = message.video.file_id
        media_caption = message.caption
    # Глобальный блок
    from app.database.crud.ticket import TicketCRUD

    blocked_until = await TicketCRUD.is_user_globally_blocked(db, db_user.id)
    if blocked_until:
        texts = get_texts(db_user.language)
        data_prompt = await state.get_data()
        prompt_chat_id = data_prompt.get('prompt_chat_id')
        prompt_message_id = data_prompt.get('prompt_message_id')
        blocked_text = (
            texts.t('USER_BLOCKED_FOREVER', 'Вы заблокированы для обращений в поддержку.')
            if blocked_until.year > 9999 - 1
            else texts.t('USER_BLOCKED_UNTIL', 'Вы заблокированы до {time}').format(
                time=blocked_until.strftime('%d.%m.%Y %H:%M')
            )
        )
        text_msg = _alert('lock', blocked_text)
        if prompt_chat_id and prompt_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=prompt_chat_id, message_id=prompt_message_id, text=text_msg, parse_mode='HTML'
                )
            except TelegramBadRequest:
                await message.answer(text_msg, parse_mode='HTML')
        else:
            await message.answer(text_msg, parse_mode='HTML')
        await state.clear()
        return

    # Удалим сообщение пользователя через 2 секунды
    asyncio.create_task(_try_delete_message_later(message.bot, message.chat.id, message.message_id, 2.0))
    # Вложение может описывать проблему без текстовой подписи.
    if len(message_text) < 10 and not media_file_id:
        texts = get_texts(db_user.language)
        data_prompt = await state.get_data()
        prompt_chat_id = data_prompt.get('prompt_chat_id')
        prompt_message_id = data_prompt.get('prompt_message_id')
        err_text = _alert(
            'warning',
            texts.t(
                'TICKET_MESSAGE_TOO_SHORT', 'Сообщение слишком короткое. Опишите проблему подробнее или отправьте фото:'
            ),
            title=texts.t('CREATE_TICKET_BUTTON', 'Создать тикет'),
        )
        await _edit_or_send(message, prompt_chat_id, prompt_message_id, err_text, db_user.language)
        return

    # Раньше текст молча резался до 500 символов — пользователь и поддержка
    # теряли остаток. Теперь просим сократить, ничего не отбрасывая втихую.
    if len(message_text) > TICKET_MESSAGE_MAX_LENGTH:
        texts = get_texts(db_user.language)
        data_prompt = await state.get_data()
        prompt_chat_id = data_prompt.get('prompt_chat_id')
        prompt_message_id = data_prompt.get('prompt_message_id')
        err_text = _alert(
            'warning',
            texts.t(
                'TICKET_MESSAGE_TOO_LONG',
                'Сообщение слишком длинное. Максимум {limit} символов. Сократите текст и отправьте еще раз:',
            ).format(limit=TICKET_MESSAGE_MAX_LENGTH),
            title=texts.t('CREATE_TICKET_BUTTON', 'Создать тикет'),
        )
        await _edit_or_send(message, prompt_chat_id, prompt_message_id, err_text, db_user.language)
        return

    data = await state.get_data()
    title = data.get('title')
    priority = 'normal'

    try:
        ticket = await TicketCRUD.create_ticket(
            db,
            db_user.id,
            title,
            message_text,
            priority,
            media_type=media_type,
            media_file_id=media_file_id,
            media_caption=media_caption,
        )
        # Включим временное подавление лишних сообщений пользователя (на случай разбиения длинного текста)
        try:
            await cache.set(cache_key('suppress_user_input', db_user.id), True, 6)
        except Exception:
            pass

        texts = get_texts(db_user.language)
        # Ограничим длину подтверждения чтобы не упереться в лимиты
        safe_title = html.escape(title if len(title) <= 200 else (title[:197] + '...'))
        detail_lines = [
            f'{admin_icon("document")} Заголовок: {safe_title}',
            f'{admin_icon("tag")} Статус: {texts.t("TICKET_STATUS_OPEN", "Открыт")}',
            f'{admin_icon("calendar")} Создан: {format_local_datetime(ticket.created_at, "%d.%m.%Y %H:%M")}',
        ]
        if media_file_id:
            detail_lines.append(f'{admin_icon("image")} Вложение: {"видео" if media_type == "video" else "фото"}')
        creation_text = _screen('success', f'Тикет #{ticket.id} создан', quote=detail_lines)

        data_prompt = await state.get_data()
        prompt_chat_id = data_prompt.get('prompt_chat_id')
        prompt_message_id = data_prompt.get('prompt_message_id')
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    make_button(
                        text=_btn_text(
                            texts.t(
                                'VIEW_TICKET',
                                '<tg-emoji emoji-id="5960714428394507968">👁</tg-emoji> Посмотреть тикет',
                            )
                        ),
                        callback_data=f'view_ticket_{ticket.id}',
                        style='primary',
                        icon_custom_emoji_id=_UI_ICONS['eye'].emoji_id,
                    )
                ],
                [
                    make_button(
                        text=texts.t('BACK_TO_MENU', '← В главное меню'),
                        callback_data='back_to_menu',
                        style='danger',
                        icon_custom_emoji_id=_UI_ICONS['back'].emoji_id,
                    )
                ],
            ]
        )
        if prompt_chat_id and prompt_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=prompt_chat_id,
                    message_id=prompt_message_id,
                    text=creation_text,
                    reply_markup=keyboard,
                    parse_mode='HTML',
                )
            except TelegramBadRequest:
                await message.answer(creation_text, reply_markup=keyboard, parse_mode='HTML')
        else:
            await message.answer(creation_text, reply_markup=keyboard, parse_mode='HTML')

        await state.clear()

        # Уведомить админов
        await notify_admins_about_new_ticket(ticket, db)

    except Exception as e:
        logger.error('Error creating ticket', error=e)
        texts = get_texts(db_user.language)
        await message.answer(
            _alert(
                'cross',
                texts.t(
                    'TICKET_CREATE_ERROR',
                    '<tg-emoji emoji-id="5778527486270770928">❌</tg-emoji> Произошла ошибка при создании тикета. Попробуйте позже.',
                ),
            ),
            parse_mode='HTML',
        )


async def show_my_tickets(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)

    # Определяем текущую страницу
    current_page = 1
    if callback.data.startswith('my_tickets_page_'):
        try:
            current_page = int(callback.data.replace('my_tickets_page_', ''))
        except ValueError:
            current_page = 1

    # Пагинация открытых тикетов из БД
    per_page = 10
    total_open = await TicketCRUD.count_user_tickets_by_statuses(
        db, db_user.id, [TicketStatus.OPEN.value, TicketStatus.ANSWERED.value, TicketStatus.PENDING.value]
    )
    total_pages = max(1, (total_open + per_page - 1) // per_page)
    current_page = max(1, min(current_page, total_pages))
    offset = (current_page - 1) * per_page
    open_tickets = await TicketCRUD.get_user_tickets_by_statuses(
        db,
        db_user.id,
        [TicketStatus.OPEN.value, TicketStatus.ANSWERED.value, TicketStatus.PENDING.value],
        limit=per_page,
        offset=offset,
    )

    # Проверка на отсутствие тикетов совсем (ни открытых, ни закрытых)
    has_closed_any = await TicketCRUD.count_user_tickets_by_statuses(db, db_user.id, [TicketStatus.CLOSED.value]) > 0
    if not open_tickets and not has_closed_any:
        empty_text = _screen('chat', texts.t('NO_TICKETS', 'У вас пока нет тикетов.'))
        await callback.message.edit_text(
            empty_text,
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        make_button(
                            text=texts.t('CREATE_TICKET_BUTTON', 'Создать тикет'),
                            callback_data='create_ticket',
                            style='success',
                            icon_custom_emoji_id=_UI_ICONS['plus'].emoji_id,
                        )
                    ],
                    [
                        make_button(
                            text=_btn_text(texts.t('VIEW_CLOSED_TICKETS', 'Закрытые тикеты')),
                            callback_data='my_tickets_closed',
                            icon_custom_emoji_id=_UI_ICONS['lock'].emoji_id,
                        )
                    ],
                    [
                        make_button(
                            text=texts.BACK,
                            callback_data='menu_support',
                            style='danger',
                            icon_custom_emoji_id=_UI_ICONS['back'].emoji_id,
                        )
                    ],
                ]
            ),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    # Открытые с пагинацией (DB)
    open_data = [{'id': t.id, 'title': t.title, 'status_emoji': t.status_emoji} for t in open_tickets]
    keyboard = get_my_tickets_keyboard(
        open_data,
        current_page=current_page,
        total_pages=total_pages,
        language=db_user.language,
        page_prefix='my_tickets_page_',
    )
    # Добавим кнопку перехода к закрытым
    keyboard.inline_keyboard.insert(
        0,
        [
            make_button(
                text=_btn_text(texts.t('VIEW_CLOSED_TICKETS', 'Закрытые тикеты')),
                callback_data='my_tickets_closed',
                icon_custom_emoji_id=_UI_ICONS['lock'].emoji_id,
            )
        ],
    )
    # Всегда используем фото-рендер с логотипом (утилита сама сделает фоллбек при необходимости)
    caption = _screen(
        'chat',
        texts.t('MY_TICKETS_TITLE', 'Ваши тикеты:'),
        quote=[f'{admin_icon("chat")} {texts.t("OPEN_TICKETS", "Открытые")}: {total_open}'],
    )
    await edit_or_answer_photo(
        callback=callback,
        caption=caption,
        keyboard=keyboard,
        parse_mode='HTML',
    )
    await callback.answer()


async def show_my_tickets_closed(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    # Пагинация закрытых
    current_page = 1
    data_str = callback.data
    if data_str.startswith('my_tickets_closed_page_'):
        try:
            current_page = int(data_str.replace('my_tickets_closed_page_', ''))
        except ValueError:
            current_page = 1

    per_page = 10
    total_closed = await TicketCRUD.count_user_tickets_by_statuses(db, db_user.id, [TicketStatus.CLOSED.value])
    if total_closed == 0:
        await callback.message.edit_text(
            _screen('lock', texts.t('NO_CLOSED_TICKETS', 'Закрытых тикетов пока нет.')),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        make_button(
                            text=texts.t('BACK_TO_OPEN_TICKETS', '← Открытые тикеты'),
                            callback_data='my_tickets',
                            style='primary',
                            icon_custom_emoji_id=_UI_ICONS['chat'].emoji_id,
                        )
                    ],
                    [
                        make_button(
                            text=texts.BACK,
                            callback_data='menu_support',
                            style='danger',
                            icon_custom_emoji_id=_UI_ICONS['back'].emoji_id,
                        )
                    ],
                ]
            ),
            parse_mode='HTML',
        )
        await callback.answer()
        return
    total_pages = max(1, (total_closed + per_page - 1) // per_page)
    current_page = max(1, min(current_page, total_pages))
    offset = (current_page - 1) * per_page
    tickets = await TicketCRUD.get_user_tickets_by_statuses(
        db, db_user.id, [TicketStatus.CLOSED.value], limit=per_page, offset=offset
    )
    data = [{'id': t.id, 'title': t.title, 'status_emoji': t.status_emoji} for t in tickets]
    kb = get_my_tickets_keyboard(
        data,
        current_page=current_page,
        total_pages=total_pages,
        language=db_user.language,
        page_prefix='my_tickets_closed_page_',
    )
    kb.inline_keyboard.insert(
        0,
        [
            make_button(
                text=texts.t('BACK_TO_OPEN_TICKETS', '← Открытые тикеты'),
                callback_data='my_tickets',
                style='primary',
                icon_custom_emoji_id=_UI_ICONS['chat'].emoji_id,
            )
        ],
    )
    await edit_or_answer_photo(
        callback=callback,
        caption=_screen(
            'lock',
            texts.t('CLOSED_TICKETS_TITLE', 'Закрытые тикеты:'),
            quote=[f'{admin_icon("lock")} {_title(texts.t("CLOSED_TICKETS", "Закрытые"))}: {total_closed}'],
        ),
        keyboard=kb,
        parse_mode='HTML',
    )
    await callback.answer()


async def view_ticket(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Показать детали тикета с пагинацией"""
    data_str = callback.data
    # None — страница не задана: открываем последнюю, где лежат свежие сообщения
    page = None
    ticket_id = None
    if data_str.startswith('ticket_view_page_'):
        # format: ticket_view_page_{ticket_id}_{page}
        try:
            _, _, _, tid, p = data_str.split('_')
            ticket_id = int(tid)
            page = max(1, int(p))
        except Exception:
            pass
    if ticket_id is None:
        ticket_id = int(data_str.replace('view_ticket_', ''))

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True)

    if not ticket or ticket.user_id != db_user.id:
        texts = get_texts(db_user.language)
        await callback.answer(_plain(texts.t('TICKET_NOT_FOUND', 'Тикет не найден.')), show_alert=True)
        return

    texts = get_texts(db_user.language)

    # Формируем текст тикета
    status_text = {
        TicketStatus.OPEN.value: texts.t('TICKET_STATUS_OPEN', 'Открыт'),
        TicketStatus.ANSWERED.value: texts.t('TICKET_STATUS_ANSWERED', 'Отвечен'),
        TicketStatus.CLOSED.value: texts.t('TICKET_STATUS_CLOSED', 'Закрыт'),
        TicketStatus.PENDING.value: texts.t('TICKET_STATUS_PENDING', 'В ожидании'),
    }.get(ticket.status, ticket.status)

    header = _screen(
        'chat',
        f'Тикет #{ticket.id}',
        quote=[
            f'{admin_icon("document")} Заголовок: {html.escape(ticket.title or "")}',
            f'{admin_icon("tag")} Статус: {status_text}',
            f'{admin_icon("calendar")} Создан: {format_local_datetime(ticket.created_at, "%d.%m.%Y %H:%M")}',
        ],
    )
    header += '\n\n'
    message_blocks: list[str] = []
    if ticket.messages:
        message_blocks.append(f'{admin_icon("chat")} <b>Сообщения ({len(ticket.messages)})</b>\n\n')
        for msg in ticket.messages:
            sender = (
                f'{admin_icon("user")} <b>Вы</b>' if msg.is_user_message else f'{admin_icon("tools")} <b>Поддержка</b>'
            )
            block = f'{sender} · {format_local_datetime(msg.created_at, "%d.%m %H:%M")}\n{html.escape(msg.message_text or "")}\n\n'
            for item in get_ticket_media(msg):
                label = {'photo': 'фото', 'video': 'видео', 'document': 'файл'}[item['type']]
                block += f'{admin_icon("image")} Вложение: {label}\n\n'
            message_blocks.append(block)
    pages = build_ticket_pages(header, message_blocks, max_len=TICKET_PAGE_MAX_LEN)
    total_pages = len(pages)
    page = total_pages if page is None else min(page, total_pages)

    keyboard = get_ticket_view_keyboard(
        ticket_id,
        ticket.is_closed,
        db_user.language,
    )
    # Если есть фото или видео — добавим кнопку для просмотра
    has_attachments = any(get_ticket_media(m) for m in ticket.messages or [])
    if has_attachments:
        try:
            keyboard.inline_keyboard.insert(
                0,
                [
                    make_button(
                        text=texts.t(
                            'TICKET_ATTACHMENTS', '<tg-emoji emoji-id="5877495434124988415">📎</tg-emoji> Вложения'
                        ),
                        callback_data=f'ticket_attachments_{ticket_id}',
                        icon_custom_emoji_id=_UI_ICONS['image'].emoji_id,
                    )
                ],
            )
        except Exception:
            pass
    # Пагинация
    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(
                make_button(
                    text='<tg-emoji emoji-id="5875082500023258804">⬅</tg-emoji>',
                    callback_data=f'ticket_view_page_{ticket_id}_{page - 1}',
                    style='primary',
                    icon_custom_emoji_id=_UI_ICONS['arrow_left'].emoji_id,
                )
            )
        nav_row.append(make_button(text=f'{page}/{total_pages}', callback_data='noop'))
        if page < total_pages:
            nav_row.append(
                make_button(
                    text='<tg-emoji emoji-id="5877468380125990242">➡</tg-emoji>',
                    callback_data=f'ticket_view_page_{ticket_id}_{page + 1}',
                    style='primary',
                    icon_custom_emoji_id=_UI_ICONS['arrow_right'].emoji_id,
                )
            )
        if getattr(keyboard, 'inline_keyboard', None) is not None:
            keyboard.inline_keyboard.insert(0, nav_row)
    # Показываем как текст (чтобы не упереться в caption лимит)
    page_text = compact_blank_lines(pages[page - 1])
    await safe_edit_or_resend(callback.message, page_text, keyboard)
    await callback.answer()


async def send_ticket_attachments(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    try:
        await callback.answer(
            texts.t(
                'SENDING_ATTACHMENTS', '<tg-emoji emoji-id="5877495434124988415">📎</tg-emoji> Отправляю вложения...'
            )
        )
    except Exception:
        pass
    try:
        ticket_id = int(callback.data.replace('ticket_attachments_', ''))
    except ValueError:
        await callback.answer(_plain(texts.t('TICKET_NOT_FOUND', 'Тикет не найден.')), show_alert=True)
        return

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True)
    if not ticket or ticket.user_id != db_user.id:
        await callback.answer(_plain(texts.t('TICKET_NOT_FOUND', 'Тикет не найден.')), show_alert=True)
        return

    attachments = [item for message in ticket.messages for item in get_ticket_media(message)]
    if not attachments:
        await callback.answer(_plain(texts.t('NO_ATTACHMENTS', 'Вложений нет.')), show_alert=True)
        return

    # Telegram ограничивает media group до 10 элементов. Отправим чанками.
    from aiogram.types import InputMediaDocument, InputMediaPhoto, InputMediaVideo

    chunks = ticket_media_chunks(attachments)
    last_group_message = None
    for chunk in chunks:
        media = [
            {'photo': InputMediaPhoto, 'video': InputMediaVideo, 'document': InputMediaDocument}[m['type']](
                media=m['file_id']
            )
            for m in chunk
        ]
        try:
            if len(media) == 1:
                item = media[0]
                sender = getattr(callback.message.bot, f'send_{item.type.value}')
                messages = [await sender(chat_id=callback.from_user.id, **{item.type: item.media})]
            else:
                messages = await callback.message.bot.send_media_group(chat_id=callback.from_user.id, media=media)
            if messages:
                last_group_message = messages[-1]
        except Exception:
            pass
    if last_group_message:
        try:
            kb = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        make_button(
                            text=texts.t(
                                'DELETE_MESSAGE', '<tg-emoji emoji-id="5879896690210639947">🗑</tg-emoji> Удалить'
                            ),
                            callback_data=f'user_delete_message_{last_group_message.message_id}',
                            style='danger',
                            icon_custom_emoji_id=_UI_ICONS['delete'].emoji_id,
                        )
                    ]
                ]
            )
            await callback.message.bot.send_message(
                chat_id=callback.from_user.id,
                text=_screen('image', texts.t('ATTACHMENTS_SENT', 'Вложения отправлены.')),
                reply_markup=kb,
                parse_mode='HTML',
            )
        except Exception:
            pass
    else:
        try:
            await callback.answer(_plain(texts.t('ATTACHMENTS_SENT', 'Вложения отправлены.')))
        except Exception:
            pass


async def user_delete_message(callback: types.CallbackQuery):
    try:
        msg_id = int(callback.data.replace('user_delete_message_', ''))
    except ValueError:
        await callback.answer('❌')
        return
    try:
        await callback.message.bot.delete_message(chat_id=callback.from_user.id, message_id=msg_id)
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer('✅')


async def _edit_or_send(
    message: types.Message,
    chat_id: int | None,
    message_id: int | None,
    text: str,
    language: str,
) -> None:
    """Попытаться отредактировать prompt-сообщение, при неудаче — отправить новое."""
    if chat_id and message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=get_ticket_cancel_keyboard(language),
                parse_mode='HTML',
            )
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, reply_markup=get_ticket_cancel_keyboard(language), parse_mode='HTML')


async def _try_delete_message_later(bot: Bot, chat_id: int, message_id: int, delay_seconds: float = 1.0):
    try:
        await asyncio.sleep(delay_seconds)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        # В приватных чатах удаление сообщений пользователя может быть недоступно — игнорируем ошибки
        pass


async def reply_to_ticket(callback: types.CallbackQuery, state: FSMContext, db_user: User):
    """Начать ответ на тикет"""
    ticket_id = int(callback.data.replace('reply_ticket_', ''))

    await state.update_data(ticket_id=ticket_id)

    texts = get_texts(db_user.language)

    prompt_text = _screen(
        'chat',
        texts.t('REPLY_TO_TICKET', 'Ответить'),
        body=texts.t('TICKET_REPLY_INPUT', 'Введите ваш ответ:'),
    )
    try:
        await callback.message.edit_text(
            prompt_text,
            reply_markup=get_ticket_reply_cancel_keyboard(db_user.language),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            prompt_text,
            reply_markup=get_ticket_reply_cancel_keyboard(db_user.language),
            parse_mode='HTML',
        )

    await state.set_state(TicketStates.waiting_for_reply)
    await callback.answer()


async def handle_ticket_reply(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    # Проверяем, что пользователь в правильном состоянии
    current_state = await state.get_state()
    if current_state != TicketStates.waiting_for_reply:
        return

    # Защита от спама: по тикету принимаем только первое сообщение в коротком окне
    try:
        data_rl = await state.get_data()
        rl_ticket_id = data_rl.get('ticket_id') or 'reply'
        limited = await RateLimitCache.is_rate_limited(db_user.id, f'ticket_reply_{rl_ticket_id}', limit=1, window=2)
        if limited:
            try:
                asyncio.create_task(_try_delete_message_later(message.bot, message.chat.id, message.message_id, 2.0))
            except Exception as exc:
                logger.debug('Антиспам тикетов: сообщение не поставлено на удаление', error=str(exc))
            return
    except Exception:
        pass
    try:
        data_rl = await state.get_data()
        last_ts = data_rl.get('rl_ts_reply')
        now_ts = time.time()
        if last_ts and (now_ts - float(last_ts)) < 2:
            try:
                asyncio.create_task(_try_delete_message_later(message.bot, message.chat.id, message.message_id, 2.0))
            except Exception as exc:
                logger.debug('Антиспам тикетов: сообщение не поставлено на удаление', error=str(exc))
            return
        await state.update_data(rl_ts_reply=now_ts)
    except Exception as exc:
        # Антиспам — вспомогательный механизм: без состояния ответ всё равно обрабатывается.
        logger.debug('Антиспам ответа на тикет: состояние не обновлено', error=str(exc))

    """Обработать ответ на тикет"""
    # Фото и видео для ответа пользователя.
    reply_text = (message.text or message.caption or '').strip()
    media_type = None
    media_file_id = None
    media_caption = None
    if message.photo:
        media_type = 'photo'
        media_file_id = message.photo[-1].file_id
        media_caption = message.caption
    elif message.video:
        media_type = 'video'
        media_file_id = message.video.file_id
        media_caption = message.caption

    if len(reply_text) < 5 and not media_file_id:
        texts = get_texts(db_user.language)
        await message.answer(
            _alert(
                'warning',
                texts.t('TICKET_REPLY_TOO_SHORT', 'Ответ должен содержать минимум 5 символов. Попробуйте еще раз:'),
                title=texts.t('REPLY_TO_TICKET', 'Ответить'),
            ),
            parse_mode='HTML',
        )
        return

    # Раньше ответ молча резался до 400 символов, и поддержка видела огрызок.
    if len(reply_text) > TICKET_MESSAGE_MAX_LENGTH:
        texts = get_texts(db_user.language)
        await message.answer(
            _alert(
                'warning',
                texts.t(
                    'TICKET_REPLY_TOO_LONG',
                    'Ответ слишком длинный. Максимум {limit} символов. Сократите текст и отправьте еще раз:',
                ).format(limit=TICKET_MESSAGE_MAX_LENGTH),
                title=texts.t('REPLY_TO_TICKET', 'Ответить'),
            ),
            parse_mode='HTML',
        )
        return

    data = await state.get_data()
    ticket_id = data.get('ticket_id')

    if not ticket_id:
        texts = get_texts(db_user.language)
        await message.answer(
            _alert('cross', texts.t('TICKET_REPLY_ERROR', 'Ошибка: не найден ID тикета.')), parse_mode='HTML'
        )
        await state.clear()
        return

    try:
        # Проверяем, что тикет принадлежит пользователю и не закрыт
        ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=False)
        if not ticket or ticket.user_id != db_user.id:
            texts = get_texts(db_user.language)
            await message.answer(_alert('search', texts.t('TICKET_NOT_FOUND', 'Тикет не найден.')), parse_mode='HTML')
            await state.clear()
            return
        if ticket.status == TicketStatus.CLOSED.value:
            texts = get_texts(db_user.language)
            await message.answer(
                _alert(
                    'lock',
                    texts.t('TICKET_CLOSED', '<tg-emoji emoji-id="5776375003280838798">✅</tg-emoji> Тикет закрыт.'),
                ),
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            make_button(
                                text=texts.t(
                                    'CLOSE_NOTIFICATION',
                                    '<tg-emoji emoji-id="5778527486270770928">❌</tg-emoji> Закрыть уведомление',
                                ),
                                callback_data=f'close_ticket_notification_{ticket.id}',
                                style='danger',
                                icon_custom_emoji_id=_UI_ICONS['cross'].emoji_id,
                            )
                        ]
                    ]
                ),
                parse_mode='HTML',
            )
            await state.clear()
            return

        # Блокируем добавление сообщения, если тикет закрыт или заблокирован админом
        if ticket.status == TicketStatus.CLOSED.value or ticket.is_user_reply_blocked:
            texts = get_texts(db_user.language)
            await message.answer(
                _alert(
                    'lock',
                    texts.t(
                        'TICKET_CLOSED_NO_REPLY',
                        '<tg-emoji emoji-id="5778527486270770928">❌</tg-emoji> Тикет закрыт, ответить невозможно.',
                    ),
                ),
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            make_button(
                                text=texts.t(
                                    'CLOSE_NOTIFICATION',
                                    '<tg-emoji emoji-id="5778527486270770928">❌</tg-emoji> Закрыть уведомление',
                                ),
                                callback_data=f'close_ticket_notification_{ticket.id}',
                                style='danger',
                                icon_custom_emoji_id=_UI_ICONS['cross'].emoji_id,
                            )
                        ]
                    ]
                ),
                parse_mode='HTML',
            )
            await state.clear()
            return

        # Добавляем сообщение в тикет
        await TicketMessageCRUD.add_message(
            db,
            ticket_id,
            db_user.id,
            reply_text,
            is_from_admin=False,
            media_type=media_type,
            media_file_id=media_file_id,
            media_caption=media_caption,
        )

        texts = get_texts(db_user.language)

        await message.answer(
            _screen(
                'success',
                texts.t(
                    'TICKET_REPLY_SENT', '<tg-emoji emoji-id="5776375003280838798">✅</tg-emoji> Ваш ответ отправлен!'
                ),
            ),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        make_button(
                            text=_btn_text(
                                texts.t(
                                    'VIEW_TICKET',
                                    '<tg-emoji emoji-id="5960714428394507968">👁</tg-emoji> Посмотреть тикет',
                                )
                            ),
                            callback_data=f'view_ticket_{ticket_id}',
                            style='primary',
                            icon_custom_emoji_id=_UI_ICONS['eye'].emoji_id,
                        )
                    ],
                    [
                        make_button(
                            text=texts.t('BACK_TO_MENU', '← В главное меню'),
                            callback_data='back_to_menu',
                            style='danger',
                            icon_custom_emoji_id=_UI_ICONS['back'].emoji_id,
                        )
                    ],
                ]
            ),
            parse_mode='HTML',
        )

        await state.clear()

        # Уведомить админов об ответе пользователя
        logger.info('Attempting to notify admins about ticket reply', ticket_id=ticket_id)
        await notify_admins_about_ticket_reply(
            ticket, reply_text, db, media_file_id=media_file_id, media_type=media_type
        )

    except Exception as e:
        logger.error('Error adding ticket reply', error=e)
        texts = get_texts(db_user.language)
        await message.answer(
            _alert(
                'cross',
                texts.t(
                    'TICKET_REPLY_ERROR',
                    '<tg-emoji emoji-id="5778527486270770928">❌</tg-emoji> Произошла ошибка при отправке ответа. Попробуйте позже.',
                ),
            ),
            parse_mode='HTML',
        )


async def close_ticket(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Закрыть тикет"""
    ticket_id = int(callback.data.replace('close_ticket_', ''))

    try:
        # Проверяем, что тикет принадлежит пользователю
        ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=False)
        if not ticket or ticket.user_id != db_user.id:
            texts = get_texts(db_user.language)
            await callback.answer(_plain(texts.t('TICKET_NOT_FOUND', 'Тикет не найден.')), show_alert=True)
            return

        # Запрещаем закрытие, если заблокирован для ответа? (не требуется) Закрываем тикет
        success = await TicketCRUD.close_ticket(db, ticket_id)

        if success:
            texts = get_texts(db_user.language)
            await callback.answer(
                _plain(
                    texts.t('TICKET_CLOSED', '<tg-emoji emoji-id="5776375003280838798">✅</tg-emoji> Тикет закрыт.')
                ),
                show_alert=True,
            )

            # Обновляем inline-клавиатуру текущего сообщения (убираем кнопки)
            await callback.message.edit_reply_markup(
                reply_markup=get_ticket_view_keyboard(ticket_id, True, db_user.language)
            )
        else:
            texts = get_texts(db_user.language)
            await callback.answer(
                _plain(
                    texts.t(
                        'TICKET_CLOSE_ERROR',
                        '<tg-emoji emoji-id="5778527486270770928">❌</tg-emoji> Ошибка при закрытии тикета.',
                    )
                ),
                show_alert=True,
            )

    except Exception as e:
        logger.error('Error closing ticket', error=e)
        texts = get_texts(db_user.language)
        await callback.answer(
            _plain(
                texts.t(
                    'TICKET_CLOSE_ERROR',
                    '<tg-emoji emoji-id="5778527486270770928">❌</tg-emoji> Ошибка при закрытии тикета.',
                )
            ),
            show_alert=True,
        )


async def cancel_ticket_creation(callback: types.CallbackQuery, state: FSMContext, db_user: User):
    """Отменить создание тикета"""
    await state.clear()

    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        _screen('cross', texts.t('TICKET_CREATION_CANCELLED', 'Создание тикета отменено.')),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    make_button(
                        text=texts.t(
                            'BACK_TO_SUPPORT', '<tg-emoji emoji-id="5875082500023258804">⬅</tg-emoji> К поддержке'
                        ),
                        callback_data='menu_support',
                        style='danger',
                        icon_custom_emoji_id=_UI_ICONS['back'].emoji_id,
                    )
                ]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


async def cancel_ticket_reply(callback: types.CallbackQuery, state: FSMContext, db_user: User):
    """Отменить ответ на тикет"""
    await state.clear()

    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        _screen('cross', texts.t('TICKET_REPLY_CANCELLED', 'Ответ отменен.')),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    make_button(
                        text=texts.t(
                            'BACK_TO_TICKETS', '<tg-emoji emoji-id="5875082500023258804">⬅</tg-emoji> К тикетам'
                        ),
                        callback_data='my_tickets',
                        style='danger',
                        icon_custom_emoji_id=_UI_ICONS['back'].emoji_id,
                    )
                ]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


async def close_ticket_notification(callback: types.CallbackQuery, db_user: User):
    """Закрыть уведомление о тикете"""
    texts = get_texts(db_user.language)

    # Проверяем, доступно ли сообщение для удаления
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return

    try:
        await callback.message.delete()
    except TelegramBadRequest:
        # Message is too old to delete (>48h) — edit it instead
        try:
            await callback.message.edit_text(
                _screen('check', texts.t('NOTIFICATION_CLOSED', 'Уведомление закрыто.')),
                parse_mode='HTML',
            )
        except TelegramBadRequest:
            pass
    await callback.answer(_plain(texts.t('NOTIFICATION_CLOSED', 'Уведомление закрыто.')))


def build_ticket_card_keyboard(ticket: Ticket, user: User | None, *, role: str):
    """Клавиатура карточки тикета по роли получателя ('admin' / 'moderator' / 'group').

    Для группового/супергруппа админ-чата ('group') — урезанный набор без
    FSM-кнопок: «Ответить»/«Блок по времени» в общем чате не работают из-за
    privacy mode бота, остаются надёжные URL/callback-кнопки. Той же клавиатурой
    карточка перерисовывается после действий в группе (закрыть, блок, разблок) —
    иначе туда попадал экран личной админки с мёртвыми кнопками и «Назад».
    """
    from app.config import settings

    # В cabinet-режиме добавляем кнопку «открыть тикет в кабинете»: web_app в личке,
    # t.me Mini App диплинк в группе (где web_app недоступен). None — если не
    # cabinet-режим / кабинет не настроен / в группе нет зарегистрированного Mini App.
    cabinet_button = build_admin_ticket_cabinet_button(
        ticket.id,
        text=get_texts(settings.DEFAULT_LANGUAGE).t(
            'OPEN_TICKET_IN_CABINET', '<tg-emoji emoji-id="5883973610606956186">🗂</tg-emoji> Открыть в кабинете'
        ),
        in_group=(role == 'group'),
    )
    return get_ticket_notification_keyboard(
        ticket.id,
        user_id=user.id if user else None,
        telegram_id=user.telegram_id if user else None,
        username=user.username if user else None,
        is_closed=ticket.is_closed,
        is_user_blocked=getattr(ticket, 'is_user_reply_blocked', False),
        is_admin=(role == 'admin'),
        fsm_enabled=(role != 'group'),
        cabinet_button=cabinet_button,
        language=settings.DEFAULT_LANGUAGE,
    )


def _build_ticket_notification_keyboard(service: AdminNotificationService, ticket: Ticket, user: User | None):
    """Клавиатура уведомления о тикете; None только для роли 'none'
    (посторонний в личке / строковый chat_id)."""
    role = service.resolve_recipient_role()
    if role == 'none':
        return None
    return build_ticket_card_keyboard(ticket, user, role=role)


async def notify_admins_about_new_ticket(ticket: Ticket, db: AsyncSession):
    """Уведомить админов о новом тикете"""
    try:
        from app.config import settings

        if not settings.is_admin_notifications_enabled():
            logger.info(
                'Admin notifications disabled. Ticket created by user', ticket_id=ticket.id, user_id=ticket.user_id
            )
            return

        get_texts(settings.DEFAULT_LANGUAGE)
        title = (ticket.title or '').strip()
        if len(title) > 60:
            title = title[:57] + '...'

        try:
            user = await get_user_by_id(db, ticket.user_id)
        except Exception:
            user = None
        full_name = html.escape(user.full_name or '') if user else 'Unknown'
        telegram_id_display = (user.telegram_id or user.email or f'#{user.id}') if user else '—'
        username_display = format_username_link(user.username if user else None, 'отсутствует')

        # Загружаем первое сообщение для получения медиа и превью текста
        first_message = await TicketMessageCRUD.get_first_message(db, ticket.id)
        media_file_id = None
        media_type = None
        message_preview = ''
        if first_message:
            media_file_id = first_message.media_file_id if first_message.has_media else None
            media_type = first_message.media_type if first_message.has_media else None
            msg_text = (first_message.message_text or '').strip()
            if msg_text:
                message_preview = preview_text(msg_text)

        safe_title = html.escape(title) if title else '—'

        notification_text = (
            f'<b>НОВЫЙ ТИКЕТ</b>\n\n'
            f'<b>ID:</b> <code>{ticket.id}</code>\n'
            f'<tg-emoji emoji-id="5771887475421090729">👤</tg-emoji> <b>Пользователь:</b> {full_name}\n'
            f'<b>ID:</b> <code>{telegram_id_display}</code>\n'
            f'<tg-emoji emoji-id="5985833664884250583">📱</tg-emoji> <b>Username:</b> {username_display}\n'
            f'<tg-emoji emoji-id="5886330010054168711">📝</tg-emoji> <b>Заголовок:</b> {safe_title}\n'
        )

        if message_preview:
            notification_text += f'\n<b>Сообщение:</b>\n{html.escape(message_preview)}\n'

        notification_text += f'\n<tg-emoji emoji-id="5967412305338568701">📅</tg-emoji> <b>Создан:</b> {format_local_datetime(ticket.created_at, "%d.%m.%Y %H:%M")}\n'

        from app.services.maintenance_service import maintenance_service

        bot = maintenance_service._bot or None
        if bot is None:
            logger.warning('Bot instance is not available for admin notifications')
            return

        service = AdminNotificationService(bot)

        # Определяем роль получателя до отправки (без I/O — данные в памяти).
        # В личном чате chat_id == telegram_id, что позволяет проверить права заранее.
        keyboard = _build_ticket_notification_keyboard(service, ticket, user)

        await service.send_ticket_event_notification(
            notification_text, keyboard, media_file_id=media_file_id, media_type=media_type
        )
    except Exception as e:
        logger.error('Error notifying admins about new ticket', error=e)


async def notify_admins_about_ticket_reply(
    ticket: Ticket,
    reply_text: str,
    db: AsyncSession,
    *,
    media_file_id: str | None = None,
    media_type: str | None = None,
):
    """Уведомить админов об ответе пользователя на тикет"""
    logger.info('notify_admins_about_ticket_reply called for ticket', ticket_id=ticket.id)
    try:
        from app.config import settings

        if not settings.is_admin_notifications_enabled():
            logger.info('Admin notifications disabled. Reply to ticket', ticket_id=ticket.id)
            return

        title = (ticket.title or '').strip()
        if len(title) > 60:
            title = title[:57] + '...'

        try:
            user = await get_user_by_id(db, ticket.user_id)
        except Exception:
            user = None
        full_name = html.escape(user.full_name or '') if user else 'Unknown'
        telegram_id_display = (user.telegram_id or user.email or f'#{user.id}') if user else '—'
        username_display = format_username_link(user.username if user else None, 'отсутствует')

        reply_preview = preview_text(reply_text)
        safe_title = html.escape(title) if title else '—'

        notification_text = (
            f'<tg-emoji emoji-id="5886666250158870040">💬</tg-emoji> <b>ОТВЕТ НА ТИКЕТ</b>\n\n'
            f'<b>ID тикета:</b> <code>{ticket.id}</code>\n'
            f'<tg-emoji emoji-id="5886330010054168711">📝</tg-emoji> <b>Заголовок:</b> {safe_title}\n'
            f'<tg-emoji emoji-id="5771887475421090729">👤</tg-emoji> <b>Пользователь:</b> {full_name}\n'
            f'<b>ID:</b> <code>{telegram_id_display}</code>\n'
            f'<tg-emoji emoji-id="5985833664884250583">📱</tg-emoji> <b>Username:</b> {username_display}\n\n'
            f'<b>Сообщение:</b>\n{html.escape(reply_preview)}\n'
        )

        from app.services.maintenance_service import maintenance_service

        bot = maintenance_service._bot or None
        if bot is None:
            logger.warning('Bot instance is not available for admin notifications')
            return

        service = AdminNotificationService(bot)

        keyboard = _build_ticket_notification_keyboard(service, ticket, user)

        result = await service.send_ticket_event_notification(
            notification_text, keyboard, media_file_id=media_file_id, media_type=media_type
        )
        logger.info('Ticket reply notification sent', ticket_id=ticket.id, result=result)
    except Exception as e:
        logger.error('Error notifying admins about ticket reply', error=e)


def register_handlers(dp: Dispatcher):
    """Регистрация обработчиков тикетов"""

    dp.callback_query.register(start_payment_ticket, F.data == 'payment_support_create', flags={'support_access': True})

    # Создание тикета (теперь без приоритета)
    dp.callback_query.register(
        show_ticket_priority_selection, F.data == 'create_ticket', flags={'support_access': True}
    )

    dp.message.register(handle_ticket_title_input, TicketStates.waiting_for_title, flags={'support_access': True})

    dp.message.register(handle_ticket_message_input, TicketStates.waiting_for_message, flags={'support_access': True})

    # Просмотр тикетов
    dp.callback_query.register(show_my_tickets, F.data == 'my_tickets', flags={'support_access': True})
    dp.callback_query.register(show_my_tickets_closed, F.data == 'my_tickets_closed', flags={'support_access': True})
    dp.callback_query.register(
        show_my_tickets_closed, F.data.startswith('my_tickets_closed_page_'), flags={'support_access': True}
    )

    dp.callback_query.register(
        view_ticket,
        F.data.startswith('view_ticket_') | F.data.startswith('ticket_view_page_'),
        flags={'support_access': True},
    )

    # Вложения пользователя
    dp.callback_query.register(
        send_ticket_attachments, F.data.startswith('ticket_attachments_'), flags={'support_access': True}
    )

    dp.callback_query.register(
        user_delete_message, F.data.startswith('user_delete_message_'), flags={'support_access': True}
    )

    # Ответы на тикеты
    dp.callback_query.register(reply_to_ticket, F.data.startswith('reply_ticket_'), flags={'support_access': True})

    dp.message.register(handle_ticket_reply, TicketStates.waiting_for_reply, flags={'support_access': True})

    # Закрытие тикетов
    dp.callback_query.register(close_ticket, F.data.regexp(r'^close_ticket_\d+$'), flags={'support_access': True})

    # Отмена операций
    dp.callback_query.register(
        cancel_ticket_creation, F.data == 'cancel_ticket_creation', flags={'support_access': True}
    )

    dp.callback_query.register(cancel_ticket_reply, F.data == 'cancel_ticket_reply', flags={'support_access': True})

    # Пагинация тикетов
    dp.callback_query.register(show_my_tickets, F.data.startswith('my_tickets_page_'), flags={'support_access': True})

    # Закрытие уведомлений
    dp.callback_query.register(
        close_ticket_notification, F.data.startswith('close_ticket_notification_'), flags={'support_access': True}
    )
