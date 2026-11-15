import html
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import structlog
from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.poll import (
    create_poll,
    delete_poll,
    get_poll_by_id,
    get_poll_statistics,
    list_polls,
)
from app.database.models import Poll, User
from app.handlers.admin.messages import (
    get_custom_users,
    get_custom_users_count,
    get_target_display_name,
    get_target_users,
    get_target_users_count,
)
from app.keyboards.admin import get_admin_communications_submenu_keyboard
from app.localization.texts import get_texts
from app.services.poll_service import send_poll_to_users
from app.utils.admin_ui import admin_back_button, admin_button, admin_heading, admin_icon, admin_quote, admin_screen
from app.utils.decorators import admin_required, error_handler
from app.utils.miniapp_buttons import strip_leading_emoji
from app.utils.validators import get_html_help_text, validate_html_tags


logger = structlog.get_logger(__name__)

_BOLD_TAGS_RE = re.compile(r'</?b>')


def _heading(icon: str, title: str) -> str:
    """Заголовок с premium-иконкой и <b>; существующие теги не дублируются."""
    return admin_heading(icon, _BOLD_TAGS_RE.sub('', title or '').strip())


def _prompt_body(raw: str) -> str:
    """Промпт без повторённого заголовка экрана из локали."""
    prepared = (raw or '').strip()
    if '\n\n' in prepared:
        head, _, body = prepared.partition('\n\n')
        if '<b>' in head or '<tg-emoji' in head:
            return body.strip()
    return prepared


def _safe_format_price(amount_kopeks: int) -> str:
    try:
        return settings.format_price(amount_kopeks)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error('Не удалось отформатировать сумму', amount_kopeks=amount_kopeks, error=error)
        return f'{amount_kopeks / 100:.2f} ₽'


async def _safe_delete_message(message: types.Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest as error:
        if 'message to delete not found' in str(error).lower():
            logger.debug('Сообщение уже удалено', error=error)
        else:
            logger.warning('Не удалось удалить сообщение', message_id=message.message_id, error=error)


async def _edit_creation_message(
    bot: Bot,
    state_data: dict,
    text: str,
    *,
    reply_markup: types.InlineKeyboardMarkup | None = None,
    parse_mode: str | None = 'HTML',
) -> bool:
    chat_id = state_data.get('form_chat_id')
    message_id = state_data.get('form_message_id')

    if not chat_id or not message_id:
        return False

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return True
    except TelegramBadRequest as error:
        error_text = str(error).lower()
        if 'message is not modified' in error_text:
            return True
        log_method = logger.debug if 'there is no text in the message to edit' in error_text else logger.warning
        log_method(
            'Не удалось обновить сообщение создания опроса %s: %s',
            message_id,
            error,
        )
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            'Непредвиденная ошибка при обновлении сообщения создания опроса', message_id=message_id, error=error
        )
    return False


async def _send_creation_message(
    message: types.Message,
    state: FSMContext,
    text: str,
    *,
    reply_markup: types.InlineKeyboardMarkup | None = None,
    parse_mode: str | None = 'HTML',
) -> types.Message:
    state_data = await state.get_data()
    chat_id = state_data.get('form_chat_id')
    message_id = state_data.get('form_message_id')

    if chat_id and message_id:
        try:
            await message.bot.delete_message(chat_id, message_id)
        except TelegramBadRequest as error:
            error_text = str(error).lower()
            if 'message to delete not found' in error_text:
                logger.debug('Сообщение уже удалено', error=error)
            else:
                logger.warning('Не удалось удалить сообщение создания опроса', message_id=message_id, error=error)
        except Exception as error:  # pragma: no cover - defensive logging
            logger.error(
                'Непредвиденная ошибка при удалении сообщения создания опроса', message_id=message_id, error=error
            )

    sent_message = await message.bot.send_message(
        chat_id=message.chat.id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )

    await state.update_data(
        form_chat_id=sent_message.chat.id,
        form_message_id=sent_message.message_id,
    )

    return sent_message


def _creation_cancelled_text(texts) -> str:
    """Экран отмены создания опроса в общем визуальном языке."""
    return admin_screen(
        texts.t('ADMIN_POLLS_CREATION_CANCELLED', 'Создание опроса отменено.'),
        icon='info',
        compact=True,
    )


