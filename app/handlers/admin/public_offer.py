import html
from datetime import datetime

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.handlers.admin.display_mode_button import cycle_display_mode_setting
from app.localization.texts import get_texts
from app.services.public_offer_service import PublicOfferService
from app.states import AdminStates
from app.utils.admin_ui import admin_back_button, admin_button, admin_quote, admin_screen
from app.utils.decorators import admin_required, error_handler
from app.utils.display_mode import display_mode_label
from app.utils.miniapp_buttons import strip_leading_emoji
from app.utils.telegram_html import stored_html_to_telegram_pages
from app.utils.validators import get_html_help_text, validate_html_tags


logger = structlog.get_logger(__name__)


def _label(text: str) -> str:
    """Текст кнопки без ведущего глифа локали: иконку даёт ``admin_button``."""
    return strip_leading_emoji(text.strip())


def _format_timestamp(value: datetime | None) -> str:
    if not value:
        return ''
    try:
        return value.strftime('%d.%m.%Y %H:%M')
    except Exception:
        return ''


async def _build_overview(
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    offer = await PublicOfferService.get_offer(
        db,
        db_user.language,
        fallback=False,
    )

    normalized_language = PublicOfferService.normalize_language(db_user.language)
    has_content = bool(offer and offer.content and offer.content.strip())

    description = texts.t(
        'ADMIN_PUBLIC_OFFER_DESCRIPTION',
        'Публичная оферта отображается в разделе «Инфо».',
    )

    status_text = texts.t(
        'ADMIN_PUBLIC_OFFER_STATUS_DISABLED',
        'Показ оферты выключен или текст отсутствует.',
    )
    if offer and offer.is_enabled and has_content:
        status_text = texts.t(
            'ADMIN_PUBLIC_OFFER_STATUS_ENABLED',
            'Оферта активна и показывается пользователям.',
        )
    elif offer and offer.is_enabled:
        status_text = texts.t(
            'ADMIN_PUBLIC_OFFER_STATUS_ENABLED_EMPTY',
            'Оферта включена, но текст пуст — пользователи её не увидят.',
        )

    updated_at = _format_timestamp(getattr(offer, 'updated_at', None))
    updated_block = ''
    if updated_at:
        updated_block = texts.t(
            'ADMIN_PUBLIC_OFFER_UPDATED_AT',
            'Последнее обновление: {timestamp}',
        ).format(timestamp=updated_at)

    preview_block = texts.t(
        'ADMIN_PUBLIC_OFFER_PREVIEW_EMPTY',
        'Текст ещё не задан.',
    )
    if has_content:
        preview_title = texts.t(
            'ADMIN_PUBLIC_OFFER_PREVIEW_TITLE',
            '<b>Превью текста:</b>',
        )
        preview_raw = offer.content.strip()
        preview_trimmed = preview_raw[:400]
        if len(preview_raw) > 400:
            preview_trimmed += '...'
        preview_block = f'{preview_title}\n<code>{html.escape(preview_trimmed)}</code>'

    language_block = texts.t(
        'ADMIN_PUBLIC_OFFER_LANGUAGE',
        'Язык: <code>{lang}</code>',
    ).format(lang=normalized_language)

    header = texts.t(
        'ADMIN_PUBLIC_OFFER_HEADER',
        '<b>Публичная оферта</b>',
    )
    actions_prompt = texts.t(
        'ADMIN_PUBLIC_OFFER_ACTION_PROMPT',
        '',
    )

    message_parts = [
        header,
        description,
    ]

    meta_parts = [part for part in (language_block, status_text, updated_block) if part]
    if meta_parts:
        message_parts.append(admin_quote('\n'.join(meta_parts)))

    message_parts.append(preview_block)
    message_parts.append(actions_prompt)

    overview_text = '\n\n'.join(part.strip() for part in message_parts if part)

    buttons: list[list[types.InlineKeyboardButton]] = []

    buttons.append(
        [
            admin_button(
                _label(
                    texts.t(
                        'ADMIN_PUBLIC_OFFER_EDIT_BUTTON',
                        'Изменить текст',
                    )
                ),
                callback_data='admin_public_offer_edit',
                icon='document',
                style='primary',
            )
        ]
    )

    if has_content:
        buttons.append(
            [
                admin_button(
                    _label(
                        texts.t(
                            'ADMIN_PUBLIC_OFFER_VIEW_BUTTON',
                            'Просмотреть текущий текст',
                        )
                    ),
                    callback_data='admin_public_offer_view',
                    icon='document',
                    style='primary',
                )
            ]
        )

    toggle_text = _label(
        texts.t(
            'ADMIN_PUBLIC_OFFER_ENABLE_BUTTON',
            'Включить показ',
        )
    )
    if offer and offer.is_enabled:
        toggle_text = _label(
            texts.t(
                'ADMIN_PUBLIC_OFFER_DISABLE_BUTTON',
                'Отключить показ',
            )
        )

    buttons.append(
        [
            admin_button(
                toggle_text,
                callback_data='admin_public_offer_toggle',
                icon='lock' if offer and offer.is_enabled else 'success',
            )
        ]
    )

    buttons.append(
        [
            admin_button(
                _label(
                    texts.t(
                        'ADMIN_PUBLIC_OFFER_DISPLAY_MODE_BUTTON',
                        'Отображение: {mode}',
                    ).format(mode=display_mode_label(settings.PUBLIC_OFFER_DISPLAY_MODE, language=db_user.language))
                ),
                callback_data='admin_public_offer_display_mode',
                icon='settings',
                style='primary',
            )
        ]
    )

    buttons.append(
        [
            admin_button(
                _label(
                    texts.t(
                        'ADMIN_PUBLIC_OFFER_HTML_HELP',
                        'HTML помощь',
                    )
                ),
                callback_data='admin_public_offer_help',
                icon='document',
                style='primary',
            )
        ]
    )

    buttons.append([admin_back_button(texts, 'admin_submenu_settings')])

    return (
        admin_screen(overview_text, icon='document', compact=True),
        types.InlineKeyboardMarkup(inline_keyboard=buttons),
        offer,
    )


@admin_required
@error_handler
async def show_public_offer_management(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    overview_text, markup, _ = await _build_overview(db_user, db)

    await callback.message.edit_text(
        overview_text,
        reply_markup=markup,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_public_offer(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    updated_offer = await PublicOfferService.toggle_enabled(db, db_user.language)
    logger.info(
        'Админ %s переключил показ публичной оферты: %s',
        db_user.telegram_id,
        'enabled' if updated_offer.is_enabled else 'disabled',
    )
    status_message = (
        texts.t('ADMIN_PUBLIC_OFFER_ENABLED', 'Оферта включена')
        if updated_offer.is_enabled
        else texts.t('ADMIN_PUBLIC_OFFER_DISABLED', 'Оферта отключена')
    )

    overview_text, markup, _ = await _build_overview(db_user, db)
    await callback.message.edit_text(
        overview_text,
        reply_markup=markup,
        parse_mode='HTML',
    )
    await callback.answer(status_message)


@admin_required
@error_handler
async def cycle_public_offer_display_mode(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    new_mode = await cycle_display_mode_setting(callback, db, 'PUBLIC_OFFER_DISPLAY_MODE', language=db_user.language)
    if new_mode is None:
        return

    overview_text, markup, _ = await _build_overview(db_user, db)
    await callback.message.edit_text(
        overview_text,
        reply_markup=markup,
        parse_mode='HTML',
    )
    await callback.answer(
        texts.t(
            'ADMIN_DISPLAY_MODE_CHANGED',
            'Отображение: {mode}',
        ).format(mode=display_mode_label(new_mode, language=db_user.language))
    )


@admin_required
@error_handler
async def start_edit_public_offer(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    offer = await PublicOfferService.get_offer(
        db,
        db_user.language,
        fallback=False,
    )

    current_preview = ''
    if offer and offer.content:
        preview = offer.content.strip()[:400]
        if len(offer.content.strip()) > 400:
            preview += '...'
        current_preview = (
            admin_quote(
                texts.t(
                    'ADMIN_PUBLIC_OFFER_CURRENT_PREVIEW',
                    'Текущий текст (превью):',
                )
                + f'\n<code>{html.escape(preview)}</code>'
            )
            + '\n\n'
        )

    prompt = texts.t(
        'ADMIN_PUBLIC_OFFER_EDIT_PROMPT',
        'Отправьте новый текст публичной оферты. Допускается HTML-разметка.',
    )

    hint = texts.t(
        'ADMIN_PUBLIC_OFFER_EDIT_HINT',
        'Используйте /html_help для справки по тегам.',
    )

    message_text = (
        admin_screen(
            f'<b>{texts.t("ADMIN_PUBLIC_OFFER_EDIT_TITLE", "Редактирование оферты")}</b>',
            icon='document',
            compact=True,
        )
        + f'\n\n{current_preview}{prompt}\n\n{hint}'
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_button(
                    _label(
                        texts.t(
                            'ADMIN_PUBLIC_OFFER_HTML_HELP',
                            'HTML помощь',
                        )
                    ),
                    callback_data='admin_public_offer_help',
                    icon='document',
                    style='primary',
                )
            ],
            [
                admin_button(
                    _label(texts.t('ADMIN_PUBLIC_OFFER_CANCEL', 'Отмена')),
                    callback_data='admin_public_offer_cancel',
                    style='danger',
                )
            ],
        ]
    )

    await callback.message.edit_text(message_text, reply_markup=keyboard, parse_mode='HTML')
    await state.set_state(AdminStates.editing_public_offer)
    await callback.answer()


@admin_required
@error_handler
async def cancel_edit_public_offer(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    await state.clear()
    overview_text, markup, _ = await _build_overview(db_user, db)
    await callback.message.edit_text(
        overview_text,
        reply_markup=markup,
        parse_mode='HTML',
    )
    await callback.answer(
        get_texts(db_user.language).t(
            'ADMIN_PUBLIC_OFFER_EDIT_CANCELLED',
            'Редактирование оферты отменено.',
        )
    )


@admin_required
@error_handler
async def process_public_offer_edit(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    new_text = message.text or ''

    if len(new_text) > 4000:
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_PUBLIC_OFFER_TOO_LONG',
                    'Текст оферты слишком длинный. Максимум 4000 символов.',
                ),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    is_valid, error_message = validate_html_tags(new_text)
    if not is_valid:
        await message.answer(
            admin_screen(
                texts.t(
                    'ADMIN_PUBLIC_OFFER_HTML_ERROR',
                    'Ошибка в HTML: {error}',
                ).format(error=error_message),
                icon='error',
                compact=True,
            ),
            parse_mode='HTML',
        )
        return

    await PublicOfferService.save_offer(db, db_user.language, new_text)
    logger.info(
        'Админ обновил текст публичной оферты (символов)', telegram_id=db_user.telegram_id, new_text_count=len(new_text)
    )
    await state.clear()

    success_text = admin_screen(
        texts.t(
            'ADMIN_PUBLIC_OFFER_SAVED',
            'Публичная оферта обновлена.',
        ),
        icon='success',
        compact=True,
    )

    reply_markup = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_back_button(
                    texts,
                    'admin_public_offer',
                    label=_label(
                        texts.t(
                            'ADMIN_PUBLIC_OFFER_BACK_BUTTON',
                            'К настройкам оферты',
                        )
                    ),
                )
            ]
        ]
    )

    await message.answer(success_text, reply_markup=reply_markup, parse_mode='HTML')


@admin_required
@error_handler
async def view_public_offer(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    offer = await PublicOfferService.get_offer(
        db,
        db_user.language,
        fallback=False,
    )

    if not offer or not offer.content or not offer.content.strip():
        await callback.answer(
            texts.t(
                'ADMIN_PUBLIC_OFFER_PREVIEW_EMPTY_ALERT',
                'Текст оферты пока не задан.',
            ),
            show_alert=True,
        )
        return

    # Как и на пользовательском экране: сырой HTML с <p> Telegram не разберёт.
    max_length = 3800
    pages = stored_html_to_telegram_pages(offer.content, max_length=max_length)

    if not pages:
        await callback.answer(
            texts.t(
                'ADMIN_PUBLIC_OFFER_PREVIEW_EMPTY_ALERT',
                'Текст оферты пока не задан.',
            ),
            show_alert=True,
        )
        return

    preview = pages[0]
    truncated = len(pages) > 1

    header = texts.t(
        'ADMIN_PUBLIC_OFFER_VIEW_TITLE',
        '<b>Текущий текст оферты</b>',
    )

    note = ''
    if truncated:
        note = texts.t(
            'ADMIN_PUBLIC_OFFER_VIEW_TRUNCATED',
            '\n\nТекст сокращён для отображения. Полную версию увидят пользователи в меню.',
        )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                admin_back_button(
                    texts,
                    'admin_public_offer',
                    label=_label(
                        texts.t(
                            'ADMIN_PUBLIC_OFFER_BACK_BUTTON',
                            'К настройкам оферты',
                        )
                    ),
                )
            ],
            [
                admin_button(
                    _label(
                        texts.t(
                            'ADMIN_PUBLIC_OFFER_EDIT_BUTTON',
                            'Изменить текст',
                        )
                    ),
                    callback_data='admin_public_offer_edit',
                    icon='document',
                    style='primary',
                )
            ],
        ]
    )

    await callback.message.edit_text(
        admin_screen(f'{header}\n\n{preview}{note}'),
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_public_offer_html_help(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    help_text = get_html_help_text()

    current_state = await state.get_state()

    buttons: list[list[types.InlineKeyboardButton]] = []

    if current_state == AdminStates.editing_public_offer.state:
        buttons.append(
            [
                admin_back_button(
                    texts,
                    'admin_public_offer_edit',
                    label=_label(
                        texts.t(
                            'ADMIN_PUBLIC_OFFER_RETURN_TO_EDIT',
                            'Назад к редактированию',
                        )
                    ),
                )
            ]
        )

    buttons.append(
        [
            admin_back_button(
                texts,
                'admin_public_offer',
                label=_label(
                    texts.t(
                        'ADMIN_PUBLIC_OFFER_BACK_BUTTON',
                        'К настройкам оферты',
                    )
                ),
            )
        ]
    )

    await callback.message.edit_text(
        admin_screen(help_text, icon='document'),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )
    await callback.answer()


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        show_public_offer_management,
        F.data == 'admin_public_offer',
    )
    dp.callback_query.register(
        toggle_public_offer,
        F.data == 'admin_public_offer_toggle',
    )
    dp.callback_query.register(
        cycle_public_offer_display_mode,
        F.data == 'admin_public_offer_display_mode',
    )
    dp.callback_query.register(
        start_edit_public_offer,
        F.data == 'admin_public_offer_edit',
    )
    dp.callback_query.register(
        cancel_edit_public_offer,
        F.data == 'admin_public_offer_cancel',
    )
    dp.callback_query.register(
        view_public_offer,
        F.data == 'admin_public_offer_view',
    )
    dp.callback_query.register(
        show_public_offer_html_help,
        F.data == 'admin_public_offer_help',
    )

    dp.message.register(
        process_public_offer_edit,
        AdminStates.editing_public_offer,
    )
