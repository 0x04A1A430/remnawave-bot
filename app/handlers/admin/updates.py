import html

import structlog
from aiogram import Dispatcher, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.version_service import version_service
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


def get_updates_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=' Проверить обновления', callback_data='admin_updates_check')],
        [InlineKeyboardButton(text=' Информация о версии', callback_data='admin_updates_info')],
        [InlineKeyboardButton(text=' Открыть репозиторий', url=f'https://github.com/{version_service.repo}/releases')],
        [InlineKeyboardButton(text='← Назад', callback_data='admin_panel')],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_version_info_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)
    buttons = [
        [InlineKeyboardButton(text=texts.t('ADMIN_UPDATES_025'), callback_data='admin_updates_info')],
        [InlineKeyboardButton(text=texts.t('ADMIN_UPDATES_026'), callback_data='admin_updates')],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@admin_required
@error_handler
async def show_updates_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(getattr(db_user, 'language', 'ru'))
    try:
        version_info = await version_service.get_version_info()

        current_version = version_info['current_version']
        has_updates = version_info['has_updates']
        total_newer = version_info['total_newer']
        last_check = version_info['last_check']

        status_icon = '' if has_updates else ''
        status_text = (
            texts.t('ADMIN_UPDATES_006').format(value1=total_newer) if has_updates else texts.t('ADMIN_UPDATES_007')
        )

        last_check_text = ''
        if last_check:
            last_check_text = f'\n Последняя проверка: {last_check.strftime("%d.%m.%Y %H:%M")}'

        message = f""" <b>СИСТЕМА ОБНОВЛЕНИЙ</b>

 <b>Текущая версия:</b> <code>{current_version}</code>
{status_icon} <b>Статус:</b> {status_text}

 <b>Репозиторий:</b> {version_service.repo}{last_check_text}

ℹ Система автоматически проверяет обновления каждый час и отправляет уведомления о новых версиях."""

        await callback.message.edit_text(
            message, reply_markup=get_updates_keyboard(db_user.language), parse_mode='HTML'
        )
        await callback.answer()

    except Exception as e:
        if 'message is not modified' in str(e).lower():
            logger.debug(' Сообщение не изменено в show_updates_menu')
            await callback.answer()
            return
        logger.error('Ошибка показа меню обновлений', error=e)
        await callback.answer(texts.t('ADMIN_UPDATES_027'), show_alert=True)


@admin_required
@error_handler
async def check_updates(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(getattr(db_user, 'language', 'ru'))
    await callback.answer(texts.t('ADMIN_UPDATES_009'))

    try:
        has_updates, newer_releases = await version_service.check_for_updates(force=True)

        if not has_updates:
            message = f""" <b>ОБНОВЛЕНИЯ НЕ НАЙДЕНЫ</b>

 <b>Текущая версия:</b> <code>{version_service.current_version}</code>
 <b>Статус:</b> У вас установлена последняя версия

 <b>Репозиторий:</b> {version_service.repo}"""

        else:
            updates_list = []
            for i, release in enumerate(newer_releases[:5]):
                icon = version_service.format_version_display(release).split()[0]
                updates_list.append(f'{i + 1}. {icon} <code>{release.tag_name}</code> • {release.formatted_date}')

            updates_text = '\n'.join(updates_list)
            more_text = f'\n\n И еще {len(newer_releases) - 5} обновлений...' if len(newer_releases) > 5 else ''

            message = f""" <b>НАЙДЕНЫ ОБНОВЛЕНИЯ</b>

 <b>Текущая версия:</b> <code>{version_service.current_version}</code>
 <b>Доступно обновлений:</b> {len(newer_releases)}

 <b>Последние версии:</b>
{updates_text}{more_text}

 <b>Репозиторий:</b> {version_service.repo}"""

        keyboard = get_updates_keyboard(db_user.language)

        if has_updates:
            keyboard.inline_keyboard.insert(
                -2, [InlineKeyboardButton(text=texts.t('ADMIN_UPDATES_032'), callback_data='admin_updates_info')]
            )

        await callback.message.edit_text(message, reply_markup=keyboard, parse_mode='HTML')

    except Exception as e:
        if 'message is not modified' in str(e).lower():
            logger.debug(' Сообщение не изменено в check_updates')
            return
        logger.error('Ошибка проверки обновлений', error=e)
        await callback.message.edit_text(
            texts.t('ADMIN_UPDATES_028').format(value1=version_service.current_version),
            reply_markup=get_updates_keyboard(db_user.language),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def show_version_info(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(getattr(db_user, 'language', 'ru'))
    await callback.answer(texts.t('ADMIN_UPDATES_012'))

    try:
        version_info = await version_service.get_version_info()

        current_version = version_info['current_version']
        current_release = version_info['current_release']
        newer_releases = version_info['newer_releases']
        has_updates = version_info['has_updates']
        last_check = version_info['last_check']

        current_info = texts.t('ADMIN_UPDATES_001')

        if current_release:
            current_info += texts.t('ADMIN_UPDATES_013').format(value1=current_release.tag_name)
            current_info += texts.t('ADMIN_UPDATES_014').format(value1=current_release.formatted_date)
            if _release_description(current_release, texts):
                current_info += texts.t('ADMIN_UPDATES_019').format(value1=_release_description(current_release, texts))
        else:
            current_info += texts.t('ADMIN_UPDATES_013').format(value1=current_version)
            current_info += texts.t('ADMIN_UPDATES_015')

        message_parts = [current_info]

        if has_updates and newer_releases:
            updates_info = texts.t('ADMIN_UPDATES_016')

            for i, release in enumerate(newer_releases):
                icon = '' if i == 0 else ''
                if release.prerelease or release.is_dev:
                    icon = ''

                updates_info += f'{icon} <b>{release.tag_name}</b>\n'
                updates_info += f'    {release.formatted_date}\n'
                if _release_description(release, texts):
                    updates_info += f'    {_release_description(release, texts)}\n'
                updates_info += '\n'

            message_parts.append(updates_info.rstrip())

        system_info = texts.t('ADMIN_UPDATES_002')
        system_info += texts.t('ADMIN_UPDATES_003').format(value1=version_service.repo)
        system_info += texts.t('ADMIN_UPDATES_004').format(
            value1=texts.t('ADMIN_UPDATES_029') if version_service.enabled else texts.t('ADMIN_UPDATES_030')
        )
        system_info += texts.t('ADMIN_UPDATES_005')

        if last_check:
            system_info += f' <b>Последняя проверка:</b> {last_check.strftime("%d.%m.%Y %H:%M")}\n'

        message_parts.append(system_info.rstrip())

        final_message = '\n'.join(message_parts)

        if len(final_message) > 4000:
            final_message = final_message[:3900] + texts.t('ADMIN_UPDATES_020')

        await callback.message.edit_text(
            final_message,
            reply_markup=get_version_info_keyboard(db_user.language),
            parse_mode='HTML',
            disable_web_page_preview=True,
        )

    except Exception as e:
        if 'message is not modified' in str(e).lower():
            logger.debug(' Сообщение не изменено в show_version_info')
            return
        logger.error('Ошибка получения информации о версиях', error=e)
        await callback.message.edit_text(
            texts.t('ADMIN_UPDATES_031').format(value1=version_service.current_version),
            reply_markup=get_version_info_keyboard(db_user.language),
            parse_mode='HTML',
        )


def _release_description(release, texts) -> str:
    # Release notes are external content, not Telegram HTML or a translation key.
    if hasattr(release, 'body') and not release.body:
        return texts.t('ADMIN_UPDATES_NO_DESCRIPTION')
    return html.escape(release.short_description)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_updates_menu, F.data == 'admin_updates')

    dp.callback_query.register(check_updates, F.data == 'admin_updates_check')

    dp.callback_query.register(show_version_info, F.data == 'admin_updates_info')
