from pathlib import Path
from aiogram.types import Message, FSInputFile, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest

from app.config import settings

LOGO_PATH = Path(settings.LOGO_FILE)


def is_qr_message(message: Message) -> bool:
    return bool(message.caption and message.caption.startswith("\U0001F517 Ваша реферальная ссылка"))


_original_answer = Message.answer
_original_edit_text = Message.edit_text


async def _answer_with_photo(self: Message, text: str = None, **kwargs):
    # Уважаем флаг в рантайме: если логотип выключен — не подменяем ответ
    if not settings.ENABLE_LOGO_MODE:
        return await _original_answer(self, text, **kwargs)
    # Если caption слишком длинный для фото — отправим как текст
    try:
        if text is not None and len(text) > 900:
            return await _original_answer(self, text, **kwargs)
    except Exception:
        pass
    if LOGO_PATH.exists():
        try:
            return await self.answer_photo(FSInputFile(LOGO_PATH), caption=text, **kwargs)
        except Exception:
            # Фоллбек, если Telegram ругается на caption: отправим как текст
            return await _original_answer(self, text, **kwargs)
    return await _original_answer(self, text, **kwargs)


async def _edit_with_photo(self: Message, text: str, **kwargs):
    # Уважаем флаг в рантайме: если логотип выключен — не подменяем редактирование
    if not settings.ENABLE_LOGO_MODE:
        return await _original_edit_text(self, text, **kwargs)
    if self.photo:
        # Если caption потенциально слишком длинный — отправим как текст вместо caption
        try:
            if text is not None and len(text) > 900:
                try:
                    await self.delete()
                except Exception:
                    pass
                return await _original_answer(self, text, **kwargs)
        except Exception:
            pass
        # Всегда используем логотип если включен режим логотипа,
        # кроме специальных случаев (QR сообщения)
        if settings.ENABLE_LOGO_MODE and LOGO_PATH.exists() and not is_qr_message(self):
            media = FSInputFile(LOGO_PATH)
        elif is_qr_message(self) and LOGO_PATH.exists():
            media = FSInputFile(LOGO_PATH)
        else:
            media = self.photo[-1].file_id
        media_kwargs = {"media": media, "caption": text}
        if "parse_mode" in kwargs:
            _pm = kwargs.pop("parse_mode")
            media_kwargs["parse_mode"] = _pm if _pm is not None else "HTML"
        else:
            media_kwargs["parse_mode"] = "HTML"
        try:
            return await self.edit_media(InputMediaPhoto(**media_kwargs), **kwargs)
        except TelegramBadRequest:
            # Фоллбек: удалим и отправим обычный текст без фото
            try:
                await self.delete()
            except Exception:
                pass
            return await _original_answer(self, text, **kwargs)
    return await _original_edit_text(self, text, **kwargs)


def patch_message_methods():
    if not settings.ENABLE_LOGO_MODE:
        return
    Message.answer = _answer_with_photo
    Message.edit_text = _edit_with_photo

