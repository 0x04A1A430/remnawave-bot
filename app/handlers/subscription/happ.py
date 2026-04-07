from aiogram import types
from aiogram.types import InaccessibleMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_happ_download_link_keyboard, get_happ_download_platform_keyboard
from app.localization.texts import get_texts


async def handle_happ_download_request(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    prompt_text = texts.t(
        'HAPP_DOWNLOAD_PROMPT',
        '<b>Скачать Happ</b>\nВыберите ваше устройство:',
    )

    keyboard = get_happ_download_platform_keyboard(db_user.language)

    await callback.message.answer(prompt_text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()


async def handle_happ_download_platform_choice(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    # Проверяем, доступно ли сообщение для редактирования
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return

    platform = callback.data.split('_')[-1]
    if platform == 'pc':
        platform = 'windows'
    texts = get_texts(db_user.language)
    link = settings.get_happ_download_link(platform)

    if not link:
        await callback.answer(
            texts.t('HAPP_DOWNLOAD_LINK_NOT_SET', 'Ссылка для этого устройства не настроена'),
            show_alert=True,
        )
        return

    platform_names = {
        'ios': texts.t('HAPP_PLATFORM_IOS', 'iOS'),
        'android': texts.t('HAPP_PLATFORM_ANDROID', 'Android'),
        'macos': texts.t('HAPP_PLATFORM_MACOS', '️ Mac OS'),
        'windows': texts.t('HAPP_PLATFORM_WINDOWS', 'Windows'),
    }

    link_text = texts.t(
        'HAPP_DOWNLOAD_LINK_MESSAGE',
        '↓ Скачайте Happ для {platform}:',
    ).format(platform=platform_names.get(platform, platform.upper()))

    keyboard = get_happ_download_link_keyboard(db_user.language, link)

    await callback.message.edit_text(link_text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()


async def handle_happ_download_close(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.answer()


async def handle_happ_download_back(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    # Проверяем, доступно ли сообщение для редактирования
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return

    texts = get_texts(db_user.language)
    prompt_text = texts.t(
        'HAPP_DOWNLOAD_PROMPT',
        '<b>Скачать Happ</b>\nВыберите ваше устройство:',
    )

    keyboard = get_happ_download_platform_keyboard(db_user.language)

    await callback.message.edit_text(prompt_text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()
