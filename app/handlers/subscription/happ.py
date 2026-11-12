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
        '<tg-emoji emoji-id="5877307202888273539">📥</tg-emoji> <b>Скачать Happ</b>\nВыберите ваше устройство:',
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
            texts.t(
                'HAPP_DOWNLOAD_LINK_NOT_SET',
                '<tg-emoji emoji-id="5778527486270770928">❌</tg-emoji> Ссылка для этого устройства не настроена',
            ),
            show_alert=True,
        )
        return

    platform_names = {
        'ios': texts.t('HAPP_PLATFORM_IOS', '<tg-emoji emoji-id="5935989710420709120">🍎</tg-emoji> iOS'),
        'android': texts.t('HAPP_PLATFORM_ANDROID', '<tg-emoji emoji-id="5931415565955503486">🤖</tg-emoji> Android'),
        'macos': texts.t('HAPP_PLATFORM_MACOS', 'Mac OS'),
        'windows': texts.t('HAPP_PLATFORM_WINDOWS', '<tg-emoji emoji-id="5877318502947229960">💻</tg-emoji> Windows'),
    }

    link_text = texts.t(
        'HAPP_DOWNLOAD_LINK_MESSAGE',
        '<tg-emoji emoji-id="5886451926995833684">⬇️</tg-emoji> Скачайте Happ для {platform}:',
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
        '<tg-emoji emoji-id="5877307202888273539">📥</tg-emoji> <b>Скачать Happ</b>\nВыберите ваше устройство:',
    )

    keyboard = get_happ_download_platform_keyboard(db_user.language)

    await callback.message.edit_text(prompt_text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()
