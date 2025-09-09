from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto

from .message_patch import LOGO_PATH, is_qr_message


def _resolve_media(message: types.Message):
    if message.photo and not is_qr_message(message):
        return message.photo[-1].file_id
    return FSInputFile(LOGO_PATH)


async def edit_or_answer_photo(
    callback: types.CallbackQuery,
    caption: str,
    keyboard: types.InlineKeyboardMarkup,
    parse_mode: str | None = "HTML",
) -> None:
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
