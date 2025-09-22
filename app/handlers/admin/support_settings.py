import logging
import re
import html
import contextlib
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.utils.decorators import admin_required, error_handler
from app.services.support_settings_service import SupportSettingsService
from app.states import SupportSettingsStates


logger = logging.getLogger(__name__)


def _get_support_settings_keyboard(language: str) -> types.InlineKeyboardMarkup:
    texts = get_texts(language)
    mode = SupportSettingsService.get_system_mode()
    menu_enabled = SupportSettingsService.is_support_menu_enabled()

    rows: list[list[types.InlineKeyboardButton]] = []

    rows.append([
        types.InlineKeyboardButton(
            text=("✅ Пункт 'Техподдержка' в меню" if menu_enabled else "🚫 Пункт 'Техподдержка' в меню"),
            callback_data="admin_support_toggle_menu"
        )
    ])

    rows.append([
        types.InlineKeyboardButton(text=("🔘 Тикеты" if mode == "tickets" else "⚪ Тикеты"), callback_data="admin_support_mode_tickets"),
        types.InlineKeyboardButton(text=("🔘 Контакт" if mode == "contact" else "⚪ Контакт"), callback_data="admin_support_mode_contact"),
        types.InlineKeyboardButton(text=("🔘 Оба" if mode == "both" else "⚪ Оба"), callback_data="admin_support_mode_both"),
    ])

    rows.append([
        types.InlineKeyboardButton(text="📝 Изменить описание", callback_data="admin_support_edit_desc")
    ])

    rows.append([
        types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_submenu_communications")
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@admin_required
@error_handler
async def show_support_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    desc = SupportSettingsService.get_support_info_text(db_user.language)
    await callback.message.edit_text(
        "🛟 <b>Настройки поддержки</b>\n\n" +
        "Режим работы и видимость в меню. Ниже текущее описание меню поддержки:\n\n" +
        desc,
        reply_markup=_get_support_settings_keyboard(db_user.language),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_support_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    current = SupportSettingsService.is_support_menu_enabled()
    SupportSettingsService.set_support_menu_enabled(not current)
    await show_support_settings(callback, db_user, db)


@admin_required
@error_handler
async def set_mode_tickets(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    SupportSettingsService.set_system_mode("tickets")
    await show_support_settings(callback, db_user, db)


@admin_required
@error_handler
async def set_mode_contact(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    SupportSettingsService.set_system_mode("contact")
    await show_support_settings(callback, db_user, db)


@admin_required
@error_handler
async def set_mode_both(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    SupportSettingsService.set_system_mode("both")
    await show_support_settings(callback, db_user, db)


@admin_required
@error_handler
async def start_edit_desc(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    texts = get_texts(db_user.language)
    current_desc_html = SupportSettingsService.get_support_info_text(db_user.language)
    # plain text for display-only code block
    current_desc_plain = re.sub(r"<[^>]+>", "", current_desc_html)

    kb_rows: list[list[types.InlineKeyboardButton]] = []
    kb_rows.append([
        types.InlineKeyboardButton(text="📨 Прислать текст", callback_data="admin_support_send_desc")
    ])
    # Подготовим блок контакта (отдельным инлайном)
    from app.config import settings
    support_contact_display = settings.get_support_contact_display()
    kb_rows.append([
        types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_support_settings")
    ])

    text_parts = [
        "📝 <b>Редактирование описания поддержки</b>",
        "",
        "Текущее описание:",
        "",
        f"<code>{html.escape(current_desc_plain)}</code>",
    ]
    if support_contact_display:
        text_parts += [
            "",
            "<b>Контакт для режима \u00abКонтакт\u00bb</b>",
            f"<code>{html.escape(support_contact_display)}</code>",
            "",
            "Добавьте в описание при необходимости.",
        ]
    await callback.message.edit_text(
        "\n".join(text_parts),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML"
    )
    await state.set_state(SupportSettingsStates.waiting_for_desc)
    await callback.answer()


@admin_required
@error_handler
async def handle_new_desc(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    new_text = message.html_text or message.text
    SupportSettingsService.set_support_info_text(db_user.language, new_text)
    await state.clear()
    markup = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="🗑 Удалить", callback_data="admin_support_delete_msg")]]
    )
    await message.answer("✅ Описание обновлено.", reply_markup=markup)


@admin_required
@error_handler
async def send_desc_copy(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    # send plain text for easy copying
    current_desc_html = SupportSettingsService.get_support_info_text(db_user.language)
    current_desc_plain = re.sub(r"<[^>]+>", "", current_desc_html)
    # attach delete button to the sent message
    markup = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="🗑 Удалить", callback_data="admin_support_delete_msg")]]
    )
    if len(current_desc_plain) <= 4000:
        await callback.message.answer(current_desc_plain, reply_markup=markup)
    else:
        # split long messages (attach delete only to the last chunk)
        chunk = 0
        while chunk < len(current_desc_plain):
            next_chunk = current_desc_plain[chunk:chunk+4000]
            is_last = (chunk + 4000) >= len(current_desc_plain)
            await callback.message.answer(next_chunk, reply_markup=(markup if is_last else None))
            chunk += 4000
    await callback.answer("Текст отправлен ниже")


@admin_required
@error_handler
async def delete_sent_message(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        await callback.message.delete()
    finally:
        with contextlib.suppress(Exception):
            await callback.answer("Сообщение удалено")


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_support_settings, F.data == "admin_support_settings")
    dp.callback_query.register(toggle_support_menu, F.data == "admin_support_toggle_menu")
    dp.callback_query.register(set_mode_tickets, F.data == "admin_support_mode_tickets")
    dp.callback_query.register(set_mode_contact, F.data == "admin_support_mode_contact")
    dp.callback_query.register(set_mode_both, F.data == "admin_support_mode_both")
    dp.callback_query.register(start_edit_desc, F.data == "admin_support_edit_desc")
    dp.callback_query.register(send_desc_copy, F.data == "admin_support_send_desc")
    dp.callback_query.register(delete_sent_message, F.data == "admin_support_delete_msg")
    dp.message.register(handle_new_desc, SupportSettingsStates.waiting_for_desc)