def _render_creation_progress(
    texts,
    data: dict,
    next_step: str,
    *,
    status_message: str | None = None,
    error_message: str | None = None,
) -> str:
    heading = _heading('stats', texts.t('ADMIN_POLLS_CREATION_HEADER', '<b>Создание опроса</b>'))
    params: list[str] = []

    title = data.get('title')
    if title:
        params.append(f'{admin_icon("edit")} {html.escape(title)}')

    if 'description' in data:
        description = data.get('description')
        if description:
            params.append(f'{admin_icon("document")} {description}')
        else:
            params.append(
                f'{admin_icon("document")} '
                + texts.t(
                    'ADMIN_POLLS_CREATION_DESCRIPTION_SKIPPED',
                    'Описание пропущено.',
                )
            )

    if 'reward_enabled' in data:
        if data.get('reward_enabled'):
            params.append(
                f'{admin_icon("gift")} '
                + texts.t('ADMIN_POLLS_REWARD_ENABLED', 'Награда: {amount}').format(
                    amount=_safe_format_price(data.get('reward_amount_kopeks', 0))
                )
            )
        else:
            params.append(f'{admin_icon("gift")} ' + texts.t('ADMIN_POLLS_REWARD_DISABLED', 'Награда отключена'))

    questions = data.get('questions', [])
    if questions:
        params.append(
            f'{admin_icon("question")} '
            + texts.t('ADMIN_POLLS_QUESTIONS_COUNT', 'Вопросов: {count}').format(count=len(questions))
        )
        for idx, question in enumerate(questions, start=1):
            params.append(f'{idx}. {html.escape(question["text"])}')
            for option in question['options']:
                params.append(f'   • {html.escape(option)}')

    prompts = {
        'title': texts.t('ADMIN_POLLS_CREATION_TITLE_PROMPT', 'Введите заголовок опроса:'),
        'description': texts.t(
            'ADMIN_POLLS_CREATION_DESCRIPTION_PROMPT',
            'Введите описание опроса. HTML разрешён.\nОтправьте /skip, чтобы пропустить.',
        ),
        'reward': texts.t(
            'ADMIN_POLLS_CREATION_REWARD_PROMPT',
            'Введите сумму награды в рублях. Отправьте 0 чтобы отключить награду.',
        ),
        'questions': texts.t(
            'ADMIN_POLLS_CREATION_QUESTION_PROMPT',
            (
                'Введите вопрос и варианты ответов.\n'
                'Каждая строка — отдельный вариант.\n'
                'Первая строка — текст вопроса.\n'
                'Отправьте /done, когда вопросы будут добавлены.'
            ),
        ),
    }

    blocks = [heading]
    if params:
        blocks.append(admin_quote('\n'.join(params)))
    blocks.append(_prompt_body(prompts.get(next_step, '')))

    if next_step == 'description' and 'description' not in data:
        blocks.append(get_html_help_text())

    if status_message:
        blocks.append(status_message)
    if error_message:
        blocks.append(error_message)

    return '\n\n'.join(block for block in blocks if block)


class PollCreationStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_reward = State()
    waiting_for_questions = State()


def _build_polls_keyboard(polls: list[Poll], language: str) -> types.InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard: list[list[types.InlineKeyboardButton]] = []

    for poll in polls[:10]:
        keyboard.append(
            [
                admin_button(
                    poll.title[:40],
                    callback_data=f'poll_view:{poll.id}',
                    icon='stats',
                )
            ]
        )

    keyboard.append(
        [
            admin_button(
                strip_leading_emoji(texts.t('ADMIN_POLLS_CREATE', 'Создать опрос')),
                callback_data='poll_create',
                icon='plus',
            )
        ]
    )
    keyboard.append([admin_back_button(texts, 'admin_submenu_communications')])

    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def _format_reward_text(poll: Poll, language: str) -> str:
    texts = get_texts(language)
    if poll.reward_enabled and poll.reward_amount_kopeks > 0:
        return texts.t(
            'ADMIN_POLLS_REWARD_ENABLED',
            'Награда: {amount}',
        ).format(amount=settings.format_price(poll.reward_amount_kopeks))
    return texts.t('ADMIN_POLLS_REWARD_DISABLED', 'Награда отключена')


