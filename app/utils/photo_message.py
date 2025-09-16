from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto

from app.config import settings
from .message_patch import LOGO_PATH, is_qr_message


def _resolve_media(message: types.Message):
    # Всегда используем логотип если включен режим логотипа,
    # кроме специальных случаев (QR сообщения)
    if settings.ENABLE_LOGO_MODE and not is_qr_message(message):
        return FSInputFile(LOGO_PATH)
    # Только если режим логотипа выключен, используем фото из сообщения
    elif message.photo:
        return message.photo[-1].file_id
    return FSInputFile(LOGO_PATH)


async def edit_or_answer_photo(
    callback: types.CallbackQuery,
    caption: str,
    keyboard: types.InlineKeyboardMarkup,
    parse_mode: str | None = "HTML",
) -> None:
    if not settings.ENABLE_LOGO_MODE:
        try:
            if callback.message.photo:
                await callback.message.delete()
                await callback.message.answer(
                    caption,
                    reply_markup=keyboard,
                    parse_mode=parse_mode,
                )
            else:
                await callback.message.edit_text(
                    caption,
                    reply_markup=keyboard,
                    parse_mode=parse_mode,
                )
        except TelegramBadRequest:
            await callback.message.delete()
            await callback.message.answer(
                caption,
                reply_markup=keyboard,
                parse_mode=parse_mode,
            )
        return

    media = _resolve_media(callback.message)
    try:
        await callback.message.edit_media(
            InputMediaPhoto(media=media, caption=caption, parse_mode=parse_mode),
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer_photo(
            FSInputFile(LOGO_PATH),
            caption=caption,
            reply_markup=keyboard,
            parse_mode=parse_mode,
        )
