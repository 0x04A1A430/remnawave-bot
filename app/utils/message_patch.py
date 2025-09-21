from pathlib import Path
from aiogram.types import Message, FSInputFile, InputMediaPhoto

from app.config import settings

LOGO_PATH = Path(settings.LOGO_FILE)


def is_qr_message(message: Message) -> bool:
    return bool(message.caption and message.caption.startswith("\U0001F517 Ваша реферальная ссылка"))


_original_answer = Message.answer
_original_edit_text = Message.edit_text


async def _answer_with_photo(self: Message, text: str = None, **kwargs):
    if LOGO_PATH.exists():
        return await self.answer_photo(FSInputFile(LOGO_PATH), caption=text, **kwargs)
    return await _original_answer(self, text, **kwargs)


async def _edit_with_photo(
    self: Message,
    text: str,
    parse_mode: str | None = None,
    entities=None,
    disable_web_page_preview: bool | None = None,
    reply_markup=None,
    message_effect_id=None,
    **kwargs,
):
    if self.photo:
        # Всегда используем логотип если включен режим логотипа,
        # кроме специальных случаев (QR сообщения)
        if settings.ENABLE_LOGO_MODE and LOGO_PATH.exists() and not is_qr_message(self):
            media = FSInputFile(LOGO_PATH)
        elif is_qr_message(self) and LOGO_PATH.exists():
            media = FSInputFile(LOGO_PATH)
        else:
            media = self.photo[-1].file_id
        media_kwargs = {"media": media, "caption": text}
        if parse_mode:
            media_kwargs["parse_mode"] = parse_mode
        if entities:
            media_kwargs["caption_entities"] = entities

        edit_kwargs = {**kwargs}
        if reply_markup is not None:
            edit_kwargs["reply_markup"] = reply_markup
        if message_effect_id is not None:
            edit_kwargs["message_effect_id"] = message_effect_id

        return await self.edit_media(InputMediaPhoto(**media_kwargs), **edit_kwargs)
    return await _original_edit_text(
        self,
        text,
        parse_mode=parse_mode,
        entities=entities,
        disable_web_page_preview=disable_web_page_preview,
        reply_markup=reply_markup,
        message_effect_id=message_effect_id,
        **kwargs,
    )


def patch_message_methods():
    if not settings.ENABLE_LOGO_MODE:
        return
    Message.answer = _answer_with_photo
    Message.edit_text = _edit_with_photo