def _build_poll_details_keyboard(poll_id: int, language: str) -> types.InlineKeyboardMarkup:
    texts = get_texts(language)
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    strip_leading_emoji(texts.t('ADMIN_POLLS_SEND', 'Отправить')),
                    callback_data=f'poll_send:{poll_id}',
                    icon='success',
                )
            ],
            [
                admin_button(
                    strip_leading_emoji(texts.t('ADMIN_POLLS_STATS', 'Статистика')),
                    callback_data=f'poll_stats:{poll_id}',
                    icon='stats',
                )
            ],
            [
                admin_button(
                    strip_leading_emoji(texts.t('ADMIN_POLLS_DELETE', 'Удалить')),
                    callback_data=f'poll_delete:{poll_id}',
                    icon='delete',
                )
            ],
            [
                admin_back_button(
                    texts,
                    'admin_polls',
                    label=strip_leading_emoji(texts.t('ADMIN_POLLS_BACK', 'К списку')),
                )
            ],
        ]
    )


def _build_target_keyboard(poll_id: int, language: str) -> types.InlineKeyboardMarkup:
    texts = get_texts(language)

    target_icons = {
        'all': 'users',
        'active': 'success',
        'trial': 'trials',
        'no': 'prohibit',
        'expiring': 'clock',
        'expired': 'lock',
        'active_zero': 'warning',
        'trial_zero': 'warning',
    }

    def _target_button(label_key: str, default: str, target: str) -> types.InlineKeyboardButton:
        return admin_button(
            strip_leading_emoji(texts.t(label_key, default)),
            callback_data=f'poll_target:{poll_id}:{target}',
            icon=target_icons.get(target, 'users'),
        )

    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _target_button('ADMIN_BROADCAST_TARGET_ALL', 'Всем', 'all'),
                _target_button('ADMIN_BROADCAST_TARGET_ACTIVE', 'С подпиской', 'active'),
            ],
            [
                _target_button('ADMIN_BROADCAST_TARGET_TRIAL', 'Триал', 'trial'),
                _target_button('ADMIN_BROADCAST_TARGET_NO_SUB', 'Без подписки', 'no'),
            ],
            [
                _target_button('ADMIN_BROADCAST_TARGET_EXPIRING', 'Истекающие', 'expiring'),
                _target_button('ADMIN_BROADCAST_TARGET_EXPIRED', 'Истекшие', 'expired'),
            ],
            [
                _target_button('ADMIN_BROADCAST_TARGET_ACTIVE_ZERO', 'Активна 0 ГБ', 'active_zero'),
                _target_button('ADMIN_BROADCAST_TARGET_TRIAL_ZERO', 'Триал 0 ГБ', 'trial_zero'),
            ],
            [
                admin_button(
                    strip_leading_emoji(texts.t('ADMIN_POLLS_CUSTOM_TARGET', 'По критериям')),
                    callback_data=f'poll_custom_menu:{poll_id}',
                    icon='filter',
                )
            ],
            [admin_back_button(texts, f'poll_view:{poll_id}')],
        ]
    )


def _build_custom_target_keyboard(poll_id: int, language: str) -> types.InlineKeyboardMarkup:
    texts = get_texts(language)

    criteria_icons = {
        'today': 'calendar',
        'week': 'calendar',
        'month': 'calendar',
        'active_today': 'eye',
        'inactive_week': 'clock',
        'inactive_month': 'clock',
        'referrals': 'referrals',
        'direct': 'user',
    }

    def _criteria_button(label_key: str, default: str, criteria: str) -> types.InlineKeyboardButton:
        return admin_button(
            strip_leading_emoji(texts.t(label_key, default)),
            callback_data=f'poll_custom_target:{poll_id}:{criteria}',
            icon=criteria_icons.get(criteria, 'filter'),
        )

    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _criteria_button('ADMIN_CRITERIA_TODAY', 'Сегодня', 'today'),
                _criteria_button('ADMIN_CRITERIA_WEEK', 'За неделю', 'week'),
            ],
            [
                _criteria_button('ADMIN_CRITERIA_MONTH', 'За месяц', 'month'),
                _criteria_button('ADMIN_CRITERIA_ACTIVE_TODAY', 'Активные сегодня', 'active_today'),
            ],
            [
                _criteria_button('ADMIN_CRITERIA_INACTIVE_WEEK', 'Неактивные 7+ дней', 'inactive_week'),
                _criteria_button('ADMIN_CRITERIA_INACTIVE_MONTH', 'Неактивные 30+ дней', 'inactive_month'),
            ],
            [
                _criteria_button('ADMIN_CRITERIA_REFERRALS', 'Через рефералов', 'referrals'),
                _criteria_button('ADMIN_CRITERIA_DIRECT', 'Прямая регистрация', 'direct'),
            ],
            [admin_back_button(texts, f'poll_send:{poll_id}')],
        ]
    )


