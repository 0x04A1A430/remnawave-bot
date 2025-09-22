import logging
from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_support_keyboard
from app.services.support_settings_service import SupportSettingsService
from app.localization.texts import get_texts
from app.utils.photo_message import edit_or_answer_photo

logger = logging.getLogger(__name__)


async def show_support_info(
    callback: types.CallbackQuery,
    db_user: User
):
    
    texts = get_texts(db_user.language)
    support_info = SupportSettingsService.get_support_info_text(db_user.language)
    await edit_or_answer_photo(
        callback=callback,
        caption=support_info,
        keyboard=get_support_keyboard(db_user.language),
        parse_mode="HTML",
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_support_info,
        F.data == "menu_support"
    )