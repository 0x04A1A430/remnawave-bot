from pathlib import Path
from aiogram.types import Message, FSInputFile, InputMediaPhoto

LOGO_PATH = Path("vpn_logo.png")


def _is_qr_message(message: Message) -> bool:
    return bool(message.caption and message.caption.startswith("\U0001F517 Ваша реферальная ссылка"))


_original_answer = Message.answer
_original_edit_text = Message.edit_text


async def _answer_with_photo(self: Message, text: str = None, **kwargs):
    if LOGO_PATH.exists():
        return await self.answer_photo(FSInputFile(LOGO_PATH), caption=text, **kwargs)
    return await _original_answer(self, text, **kwargs)


async def _edit_with_photo(self: Message, text: str, **kwargs):
    if self.photo:
        media = self.photo[-1].file_id
        if _is_qr_message(self) and LOGO_PATH.exists():
            media = FSInputFile(LOGO_PATH)
        media_kwargs = {"media": media, "caption": text}
        if "parse_mode" in kwargs:
            media_kwargs["parse_mode"] = kwargs.pop("parse_mode")
        return await self.edit_media(InputMediaPhoto(**media_kwargs), **kwargs)
    return await _original_edit_text(self, text, **kwargs)


def patch_message_methods():
    Message.answer = _answer_with_photo
    Message.edit_text = _edit_with_photo