def _build_send_confirmation_keyboard(poll_id: int, target: str, language: str) -> types.InlineKeyboardMarkup:
    texts = get_texts(language)
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    strip_leading_emoji(texts.t('ADMIN_POLLS_SEND_CONFIRM_BUTTON', 'Отправить')),
                    callback_data=f'poll_send_confirm:{poll_id}:{target}',
                    icon='broadcast',
                )
            ],
            [admin_back_button(texts, f'poll_send:{poll_id}')],
        ]
    )


@admin_required
@error_handler
async def show_polls_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    polls = await list_polls(db)
    texts = get_texts(db_user.language)

    heading = _heading('stats', texts.t('ADMIN_POLLS_LIST_TITLE', '<b>Опросы</b>'))
    if not polls:
        text = f'{heading}\n\n{texts.t("ADMIN_POLLS_LIST_EMPTY", "Опросов пока нет.")}'
    else:
        lines = []
        for poll in polls[:10]:
            reward = _format_reward_text(poll, db_user.language)
            questions = texts.t('ADMIN_POLLS_QUESTIONS_COUNT', 'Вопросов: {count}').format(count=len(poll.questions))
            lines.append(
                f'{admin_icon("stats")} <b>{html.escape(poll.title)}</b>\n'
                f'   {admin_icon("question")} {questions}\n'
                f'   {admin_icon("gift")} {reward}'
            )
        text = f'{heading}\n\n{admin_quote(chr(10).join(lines))}'

    await callback.message.edit_text(
        text,
        reply_markup=_build_polls_keyboard(polls, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def start_poll_creation(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    await state.clear()
    await state.set_state(PollCreationStates.waiting_for_title)
    await _safe_delete_message(callback.message)
    await state.update_data(questions=[])

    form_text = _render_creation_progress(texts, await state.get_data(), 'title')
    await _send_creation_message(
        callback.message,
        state,
        form_text,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_poll_title(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    state_data = await state.get_data()

    if message.text == '/cancel':
        await _safe_delete_message(message)
        cancel_text = _creation_cancelled_text(texts)
        keyboard = get_admin_communications_submenu_keyboard(db_user.language)
        updated = await _edit_creation_message(
            message.bot,
            state_data,
            cancel_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
        if not updated:
            await _send_creation_message(
                message,
                state,
                cancel_text,
                reply_markup=keyboard,
                parse_mode='HTML',
            )
        await state.clear()
        return

    title = (message.text or '').strip()
    await _safe_delete_message(message)

    if not title:
        error_text = texts.t(
            'ADMIN_POLLS_CREATION_TITLE_EMPTY',
            'Заголовок не может быть пустым. Попробуйте снова.',
        )
        form_text = _render_creation_progress(texts, state_data, 'title', error_message=error_text)
        updated = await _edit_creation_message(message.bot, state_data, form_text)
        if not updated:
            await _send_creation_message(
                message,
                state,
                form_text,
                parse_mode='HTML',
            )
        return

    await state.update_data(title=title)
    await state.set_state(PollCreationStates.waiting_for_description)

    new_data = await state.get_data()
    form_text = _render_creation_progress(texts, new_data, 'description')
    updated = await _edit_creation_message(message.bot, new_data, form_text)
    if not updated:
        await _send_creation_message(
            message,
            state,
            form_text,
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def process_poll_description(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    state_data = await state.get_data()

    if message.text == '/cancel':
        await _safe_delete_message(message)
        cancel_text = _creation_cancelled_text(texts)
        keyboard = get_admin_communications_submenu_keyboard(db_user.language)
        updated = await _edit_creation_message(
            message.bot,
            state_data,
            cancel_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
        if not updated:
            await _send_creation_message(
                message,
                state,
                cancel_text,
                reply_markup=keyboard,
                parse_mode='HTML',
            )
        await state.clear()
        return

    description: str | None
    message_text = message.text or ''
    await _safe_delete_message(message)

    if message_text == '/skip':
        description = None
    else:
        description = message_text.strip()
        is_valid, error_message = validate_html_tags(description)
        if not is_valid:
            error_text = texts.t(
                'ADMIN_POLLS_CREATION_INVALID_HTML',
                'Ошибка в HTML: {error}',
            ).format(error=error_message)
            form_text = _render_creation_progress(texts, state_data, 'description', error_message=error_text)
            updated = await _edit_creation_message(message.bot, state_data, form_text)
            if not updated:
                await _send_creation_message(
                    message,
                    state,
                    form_text,
                    parse_mode='HTML',
                )
            return

    await state.update_data(description=description)
    await state.set_state(PollCreationStates.waiting_for_reward)

    new_data = await state.get_data()
    form_text = _render_creation_progress(texts, new_data, 'reward')
    updated = await _edit_creation_message(message.bot, new_data, form_text)
    if not updated:
        await _send_creation_message(
            message,
            state,
            form_text,
            parse_mode='HTML',
        )


def _parse_reward_amount(message_text: str) -> int | None:
    normalized = message_text.replace(' ', '').replace(',', '.')
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None

    if value < 0:
        value = Decimal(0)

    kopeks = int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return max(0, kopeks)


@admin_required
@error_handler
async def process_poll_reward(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    state_data = await state.get_data()

    if message.text == '/cancel':
        await _safe_delete_message(message)
        cancel_text = _creation_cancelled_text(texts)
        keyboard = get_admin_communications_submenu_keyboard(db_user.language)
        updated = await _edit_creation_message(
            message.bot,
            state_data,
            cancel_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
        if not updated:
            await _send_creation_message(
                message,
                state,
                cancel_text,
                reply_markup=keyboard,
                parse_mode='HTML',
            )
        await state.clear()
        return

    reward_kopeks = _parse_reward_amount(message.text or '')
    await _safe_delete_message(message)
    if reward_kopeks is None:
        error_text = texts.t(
            'ADMIN_POLLS_CREATION_REWARD_INVALID',
            'Некорректная сумма. Попробуйте ещё раз.',
        )
        form_text = _render_creation_progress(texts, state_data, 'reward', error_message=error_text)
        updated = await _edit_creation_message(message.bot, state_data, form_text)
        if not updated:
            await _send_creation_message(
                message,
                state,
                form_text,
                parse_mode='HTML',
            )
        return

    reward_enabled = reward_kopeks > 0
    await state.update_data(
        reward_enabled=reward_enabled,
        reward_amount_kopeks=reward_kopeks,
    )
    await state.set_state(PollCreationStates.waiting_for_questions)

    new_data = await state.get_data()
    form_text = _render_creation_progress(texts, new_data, 'questions')
    updated = await _edit_creation_message(message.bot, new_data, form_text)
    if not updated:
        await _send_creation_message(
            message,
            state,
            form_text,
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def process_poll_question(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    state_data = await state.get_data()

    if message.text == '/cancel':
        await _safe_delete_message(message)
        cancel_text = _creation_cancelled_text(texts)
        keyboard = get_admin_communications_submenu_keyboard(db_user.language)
        updated = await _edit_creation_message(
            message.bot,
            state_data,
            cancel_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
        if not updated:
            await _send_creation_message(
                message,
                state,
                cancel_text,
                reply_markup=keyboard,
                parse_mode='HTML',
            )
        await state.clear()
        return

    if message.text == '/done':
        await _safe_delete_message(message)
        data = await state.get_data()
        questions = data.get('questions', [])
        if not questions:
            error_text = texts.t(
                'ADMIN_POLLS_CREATION_NEEDS_QUESTION',
                'Добавьте хотя бы один вопрос.',
            )
            form_text = _render_creation_progress(texts, data, 'questions', error_message=error_text)
            updated = await _edit_creation_message(message.bot, data, form_text)
            if not updated:
                await _send_creation_message(
                    message,
                    state,
                    form_text,
                    parse_mode='HTML',
                )
            return

        title = data.get('title')
        description = data.get('description')
        reward_enabled = data.get('reward_enabled', False)
        reward_amount = data.get('reward_amount_kopeks', 0)

        form_data = data.copy()

        poll = await create_poll(
            db,
            title=title,
            description=description,
            reward_enabled=reward_enabled,
            reward_amount_kopeks=reward_amount,
            created_by=db_user.id,
            questions=questions,
        )

        reward_text = _format_reward_text(poll, db_user.language)
        result_text = texts.t(
            'ADMIN_POLLS_CREATION_FINISHED',
            (' Опрос «{title}» создан!\nВопросов: {count}\n{reward}'),
        ).format(
            title=html.escape(poll.title),
            count=len(poll.questions),
            reward=reward_text,
        )

        keyboard = _build_polls_keyboard(await list_polls(db), db_user.language)
        updated = await _edit_creation_message(
            message.bot,
            form_data,
            result_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
        if not updated:
            await _send_creation_message(
                message,
                state,
                result_text,
                reply_markup=keyboard,
                parse_mode='HTML',
            )
        await state.clear()
        return

    lines = [line.strip() for line in (message.text or '').splitlines() if line.strip()]
    await _safe_delete_message(message)
    if len(lines) < 3:
        error_text = texts.t(
            'ADMIN_POLLS_CREATION_MIN_OPTIONS',
            'Нужен вопрос и минимум два варианта ответа.',
        )
        form_text = _render_creation_progress(texts, state_data, 'questions', error_message=error_text)
        updated = await _edit_creation_message(message.bot, state_data, form_text)
        if not updated:
            await _send_creation_message(
                message,
                state,
                form_text,
                parse_mode='HTML',
            )
        return

    question_text = lines[0]
    options = lines[1:]
    data = await state.get_data()
    questions = data.get('questions', [])
    questions.append({'text': question_text, 'options': options})
    await state.update_data(questions=questions)

    new_data = await state.get_data()
    status_message = texts.t(
        'ADMIN_POLLS_CREATION_ADDED_QUESTION',
        'Вопрос добавлен: «{question}». Добавьте следующий вопрос или отправьте /done.',
    ).format(question=html.escape(question_text))

    form_text = _render_creation_progress(
        texts,
        new_data,
        'questions',
        status_message=status_message,
    )
    updated = await _edit_creation_message(message.bot, new_data, form_text)
    if not updated:
        await _send_creation_message(
            message,
            state,
            form_text,
            parse_mode='HTML',
        )


async def _render_poll_details(poll: Poll, language: str) -> str:
    texts = get_texts(language)
    heading = _heading('stats', poll.title)

    meta = [f'{admin_icon("gift")} {_format_reward_text(poll, language)}']
    meta.append(
        f'{admin_icon("question")} '
        + texts.t('ADMIN_POLLS_QUESTIONS_COUNT', 'Вопросов: {count}').format(count=len(poll.questions))
    )
    if poll.description:
        meta.insert(0, f'{admin_icon("document")} {html.escape(poll.description)}')

    blocks = [heading, admin_quote('\n'.join(meta))]

    if poll.questions:
        questions = [texts.t('ADMIN_POLLS_QUESTION_LIST_HEADER', '<b>Вопросы:</b>')]
        for idx, question in enumerate(sorted(poll.questions, key=lambda q: q.order), start=1):
            questions.append(f'<b>{idx}. {html.escape(question.text)}</b>')
            for option in sorted(question.options, key=lambda o: o.order):
                questions.append(
                    '   ' + texts.t('ADMIN_POLLS_OPTION_BULLET', '• {option}').format(option=html.escape(option.text))
                )
        blocks.append('\n'.join(questions))

    return '\n\n'.join(block for block in blocks if block)


@admin_required
@error_handler
async def show_poll_details(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    poll_id = int(callback.data.split(':')[1])
    poll = await get_poll_by_id(db, poll_id)
    if not poll:
        await callback.answer(get_texts(db_user.language).t('POLL_NOT_FOUND'), show_alert=True)
        return

    text = await _render_poll_details(poll, db_user.language)
    await callback.message.edit_text(
        text,
        reply_markup=_build_poll_details_keyboard(poll.id, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def start_poll_send(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    poll_id = int(callback.data.split(':')[1])
    poll = await get_poll_by_id(db, poll_id)
    if not poll:
        await callback.answer(get_texts(db_user.language).t('POLL_NOT_FOUND'), show_alert=True)
        return

    texts = get_texts(db_user.language)
    await callback.message.edit_text(
        admin_screen(
            texts.t('ADMIN_POLLS_SEND_CHOOSE_TARGET', 'Выберите аудиторию для отправки опроса:'),
            icon='messages',
            compact=True,
        ),
        reply_markup=_build_target_keyboard(poll.id, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_custom_target_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    poll_id = int(callback.data.split(':')[1])
    await callback.message.edit_text(
        admin_screen(
            get_texts(db_user.language).t(
                'ADMIN_POLLS_CUSTOM_PROMPT',
                'Выберите дополнительный критерий аудитории:',
            ),
            icon='filter',
            compact=True,
        ),
        reply_markup=_build_custom_target_keyboard(poll_id, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


async def _show_send_confirmation(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    poll_id: int,
    target: str,
    user_count: int,
):
    poll = await get_poll_by_id(db, poll_id)
    if not poll:
        await callback.answer(get_texts(db_user.language).t('POLL_NOT_FOUND'), show_alert=True)
        return

    audience_name = get_target_display_name(target)
    texts = get_texts(db_user.language)
    confirmation_text = _heading(
        'question',
        texts.t(
            'ADMIN_POLLS_SEND_CONFIRM',
            'Отправить опрос «{title}» аудитории «{audience}»? Пользователей: {count}',
        ).format(title=html.escape(poll.title), audience=audience_name, count=user_count),
    )

    await callback.message.edit_text(
        confirmation_text,
        reply_markup=_build_send_confirmation_keyboard(poll_id, target, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def select_poll_target(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    _, payload = callback.data.split(':', 1)
    poll_id_str, target = payload.split(':', 1)
    poll_id = int(poll_id_str)

    user_count = await get_target_users_count(db, target)
    await _show_send_confirmation(callback, db_user, db, poll_id, target, user_count)


@admin_required
@error_handler
async def select_custom_poll_target(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    _, payload = callback.data.split(':', 1)
    poll_id_str, criteria = payload.split(':', 1)
    poll_id = int(poll_id_str)

    user_count = await get_custom_users_count(db, criteria)
    await _show_send_confirmation(callback, db_user, db, poll_id, f'custom_{criteria}', user_count)


@admin_required
@error_handler
async def confirm_poll_send(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    _, payload = callback.data.split(':', 1)
    poll_id_str, target = payload.split(':', 1)
    poll_id = int(poll_id_str)

    poll = await get_poll_by_id(db, poll_id)
    if not poll:
        await callback.answer(get_texts(db_user.language).t('POLL_NOT_FOUND'), show_alert=True)
        return

    poll_id_value = poll.id

    if target.startswith('custom_'):
        users = await get_custom_users(db, target.replace('custom_', ''))
    else:
        users = await get_target_users(db, target)

    user_language = db_user.language
    texts = get_texts(user_language)
    await callback.message.edit_text(
        admin_screen(
            texts.t('ADMIN_POLLS_SENDING', 'Запускаю отправку опроса...'),
            icon='messages',
            compact=True,
        ),
        parse_mode='HTML',
    )

    result = await send_poll_to_users(callback.bot, db, poll, users)

    result_lines = (
        texts.t(
            'ADMIN_POLLS_SEND_RESULT',
            'Отправка завершена\nУспешно: {sent}\nОшибок: {failed}\nПропущено: {skipped}\nВсего: {total}',
        )
        .format(**result)
        .split('\n')
    )
    summary_lines = [f'{admin_icon("check")} {line}' for line in result_lines[1:] if line]
    result_text = f'{_heading("success", result_lines[0])}\n\n{admin_quote(chr(10).join(summary_lines))}'

    await callback.message.edit_text(
        result_text,
        reply_markup=_build_poll_details_keyboard(poll_id_value, user_language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_poll_stats(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    poll_id = int(callback.data.split(':')[1])
    poll = await get_poll_by_id(db, poll_id)
    if not poll:
        await callback.answer(get_texts(db_user.language).t('POLL_NOT_FOUND'), show_alert=True)
        return

    stats = await get_poll_statistics(db, poll_id)
    texts = get_texts(db_user.language)

    reward_sum = settings.format_price(stats['reward_sum_kopeks'])
    heading = _heading('chart', texts.t('ADMIN_POLLS_STATS_HEADER', '<b>Статистика опроса</b>'))
    overview = texts.t(
        'ADMIN_POLLS_STATS_OVERVIEW',
        'Всего приглашено: {total}\nЗавершили: {completed}\nВыплачено наград: {reward}',
    ).format(
        total=stats['total_responses'],
        completed=stats['completed_responses'],
        reward=reward_sum,
    )
    blocks = [heading, admin_quote(overview)]

    for question in stats['questions']:
        lines = [f'<b>{html.escape(question["text"])}</b>']
        for option in question['options']:
            lines.append(
                '   '
                + texts.t(
                    'ADMIN_POLLS_STATS_OPTION_LINE',
                    '• {option}: {count}',
                ).format(option=html.escape(option['text']), count=option['count'])
            )
        blocks.append('\n'.join(lines))

    await callback.message.edit_text(
        '\n\n'.join(blocks),
        reply_markup=_build_poll_details_keyboard(poll.id, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_poll_delete(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    poll_id = int(callback.data.split(':')[1])
    poll = await get_poll_by_id(db, poll_id)
    if not poll:
        await callback.answer(get_texts(db_user.language).t('POLL_NOT_FOUND'), show_alert=True)
        return

    texts = get_texts(db_user.language)
    await callback.message.edit_text(
        admin_screen(
            texts.t(
                'ADMIN_POLLS_CONFIRM_DELETE',
                'Вы уверены, что хотите удалить опрос «{title}»?',
            ).format(title=html.escape(poll.title)),
            icon='warning',
            compact=True,
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    admin_button(
                        strip_leading_emoji(texts.t('ADMIN_POLLS_DELETE', 'Удалить')),
                        callback_data=f'poll_delete_confirm:{poll_id}',
                        icon='delete',
                    )
                ],
                [admin_back_button(texts, f'poll_view:{poll_id}')],
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_poll_handler(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    poll_id = int(callback.data.split(':')[1])
    success = await delete_poll(db, poll_id)
    texts = get_texts(db_user.language)

    if success:
        await callback.message.edit_text(
            admin_screen(texts.t('ADMIN_POLLS_DELETED', 'Опрос удалён.'), icon='success', compact=True),
            reply_markup=_build_polls_keyboard(await list_polls(db), db_user.language),
            parse_mode='HTML',
        )
    else:
        await callback.answer(get_texts(db_user.language).t('POLL_NOT_FOUND'), show_alert=True)
        return

    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_polls_panel, F.data == 'admin_polls')
    dp.callback_query.register(start_poll_creation, F.data == 'poll_create')
    dp.callback_query.register(show_poll_details, F.data.startswith('poll_view:'))
    dp.callback_query.register(start_poll_send, F.data.startswith('poll_send:'))
    dp.callback_query.register(show_custom_target_menu, F.data.startswith('poll_custom_menu:'))
    dp.callback_query.register(select_poll_target, F.data.startswith('poll_target:'))
    dp.callback_query.register(select_custom_poll_target, F.data.startswith('poll_custom_target:'))
    dp.callback_query.register(confirm_poll_send, F.data.startswith('poll_send_confirm:'))
    dp.callback_query.register(show_poll_stats, F.data.startswith('poll_stats:'))
    dp.callback_query.register(confirm_poll_delete, F.data.startswith('poll_delete:'))
    dp.callback_query.register(delete_poll_handler, F.data.startswith('poll_delete_confirm:'))

    dp.message.register(process_poll_title, PollCreationStates.waiting_for_title)
    dp.message.register(process_poll_description, PollCreationStates.waiting_for_description)
    dp.message.register(process_poll_reward, PollCreationStates.waiting_for_reward)
    dp.message.register(process_poll_question, PollCreationStates.waiting_for_questions)
